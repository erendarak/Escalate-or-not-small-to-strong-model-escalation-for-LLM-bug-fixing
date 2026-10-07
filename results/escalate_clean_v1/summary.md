# Results: escalate_clean_v1

Valid runs: 78 | infra-error records excluded: 0 | reference policy: strong_only

| Policy | Resolution (task-avg) | Δ vs ref (pp) | Solved/Runs | Total cost $ | Cost/solved $ | Cost Δ vs ref | Median e2e s | Median API s | Mean calls | Escalation | False accept. | Tokens in/out |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| escalate_clean | 88.5% | – | 69/78 | 0.0104 | 0.0002 | – | 21.0 | 19.3 | 1.65 | 65% | 3% | 53094/20344 |

Resolution by bug_family (task-averaged; descriptive only, 3-7 tasks per family):

| Family | Tasks | escalate_clean |
|---|---|---|
| boundary | 7 | 90% |
| condition | 3 | 33% |
| missing_code | 4 | 100% |
| operator | 7 | 95% |
| variable | 5 | 100% |

Status counts:
- escalate_clean: {'resolved': 69, 'unresolved_tests': 9}

Cost basis per model:
- small = qwen2.5-coder:1.5b: $0.1/1M in, $0.1/1M out (list-price estimate, runs locally)
- strong = qwen2.5-coder:7b: $0.2/1M in, $0.2/1M out (list-price estimate, runs locally)

Notes: resolution = valid output AND all visible AND all hidden tests pass. False acceptance = passed visible tests but failed hidden tests, among runs whose final candidate passed visible tests. Costs of local models are standardized list-price estimates (see config comments for source/date), not money spent; tokens and latency are measured.