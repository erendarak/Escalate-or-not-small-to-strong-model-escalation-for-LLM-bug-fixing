# Results: dev_local_v2

Valid runs: 15 | infra-error records excluded: 0 | reference policy: strong_only

| Policy | Resolution (task-avg) | Δ vs ref (pp) | Solved/Runs | Total cost $ | Cost/solved $ | Cost Δ vs ref | Median e2e s | Median API s | Mean calls | Escalation | False accept. | Tokens in/out |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| small_only | 20.0% | -60.0 | 1/5 | 0.0009 | 0.0009 | -12% | 6.0 | 5.6 | 1.80 | n/a | 0% | 6459/2324 |
| escalate | 80.0% | +0.0 | 4/5 | 0.0013 | 0.0003 | +32% | 7.8 | 6.8 | 1.80 | 80% | 0% | 6459/1903 |
| strong_only | 80.0% | +0.0 | 4/5 | 0.0010 | 0.0002 | +0% | 4.1 | 3.4 | 1.40 | n/a | 0% | 4496/492 |

Resolution by bug_family (task-averaged; descriptive only, 1-8 tasks per family):

| Family | Tasks | small_only | escalate | strong_only |
|---|---|---|---|---|
| boundary | 1 | 0% | 100% | 100% |
| condition | 1 | 0% | 100% | 100% |
| missing_code | 1 | 0% | 0% | 0% |
| operator | 1 | 0% | 100% | 100% |
| variable | 1 | 100% | 100% | 100% |

Status counts:
- small_only: {'candidate_timeout': 1, 'resolved': 1, 'unresolved_tests': 3}
- escalate: {'resolved': 4, 'unresolved_tests': 1}
- strong_only: {'resolved': 4, 'unresolved_tests': 1}

Cost basis per model:
- small = qwen2.5-coder:1.5b: $0.1/1M in, $0.1/1M out (list-price estimate, runs locally)
- strong = qwen2.5-coder:7b: $0.2/1M in, $0.2/1M out (list-price estimate, runs locally)

Break-even (escalate: small -> strong; pre-registered, see docs/experiment_protocol.md):
- p_small_first_try (task-avg, visible pass on call 1) = 0.200; tasks with p = 0: 4/5
- mean cost per first call: C_small = $0.000071, C_strong = $0.000108 -> measured ratio C_strong/C_small = 1.51
- escalation saves money iff ratio > 1/p = 5.00 -> does NOT save at the measured ratio
- at illustrative frontier/small price ratios (equal tokens per call assumed): 10x -> saves, 30x -> saves, 100x -> saves
- plot: break_even_escalate.svg; per-task p: break_even_escalate.csv

Notes: resolution = valid output AND all visible AND all hidden tests pass. False acceptance = passed visible tests but failed hidden tests, among runs whose final candidate passed visible tests. Costs of local models are standardized list-price estimates (see config comments for source/date), not money spent; tokens and latency are measured.