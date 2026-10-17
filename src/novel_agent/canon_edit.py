"""Author edits to the canon that have to reach beyond canon.json.

Renaming a character is the case that matters. In a live store one supporting
character's name appeared in nine files — canon, its reset snapshot, the
premise, the arc plan, the voice bible, the 떡밥 ledger, the summary and every
committed episode. Changing only the canon key would leave the planner reading
the old name from every other one of them.

The rule (coworker feedback 2, delegated):
- before any episode is committed: the name changes everywhere
- once episodes exist: their prose is published and is not rewritten, so every
  forward-looking artifact changes; if that prose actually names the character,
  the old name is kept as an alias so canon still recognises them there
- while an episode awaits approval: refused — it was written with the old name
  and is about to become canon
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .canon_store import CanonStore

# Every setup and ledger file a name can live in. Episode prose is deliberately
# absent: committed episodes are published text.
_FILES = ("canon.json", "canon.authored.json", "north_star.json", "arc_map.json",
          "voice_bible.json", "foreshadow.json", "summary.json", "genre_profile.json")


class RenameError(Exception):
    """A rename the rules refuse. The message is for the author, in Korean."""


@dataclass
class RenameReport:
    old: str
    new: str
    committed_episodes: int
    changes: list[tuple[str, int]] = field(default_factory=list)
    alias_kept: bool = False
    warning: str = ""


def _pattern(name: str) -> re.Pattern:
    # Not preceded by another syllable or letter, so renaming 현우 leaves 이현우
    # alone. Anything may follow — Korean particles attach directly (현우가).
    return re.compile(r"(?<![가-힣A-Za-z0-9])" + re.escape(name))


def _replace(node, pat: re.Pattern, new: str, counter: list[int]):
    if isinstance(node, str):
        out, n = pat.subn(new, node)
        counter[0] += n
        return out
    if isinstance(node, list):
        return [_replace(x, pat, new, counter) for x in node]
    if isinstance(node, dict):
        # keys too: canon.characters and relationships are keyed by name
        return {_replace(k, pat, new, counter): _replace(v, pat, new, counter)
                for k, v in node.items()}
    return node


def rename_character(store: CanonStore, old: str, new: str, *,
                     dry_run: bool = False) -> RenameReport:
    new = (new or "").strip()
    canon = store.load_canon()
    if old not in canon.characters:
        raise RenameError(f"캐논에 '{old}'라는 인물이 없습니다")
    if not new:
        raise RenameError("새 이름이 비어 있습니다")
    if new == old:
        raise RenameError("이름이 바뀌지 않았습니다")
    for name, card in canon.characters.items():
        if name == new:
            raise RenameError(f"'{new}'은(는) 이미 다른 인물의 이름입니다")
        if name != old and new in card.aliases:
            raise RenameError(f"'{new}'은(는) 이미 {name}의 별칭입니다")
    if store.pending_episode() is not None:
        raise RenameError("승인 대기 중인 화가 있습니다. 그 원고는 옛 이름으로 쓰였으므로 "
                          "먼저 승인하거나 반려한 뒤 이름을 바꾸세요")

    committed = store.latest_episode_number()
    report = RenameReport(old=old, new=new, committed_episodes=committed)
    pat = _pattern(old)
    rewritten: dict[str, dict] = {}
    for fname in _FILES:
        path = store.root / fname
        if not path.exists():
            continue
        counter = [0]
        data = _replace(json.loads(path.read_text(encoding="utf-8")), pat, new, counter)
        if counter[0]:
            report.changes.append((fname, counter[0]))
            rewritten[fname] = data

    # Only prose that actually NAMES them is a reason to keep the old name — a
    # character added after 1화 has no published text to stay consistent with.
    used_in = appeared_in(store, [old])
    if used_in:
        report.alias_kept = True
        span = (f"{used_in[0]}화" if len(used_in) == 1
                else f"{', '.join(map(str, used_in))}화")
        report.warning = (f"이미 커밋된 {span} 본문에는 '{old}'이(가) 그대로 남습니다. "
                          f"캐논이 그 인물을 계속 알아보도록 '{old}'을(를) 별칭으로 남겼습니다.")
    if dry_run:
        return report

    canon_data = rewritten.get("canon.json") or json.loads(
        (store.root / "canon.json").read_text(encoding="utf-8"))
    card = canon_data["characters"][new]
    if report.alias_kept and old not in card["aliases"]:
        card["aliases"].append(old)
    canon_data["version"] = canon_data.get("version", 0) + 1
    canon_data["last_modified_by"] = "author"
    rewritten["canon.json"] = canon_data

    for fname, data in rewritten.items():
        (store.root / fname).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    store.load_canon()          # validates what was written
    return report


def appeared_in(store: CanonStore, names: list[str]) -> list[int]:
    """Committed episodes whose prose names any of these — a character in them
    cannot be deleted without canon contradicting published text."""
    pats = [_pattern(n) for n in names if n]
    return [n for n in range(1, store.latest_episode_number() + 1)
            if (ep := store.load_episode(n)) and any(p.search(ep.prose) for p in pats)]
