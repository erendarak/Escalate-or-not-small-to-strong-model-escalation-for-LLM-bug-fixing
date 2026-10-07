# Results: dev_local_v1

Valid runs: 15 | infra-error records excluded: 0 | reference policy: strong_only

| Policy | Resolution (task-avg) | Δ vs ref (pp) | Solved/Runs | Total cost $ | Cost/solved $ | Cost Δ vs ref | Median e2e s | Median API s | Mean calls | Escalation | False accept. | Tokens in/out |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| small_only | 20.0% | -60.0 | 1/5 | 0.0008 | 0.0008 | -21% | 4.6 | 4.2 | 1.80 | n/a | 50% | 6166/1742 |
| escalate | 80.0% | +0.0 | 4/5 | 0.0012 | 0.0003 | +23% | 6.5 | 6.0 | 1.80 | 80% | 0% | 6166/1610 |
| strong_only | 80.0% | +0.0 | 4/5 | 0.0010 | 0.0002 | +0% | 4.2 | 3.3 | 1.40 | n/a | 0% | 4496/492 |

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

Notes: resolution = valid output AND all visible AND all hidden tests pass. False acceptance = passed visible tests but failed hidden tests, among runs whose final candidate passed visible tests. Costs of local models are standardized list-price estimates (see config comments for source/date), not money spent; tokens and latency are measured.