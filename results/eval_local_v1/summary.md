# Results: eval_local_v1

Valid runs: 234 | infra-error records excluded: 0 | reference policy: strong_only

| Policy | Resolution (task-avg) | Δ vs ref (pp) | Solved/Runs | Total cost $ | Cost/solved $ | Cost Δ vs ref | Median e2e s | Median API s | Mean calls | Escalation | False accept. | Tokens in/out |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| escalate | 79.5% | -6.4 | 62/78 | 0.0132 | 0.0002 | +56% | 6.3 | 5.3 | 1.65 | 65% | 3% | 63987/23246 |
| strong_only | 85.9% | +0.0 | 67/78 | 0.0085 | 0.0001 | +0% | 3.7 | 3.2 | 1.12 | n/a | 4% | 33722/8533 |
| small_only | 34.6% | -51.3 | 27/78 | 0.0088 | 0.0003 | +4% | 4.0 | 3.2 | 1.65 | n/a | 7% | 63753/24427 |

Resolution by bug_family (task-averaged; descriptive only, 1-8 tasks per family):

| Family | Tasks | escalate | strong_only | small_only |
|---|---|---|---|---|
| boundary | 7 | 86% | 100% | 19% |
| condition | 3 | 33% | 33% | 33% |
| missing_code | 4 | 67% | 75% | 42% |
| operator | 7 | 86% | 100% | 43% |
| variable | 5 | 100% | 87% | 40% |

Status counts:
- escalate: {'resolved': 62, 'unresolved_tests': 16}
- strong_only: {'resolved': 67, 'unresolved_tests': 11}
- small_only: {'candidate_timeout': 3, 'resolved': 27, 'unresolved_tests': 48}

Cost basis per model:
- small = qwen2.5-coder:1.5b: $0.1/1M in, $0.1/1M out (list-price estimate, runs locally)
- strong = qwen2.5-coder:7b: $0.2/1M in, $0.2/1M out (list-price estimate, runs locally)

Break-even (escalate: small -> strong; pre-registered, see docs/experiment_protocol.md):
- p_small_first_try (task-avg, visible pass on call 1) = 0.346; tasks with p = 0: 16/26
- mean cost per first call: C_small = $0.000054, C_strong = $0.000092 -> measured ratio C_strong/C_small = 1.70
- escalation saves money iff ratio > 1/p = 2.89 -> does NOT save at the measured ratio
- at illustrative frontier/small price ratios (equal tokens per call assumed): 10x -> saves, 30x -> saves, 100x -> saves
- plot: break_even_escalate.svg; per-task p: break_even_escalate.csv

Notes: resolution = valid output AND all visible AND all hidden tests pass. False acceptance = passed visible tests but failed hidden tests, among runs whose final candidate passed visible tests. Costs of local models are standardized list-price estimates (see config comments for source/date), not money spent; tokens and latency are measured.