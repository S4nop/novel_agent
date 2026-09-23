[작품]
${premise}
엔진: ${episode_engine}
규칙: ${hard_rules}

[장르]
${sub_genre} · 사이다 주기 ${catharsis_cadence}화 이내 · 최대 연속 고구마 ${max_frustration}회
금지: ${forbidden}

[현재 아크]
${arc_line}

[중심 반전] — 주요 떡밥은 결국 여기로 모입니다
${central_twist}

[등장 가능 인물]
${cast}

[지금까지]
${story_so_far}

[페이싱 지시]
${pacing_directive}

[회수 기한이 된 떡밥]
${due_seeds}

[흔들 때가 된 떡밥]
${ripening_seeds}

총 ${total_episodes}화 연재 중 ${episode_number}화의 비트시트를 설계하세요. 도입은 즉시 몰입되는 훅으로 시작합니다.
${closing_rule}
beat_type은 setup/escalation/payoff/frustration/reveal/cliffhanger 중에서만 쓰세요.

위 [흔들 때가 된 떡밥]은 다음 화에 회수해야 하는 것들입니다. 이 화에서 회수하지는 말고, 한 번 더 건드리기만 하세요 — 인물이 그것을 다시 떠올리거나, 다른 정보가 얹히거나, 상황이 한 단계 나빠지는 식입니다. 실제로 건드린 것의 ID를 `seeds_to_reinforce`에 적으세요. 던져놓고 아무 언급 없이 곧바로 회수하면 떡밥이 아니라 자문자답이 됩니다.

이 화에서 새로 던지는 떡밥은 `seeds_to_plant`에 적으세요. major는 회수하기 전에는 이야기를 끝낼 수 없는 줄기(정체·배신·숨은 목적)이고, minor는 나중에 한 번 되짚으면 끝나는 디테일입니다. due_by_ep에는 몇 화까지 회수할 작정인지 적으세요 — 총 화수를 보고 정하세요. major는 이야기의 뼈대라 보통 후반부에 가서야 회수됩니다. 매 화 심을 필요는 없습니다 — 없으면 빈 목록으로 두세요. 회수할 자신이 없는 떡밥은 심지 않는 편이 낫고, [페이싱 지시]가 새 떡밥을 금지하면 그쪽을 따르세요.

위 [회수 기한이 된 떡밥] 중 이 화에서 실제로 회수하는 것이 있으면, 대괄호 안의 ID를 `seeds_to_pay`에 그대로 적으세요. 회수한다면 그 회수 장면을 beats 안에 payoff 또는 reveal 비트로 반드시 넣으세요. 억지로 회수하지는 마세요 — 이 화에 자연스럽게 들어가지 않으면 빈 목록으로 두고 다음 화로 넘기세요.
