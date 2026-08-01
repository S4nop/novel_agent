# Phase 0 Memo — Korean Web-Novel AI Agent: Platform, Economics & Go/No-Go

*Prepared 2026-07-19. Sources cited inline. Low-confidence items flagged explicitly. Currency: ~₩1,400/USD.*

> **⚠️ Post-fact-check corrections (read first):**
> - **ep-25 conversion wall is a practitioner heuristic, not platform-fixed.** The only platform-documented Novelpia paywall is **ep 16+ (PLUS subscriber-only; first 15 free)**.
> - **Novelpia per-view rate is stale here:** reportedly cut in Oct to **비독점 4원 / 독점 8원** (from 6/12), so break-even ≈ **~375 views/episode**, not ~250.
> - **PLUS→author ~90% split is UNCONFIRMED** (no source; Novelpia settles per-view, not a clean %).
> - **12.5%-negative** figure is from the 2025 웹툰/만화 백서, not a web-novel-specific survey; the 피아조아 disclosure example was not verifiable.
> - **AI 기본법 nuance:** its 사업자 labeling scope can include an individual running a *commercialized* AI-content operation — a legal gray zone, not a clean exemption (≤₩30M 과태료, 1-yr enforcement grace).
> - **Single most decisive uncertainty:** whether Novelpia's PLUS monetization gate de-facto rejects AI work — probe before committing to the platform.

---

## 1. Platform & AI-Policy Landscape

**Where a solo builder can actually publish AI-assisted work today:**

| Platform | Open solo publish? | AI in ordinary serialization | AI in contests |
|---|---|---|---|
| **Novelpia** | ✅ 자유연재, no contract; adult OK w/ age verify | **Permissive** — AI-written works openly hosted; AI as 보조작가 effectively encouraged; no disclosure rule | Banned (text + illustration) [typetak](https://www.typetak.com/ko/blog/2025_h2_novelpia_contest) |
| **Munpia** | ✅ 자유연재, one-time 본인인증, no contract | **No AI ban, no disclosure rule** in binding TOS (v2.8) — AI use self-declared, undetectable [mm.munpia.com](https://mm.munpia.com/?menu=join_terms) | **Hard ban** (text + illustration); exclusion/award revocation [nhelp.munpia.com/notices/1780](https://nhelp.munpia.com/notices/1780) |
| **Naver 웹소설** | ✅ 챌린지리그 open board | No AI-specific rule | Webtoon arm restricted AI post-2023 |
| **Naver Series** | ❌ storefront, fed by CP/promotion | n/a | n/a |
| **KakaoPage** | ❌ 스테이지 shut 2024-12-20; now curated 투고/contest/CP-facing 루키제도 [Newsis](https://www.newsis.com/view/NISX20241202_0002979942) | No AI rule | — |
| **Ridibooks** | ❌ store; curated 투고 only | — | — |

**Recommendation: Novelpia is the home base** — most AI-permissive, genuinely open, and has a per-view payout that pays even non-exclusive self-pub work. Munpia is a viable secondary free board but **do not enter Munpia contests** with AI output.

**Disclosure/sanction reality (the important part):**
- **No Korean law forces individual authors to disclose AI.** The AI 기본법 (in force 2026-01-22) puts labeling duty on AI *service providers*, not tool-using creators [law.go.kr](https://www.law.go.kr/lsInfoP.do?lsiSeq=268543), [Fortune Korea](https://www.fortunekorea.co.kr/news/articleView.html?idxno=51771).
- **No platform mandates AI disclosure** outside contests. Text AI is "nearly impossible" to detect [Money Today](https://www.mt.co.kr/tech/2026/03/11/2026022508052980108).
- **Real enforcement is reader-driven, not platform-driven.** Exposed AI (leaked prompts, AI "tells") → 별점 테러 (1-star bombing) → voluntary halt. Documented *platform* takedowns were **plagiarism/AI-translation** cases (e.g. Munpia's '전국시대…' = ChatGPT translation of Chinese '秦功'), not sanctions for AI authorship per se [Money Today](https://www.mt.co.kr/tech/2026/03/11/2026022508052980108).
- **Sentiment is mixed, not monolithic:** KOCCA 2025 백서 — only **12.5% of users negative**; honest voluntary disclosure (피아조아, June 2025) drew sympathy, not attacks [Money Today](https://www.mt.co.kr/tech/2026/03/12/2026031123072512467). The reader test is **concealment + quality**, not AI use itself.
- ⚠️ **[LOW CONFIDENCE]** Munpia banning AI *cover images* is forum consensus only (arca/dcinside, 403-blocked); no official page confirmed. Novelpia's AI-cover ban is confirmed for contests.
- ⚠️ **[MED CONFIDENCE]** Novelpia reportedly polices AI harder at the Plus monetization gate (subscription economics) — a surge of AI Plus applications is being rejected for logic/consistency errors [arca.live](https://arca.live/b/webfiction/176759106).

---

## 2. Monetization & Where the Retention Gate Sits

There are **two distinct gates** — do not conflate them:

- **Gate 1 — Retention/hook (eps 1–3):** heavy normal churn; 연독률 is measured *from ep 4* (eps 1–3 excluded). Even top works shed 20–25%; ordinary works 30–50% [arca.live](https://arca.live/b/webfiction/22155460).
- **Gate 2 — Paid conversion (~ep 25):** the real commercial wall, ≈ 단행본 1권. Conversion happens in the **25–50화** window *after* the work charts (투베) and 연독률 checks out. **~15화 is only the early charting signal**, not the pay wall [dcinside](https://gall.dcinside.com/mgallery/board/view/?id=aiwriter&no=1440). This is craft consensus, **not** a platform-mandated number.

**Monetization models differ by platform:**
- **Munpia:** per-episode 편당 100원 (not 기다무 — that's a Kakao mechanic). Path: 자유연재 → 일반연재 → paid via management contract, direct 유료화, or DIY '나 혼자 유료화' (30화+ & 선작 1,000+, since 2023 H2). ⚠️[MED] thresholds are community-sourced.
- **Novelpia:** subscription (PLUS 정액제) + **per-view settlement** — the key differentiator. First 15 eps free, ep 16+ subscriber-only. Reported per-view: **비독점 6원 / 독점 12원** (later reported cut to 4원/8원). ⚠️[MED] current rate not confirmed on official page; the PLUS→author distribution % (~90% claimed) is **UNCONFIRMED**.
- **기다무** (Kakao 2014; Naver 매열무; RIDI 리다무): wait 12–24h for one free ticket or pay ~100원. Not a Novelpia/Munpia native mechanic.

**Retention gate for the agent = ~ep 25 conversion, gated on 연독률.** Design the product to survive to and clear ep 25, not just to nail eps 1–3.

---

## 3. Production Norms

- **Length:** ~**5,000–5,500자 공백포함** (spaces included) per paid 화 — anchored to ~100원 pricing, not just craft [brunch](https://brunch.co.kr/@qsza45/11). Naver runs higher (~6,000자+); some BL/free-board practice measures 4,000–4,500자 *spaces-excluded*. **Make length configurable per platform.**
- **Cadence:** daily (주7회) or 주5–6회 expected. Upload frequency feeds ranking directly — Novelpia real-time ranking keys off the **last-24h chapter's** view count [Novelpia FAQ](https://novelpia.com/faq/all/view_2977790/). A ~10–20 episode **비축분 (stockpile)** before launch is treated as mandatory [brunch](https://brunch.co.kr/@scrawl/155).
- **연중 (abandonment):** structurally punishing. Stopping at 15–30 eps lands in the exact review/monetization window → reads as a **failed launch**, no path to 유료화/독점. Algorithms deprioritize fast (falls off real-time ranking, ages out of 14-day new-release window). Readers penalize with 별점 테러. Legitimate 휴재 exists on paper (문체부 2025-03 표준계약서 휴재권) but only via mutual agreement + public notice — **not silent stalling**.

**Implication:** the market's single harshest failure mode is stalling in the first 30 episodes. An AI agent's core advantage — unbroken daily 5,000–5,500자 output with an enforced buffer — directly attacks this.

---

## 4. Economics — Revenue vs. Per-Episode Claude Cost

**Realistic revenue (authoritative: 문체부·KPIPA 2024 실태조사, 800 creators):**
- Mean author income ₩19.53M/yr for 2023; **no median published** — typical author earns far less given extreme skew [Sedaily](https://www.sedaily.com/NewsView/2GRJLPFP4W).
- **70.8% earn <₩5M per work**; only **~1% earn ₩100M+ per work** [Nocutnews](https://www.nocutnews.co.kr/news/6325522).
- **A no-name serial on a free board earns ~₩0 directly.** Free 자유연재 is a *funnel*, not a revenue source. Even a mid-tier Munpia paid conversion (~rank 30) grossed only ~₩4M over 200 episodes [tanma.kr](https://tanma.kr/data/munpia_start.html).
- Layered take: platform 30% base (~45–50% w/ promotions/app-store) → CP → author (CP–author 7:3 typical). Author nets **~35–50% of gross reader spend**.

**Per-episode Claude cost model** (⚠️ token counts are estimates — Korean text on the Opus 4.8 / Sonnet 5 *newer tokenizer* runs ~30% more tokens; **count per-model before committing**):

Assumptions per 5,500자 episode: output ≈ **10,000 tokens**; input context (story bible, prior-chapter summaries, style) ≈ 20,000 tokens, largely cacheable. "Realistic pipeline" = ~3 passes (outline + draft + revise) with ~30k input/episode, cache reads.

| Model | Rate (in/out $/M) | Single-pass est. | 3-pass pipeline est. |
|---|---|---|---|
| **Opus 4.8** | $5 / $25 | ~$0.26 (₩360) | ~$0.80–1.50 (₩1,100–2,100) |
| **Sonnet 5** (intro→Aug 31) | $2 / $10 | ~$0.12 (₩170) | ~$0.35–0.60 (₩490–840) |
| **Haiku 4.5** | $1 / $5 | ~$0.06 (₩85) | ~$0.18–0.30 (₩250–420) |

Batch API = flat **50% off** both directions and **stacks with prompt caching**; cache read = 0.1× input. All verified against [platform.claude.com/docs/pricing](https://platform.claude.com/docs/en/about-claude/pricing).

**Break-even math (the decision-driver):**
- Free board: revenue ≈ ₩0. **Every episode is a pure loss** until monetized.
- Novelpia non-exclusive at 6원/view: an Opus 3-pass episode (~₩1,500) needs **~250 lifetime views/episode** just to cover generation. Sonnet cuts that to ~80–140 views; Haiku to ~40–70.
- **Cost is not the constraint — distribution is.** Even Opus is cheap (~₩1,500/episode) relative to a single charting work. The binding constraint is getting *any* traffic, which the market gives to almost no one.

**Model strategy:** draft on **Sonnet 5** (intro $2/$10 through Aug 31 2026), reserve **Opus 4.8** for outline/revision passes, use **Batch + caching** for the large cacheable story-bible context. This roughly halves the per-episode cost vs. all-Opus.

---

## 5. Prior-Claim Verification

| # | Prior claim | Verdict | Evidence |
|---|---|---|---|
| 1 | Real gate is free→paid conversion (기다무), wall ~ep 15–25 | **REFINED** | Wall is **~ep 25** (=1권), range 25–50; **15화 = charting signal, not the wall**. 기다무 is a Kakao mechanic — Munpia uses 편당 100원, Novelpia uses subscription+per-view [dcinside](https://gall.dcinside.com/mgallery/board/view/?id=aiwriter&no=1440) |
| 2 | 15–30 ep then stop = 연중; incompatible w/ norms | **CONFIRMED** | Lands in review/monetization window; algorithmic + reader penalty [Novelpia FAQ](https://novelpia.com/faq/all/view_2977790/) |
| 3 | Length ~5,000–5,500자 공백포함 | **CONFIRMED** (refine) | Market norm; Naver ~6,000자+, some BL 4,000–4,500 공백미포 [brunch](https://brunch.co.kr/@qsza45/11) |
| 4 | Naver Series/KakaoPage CP-gated; Munpia/Novelpia solo free boards | **CONFIRMED** (refine) | Also Naver **웹소설 챌린지리그** is open self-pub; it's Naver **Series** (store) that's gated [namu](https://namu.wiki/w/%EB%84%A4%EC%9D%B4%EB%B2%84%20%EC%8B%9C%EB%A6%AC%EC%A6%88) |
| 5 | LINE is not a Korean 웹소설 platform | **CONFIRMED** | LINE Novel = Japanese, closed 2020-08-31; LINE Manga = Japanese webtoon [ANN](https://www.animenewsnetwork.com/interest/2020-08-31/line-novel-app-ends-service-on-august-31/.163482) |
| 6 | Readers 별점-terror AI works; platforms moving to disclosure/bans | **REFINED** | Backlash real but **concealment-driven**; bans are **contest-only**; **no general disclosure mandate**; only 12.5% users negative [Money Today](https://www.mt.co.kr/tech/2026/03/11/2026022508052980108), [KOCCA via MT](https://www.mt.co.kr/tech/2026/03/12/2026031123072512467) |
| 7 | No platform exposes per-episode drop-off funnel to solo self-pub | **CONFIRMED** (refine) | True per-reader/session funnel unavailable; but **per-episode cumulative views are public & scrapeable** — 연독률 is a computed proxy (existing Chrome extensions do this) [nhelp](https://nhelp.munpia.com/notices/1730) |
| 8 | KakaoPage 스테이지 shut ~Dec 2024 | **CONFIRMED** | Exact date **2024-12-20** [Newsis](https://www.newsis.com/view/NISX20241202_0002979942) |

---

## 6. Phase 0 Go/No-Go Implications

**GO signals:**
- **No legal or platform barrier** to publishing AI-assisted work on Novelpia (home base) or Munpia general serialization. No disclosure mandate.
- **Generation cost is trivial** (~₩250–2,100/episode) relative to any commercial outcome. The agent's daily-unbroken-output capability directly counters the market's #1 failure mode (연중 in first 30 eps).
- **Analytics loop is feasible:** scrape public per-episode view counts → compute 연독률 proxy → iterate. Existing tools prove this works.

**NO-GO / caution signals:**
- **Revenue for a no-name serial is ~₩0 on free boards** and single-digit millions of won even for mid-tier paid conversions. This is a **winner-take-most** market; a business case cannot rely on typical-author revenue.
- **Reputational risk from concealed AI** is real — design for **honesty + quality**, not hiding. Keep out of Munpia/Novelpia contests entirely.
- The **retention gate is craft (연독률 to ep 25)**, and the agent must clear it — cheap output alone doesn't earn traffic.

**Recommended Phase 0 posture:** Build on **Novelpia** (permissive + per-view payout). Optimize the pipeline to survive to ep 25 with a 10–20 episode buffer, Sonnet-5-primary generation with Opus revision, and a scraped-연독률 feedback loop. Treat revenue as a distribution problem, not a cost problem.

**Biggest remaining uncertainty:** **Whether an AI serial can actually clear Gate 2 (ep-25 conversion via 연독률) at scale** — i.e., whether AI output can hold readers past the free window. Everything downstream (revenue, per-view economics, Plus approval) hinges on retention we cannot verify from research; it must be tested empirically with a small live cohort. Secondary unknowns (all flagged above): Novelpia's current per-view rate and PLUS distribution %, and Novelpia's de facto AI scrutiny at the Plus gate.