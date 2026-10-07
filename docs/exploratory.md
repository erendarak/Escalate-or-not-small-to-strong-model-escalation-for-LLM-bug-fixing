# Exploratory analysis (post-hoc, not pre-registered)

Everything here was decided after the eval results were seen. It explains the pre-registered
result and does not replace it. The pre-registered verdict stands: `escalate` is not acceptable
(-6.4 percentage points against `strong_only`, +56% cost).

Caveats: 26 tasks, and the 3 repeats are correlated (see the protocol). The tasks inspected in
section C were chosen because they looked surprising. P-values are exploratory.

All numbers come from the logs via `scripts/exploratory.py`:

```
python scripts/exploratory.py eval_local_v1 > docs/exploratory_tables.md
python scripts/exploratory.py eval_local_v1 escalate_clean_v1 > docs/exploratory_handoff_tables.md
```

## A. Hand-off context effect

I compared the 7B model on a clean prompt (strong_only, attempt 1) with the 7B model when its
prompt also contains the small model's failed candidate (escalate, attempt 2, escalated runs
only). 18 tasks had at least one escalation.

- Task-averaged success: clean 83%, with the small candidate in context 73% (-10.2 points).
  Clean was better on 4 tasks, in-context on 1 (kth), and 13 tied.
- Paired by task and repeat, with the same seed: clean won 7 pairs and in-context won 2. Exact
  two-sided sign test p = 0.18. This is consistent with an effect but does not establish one.
- Escalated runs are the ones the small model failed, so they are not a random sample. Pairing
  controls for the task, not for this selection.

Pairing every escalate run with the strong_only run on the same task and repeat (62 vs 67 solved):

| Escalate run was | strong_only wins | escalate wins | net |
|---|---|---|---|
| not escalated (small model's answer kept) | 2 | 3 | +1 |
| escalated (7B after the small model) | 8 | 2 | -6 |

The non-escalated runs net +1. The small model wins `max_sublist_sum` 3/3, which the 7B fails on
a clean prompt. It loses `find_in_sorted` 2/3 by false acceptance: its fix passed every visible
test and failed 1 of 5 hidden cases. Routing on visible tests cannot catch that. The whole gap
comes from the escalated runs.

## B. Is strong_only just getting two shots?

No. strong_only solved 67 of 78 runs, 66 of them on the first call. It made 9 second attempts
and one succeeded. With the first call alone its resolution would be 85% instead of 86%, still
above escalate's 79%.

## C. Does the 7B copy the small model's code?

No. In all 51 escalated runs the 7B candidate never matched the small candidate code-for-code,
and never matched the buggy program. That is why I call this a hand-off context effect and not
anchoring. On the two inspected tasks it fails in other ways (code-only comparison):

- `subsequences`, over-correction, 3 of 3 repeats. The small model returned the bug unchanged,
  and the prompt shows that code as "a previous attempt ... still wrong". The 7B makes the
  correct fix (`return []` to `return [[]]`, the same fix as its clean answer) and also changes
  the loop bound (`b + 1 - k` to `b + 1 - k + 1`), which breaks it. The framing may push it to
  change more than needed. That is a hypothesis.
- `knapsack`, structural mix, 3 of 3 repeats. The small model made the inner loop run backwards
  but kept the 2-D memo. With that code in context, the 7B keeps both and drops the row-copy line,
  which is wrong. On a clean prompt the 7B also chose a backward loop but switched consistently to
  a 1-D memo, which was correct in 2 of 3 repeats. This is weaker evidence, because both versions
  chose the backward loop on their own.

## D. Feedback-only hand-off (escalate_clean)

`escalate_clean` is `[small, strong]`, but when attempt 1 fails, the strong call gets the original
prompt plus the visible-test feedback only, without the small model's code. Tasks, repeats,
seeds, models and prices are the same as in `eval_local_v1`.

The prediction and the test were committed before the run. If the hand-off context hurts,
escalate_clean should resolve more escalated runs than escalate on the same (task, repeat)
pairs. The primary comparison uses pairs that escalated in both campaigns, and reports the
discordant counts with an exact two-sided sign test. Pairs that escalated in only one campaign
are reported separately. The pre-registered decision rule is not reapplied, because this policy
was designed after the results were known.

Results (78 runs, no infrastructure errors):

- The small model's first attempt reproduced well. 66/78 candidates were byte-identical to
  `eval_local_v1`, and 76/78 had the same visible-test outcome.
- 50 pairs escalated in both campaigns. 2 pairs (`to_base` r1, r2) escalated in only one, and
  were solved either way.
- Primary: escalate_clean solved 7 pairs that escalate failed, and escalate solved 0 that
  escalate_clean failed. Exact two-sided sign test p = 0.016. The prediction held. The 7 pairs
  are `subsequences` r0-r2, `knapsack` r1-r2, `mergesort` r0 and `get_factors` r2.
- Secondary: escalate_clean's second call against the 7B on a clean prompt (strong_only, attempt
  1), on the same pairs: 3 vs 1, p = 0.63. No detectable difference.
- Every non-primary pair had the same outcome in both campaigns, so the 7 extra solved runs come
  entirely from the hand-off change.

| Policy | Resolution | Solved | Est. cost |
|---|---|---|---|
| escalate_clean | 88.5% | 69/78 | $0.0104 |
| strong_only | 85.9% | 67/78 | $0.0085 |
| escalate | 79.5% | 62/78 | $0.0132 |

escalate_clean still costs 22% more than strong_only. Its second prompt is shorter, which saves
money against escalate. At the local price ratio, though (1.7 measured, while break-even needs
more than 2.9 at p = 0.35), escalation cannot save money.

The escalate_clean latency is invalid and not reported. Another application was using the GPU
during part of the run (99% load, thermal slowdown active). The 21 s median in its `summary.md`
reflects that, not the policy.

## Interpretation

In this setting, escalate lost quality because of the hand-off context, not because of
escalation itself. A hand-off that passes only the visible-test feedback kept the strong
model's quality. This is one benchmark, two local models and a post-hoc design. It needs a
pre-registered replication before it can be claimed, for example with a frontier model and the
feedback-only hand-off fixed in advance.
