# EXPLORATORY analysis of eval_local_v1 (post-hoc, NOT pre-registered)

Everything here was decided **after** seeing the eval results. It uses only the existing
logs and local artifacts (no new model calls). It explains the pre-registered result; it does not
replace it. The pre-registered verdict stays: `escalate` is not acceptable (−6.4 pp, +56% cost).

Caveats: 26 tasks, 3 repeats that are correlated (low seed diversity at T = 0.2, see protocol),
and the tasks inspected in C were chosen because they looked surprising. No significance claims.

Numbers: `docs/exploratory_tables.md`, regenerated with
`python scripts/exploratory.py eval_local_v1 > docs/exploratory_tables.md`.

## A. Hand-off context effect: does the small model's failed candidate hurt the 7B model?

The 7B model's single-shot success on the **clean** prompt (strong_only attempt 1) was compared
with its success when the prompt also carries the small model's failed candidate and the
visible-test feedback (escalate attempt 2). The comparison covers the 18 tasks where escalation
happened at least once.

- Task-averaged: **clean 83% vs in-context 73% (−10.2 pp)**. Clean was better on 4 tasks,
  in-context on 1 (kth), and 13 tied.
- Paired by (task, repeat), using the same seed: clean succeeded and in-context failed in 7 runs;
  the reverse happened in 2 runs. **Exact two-sided sign test on the 7 vs 2 discordant pairs:
  p = 0.18.** This is consistent with a hand-off context effect but does not establish one.

Caveat: escalated runs are the ones the small model failed, so they are not a random sample of
attempts. The pairing by task and repeat controls for which task it is, not for this selection.

## Where escalate's 5-run gap comes from

Each escalate run is paired with the strong_only run on the same task and repeat (62 vs 67 solved):

| Escalate run was | strong_only wins | escalate wins | net |
|---|---|---|---|
| not escalated (small's answer kept) | 2 | 3 | +1 |
| escalated (7B after the small model) | 8 | 2 | −6 |

- **Not escalated, +1:**
  - The small model wins max_sublist_sum 3/3; the 7B fails it on the clean prompt.
  - It loses find_in_sorted 2/3 by **false acceptance**: the small fix passed all visible tests
    and failed 1 of 5 hidden cases.
  - Routing on visible tests cannot catch this kind of failure.
- **Escalated, −6:** the gap comes from the hand-off. The second call sees the small model's wrong
  code and does worse than the 7B model alone.

## B. Is strong_only's advantage just "two shots"?

No. strong_only solved 67 of 78 runs: **66 on attempt 1 and 1 on attempt 2**. It made 9 second
attempts and only one succeeded. With attempt 1 alone, its task-averaged resolution would be
85% instead of 86%, still above escalate's 79%.

## C. Does the 7B model copy or minimally edit the small model's wrong code?

This is why the finding is called a **hand-off context effect** and not anchoring: the 7B never
reproduces the small model's code.

**It doesn't copy.** In all 51 escalated runs, the 7B candidate never matched the small candidate
code-for-code, and never matched the buggy program. Instead, the inspected tasks show two ways the
hand-off misleads it (code-only comparison):

- **subsequences, over-correction (3/3 repeats):**
  - Here the small model returned the buggy code unchanged. The prompt then shows that code as
    "a previous attempt … still wrong", together with a visible failure.
  - The 7B makes the correct fix (`return []` → `return [[]]`, the same fix as its clean answer)
    **and** an extra change to the loop bound (`b + 1 - k` → `b + 1 - k + 1`), which breaks it.
  - The extra edit is consistent with the framing pushing it to change more than the one line
    needed; it is a hypothesis.
- **knapsack, structural mix (3/3 repeats):**
  - The small model rewrote the inner loop to run backwards (`range(capacity, weight - 1, -1)`)
    but kept the 2-D memo.
  - With the small candidate in context, the 7B keeps that backward loop and the 2-D memo, and drops the row-copy line, so it
    is wrong.
  - On the clean prompt the 7B also chose a backward loop, but converted consistently to a 1-D
    memo, which is correct in 2 of 3 repeats.
  - So the in-context answer looks like a mix of its own idea and the small model's structure.
    This is weaker evidence than subsequences, because both versions independently chose the
    backward loop.

## What this suggests (to be tested, not concluded)

- The cost of escalation here is not only the extra call. Showing the strong model a weaker
  model's wrong code can lower its success.
- A hand-off that passes **only the visible-test feedback**, without the previous code, might
  keep most of the strong model's clean success. That is the proposed `escalate_clean` campaign,
  also post-hoc and exploratory.

## Prediction for `escalate_clean_v1` (written and committed BEFORE the run)

`escalate_clean` = `[small, strong]`, but when attempt 1 fails, the strong call gets the original
prompt plus the visible-test feedback only, without the small model's code. Same 26 eval tasks,
repeats, seeds (2026 + repeat), models and prices as `eval_local_v1`.

**Prediction:** if the hand-off context hurts, escalate_clean should resolve more escalated runs
than escalate on the same (task, repeat) pairs.

How it is checked, fixed now:

- The primary comparison uses (task, repeat) pairs that escalated in **both** campaigns. Count the
  discordant pairs: resolved only under escalate_clean vs resolved only under escalate. Report
  both counts and an exact two-sided sign test p-value. This is exploratory: no claim beyond what
  the test shows.
- Pairs that escalated in only one campaign, because the small model's first attempt did not
  reproduce, are reported separately and are not part of the primary comparison.
- Also reported: how many small-model first attempts match `eval_local_v1`'s `candidate_hash`,
  and overall resolution, cost and calls for escalate_clean, next to escalate and strong_only.
- Not done: the pre-registered decision rule is not re-applied to declare escalate_clean a
  success, because this policy was designed after seeing the eval results.

## Result of `escalate_clean_v1` (added after the run)

Tables: `docs/exploratory_handoff_tables.md`, regenerated with
`python scripts/exploratory.py eval_local_v1 escalate_clean_v1`. 78 runs, 0 infra errors.

- **Reproducibility of the small model's first attempt** (same prompt and seed):
  - 66/78 candidates are byte-identical to `eval_local_v1`, and 76/78 have the same
    visible-test outcome.
  - 50 (task, repeat) pairs escalated in both campaigns; 2 escalated in only one (to_base r1,
    r2), and both of those were resolved either way.
- **Primary comparison (prediction above):** on the 50 pairs, escalate_clean resolved 7 that
  escalate failed, and escalate resolved 0 that escalate_clean failed.
  - **Exact two-sided sign test: p = 0.016** (exploratory).
  - The 7 pairs are subsequences r0–r2, knapsack r1–r2, mergesort r0 and get_factors r2.
  - **The prediction held.** Removing the small model's code from the hand-off recovered the
    lost runs.
- **Secondary comparison:** escalate_clean's second attempt vs the 7B single shot on a clean
  prompt (strong_only attempt 1), same pairs. Discordant 3 vs 1, p = 0.63, so no detectable
  difference.
  - With the code removed, the 7B does about as well as when it starts fresh.
- **Overall (descriptive):**

  | Policy | Resolution | Solved | Cost (est.) |
  |---|---|---|---|
  | escalate_clean | 88.5% | 69/78 | $0.0104 |
  | strong_only | 85.9% | 67/78 | $0.0085 |
  | escalate | 79.5% | 62/78 | $0.0132 |

  - All non-primary pairs had identical outcomes, so the +7 runs over escalate come entirely
    from the hand-off change.
  - escalate_clean still costs **+22%** more than strong_only. The feedback-only prompt is
    shorter, which saves money relative to escalate, but at the local price ratio (1.7× measured;
    break-even needs > 2.9× at p = 0.35) escalation still cannot save money.
- **Latency is not comparable:** another GPU-heavy application ran during part of this campaign
  (GPU at 99%, thermal slowdown active), so its latencies (median e2e 21 s) say nothing about
  the policy.

**Interpretation, exploratory:**
- In this setting, the quality loss of escalate came from the hand-off context, not from
  escalation itself.
- A hand-off that passes only the visible-test feedback kept the strong model's quality.
- This is one benchmark, two local models and a post-hoc design. It would need a pre-registered
  replication, for example in the `eval_3tier` frontier campaign with a feedback-only hand-off
  fixed in advance, before it can be claimed.
