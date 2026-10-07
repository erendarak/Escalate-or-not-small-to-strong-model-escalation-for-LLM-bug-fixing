# Results: dev_fake

Valid runs: 18 | infra-error records excluded: 0 | reference policy: strong_only

| Policy | Resolution (task-avg) | Δ vs ref (pp) | Solved/Runs | Total cost $ | Cost/solved $ | Cost Δ vs ref | Median e2e s | Mean calls | Escalation | False accept. | Tokens in/out |
|---|---|---|---|---|---|---|---|---|---|---|---|
| small_only | 0.0% | -100.0 | 0/6 | 0.0012 | ∞ (0 solved) | -95% | 0.4 | 2.00 | n/a | n/a | 4626/1720 |
| escalate | 100.0% | +0.0 | 6/6 | 0.0280 | 0.0047 | +18% | 0.4 | 2.00 | 100% | 0% | 4626/2103 |
| strong_only | 100.0% | +0.0 | 6/6 | 0.0237 | 0.0040 | +0% | 0.3 | 1.00 | n/a | 0% | 1692/1243 |

Status counts:
- small_only: {'candidate_timeout': 1, 'unresolved_tests': 5}
- escalate: {'resolved': 6}
- strong_only: {'resolved': 6}

Notes: resolution = valid output AND all visible AND all hidden tests pass. False acceptance = passed visible tests but failed hidden tests, among runs whose final candidate passed visible tests. Local models are $0 by construction; compare them on latency/tokens.