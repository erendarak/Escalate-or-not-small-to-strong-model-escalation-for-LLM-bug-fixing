# EXPLORATORY analysis of eval_local_v1 (post-hoc, NOT pre-registered)

Everything here was decided **after** seeing the eval results. It uses only the existing
logs and local artifacts (no new model calls). It explains the pre-registered result; it does not
replace it. The pre-registered verdict stays: `escalate` is not acceptable (−6.4 pp, +56% cost).

Caveats: 26 tasks, 3 repeats that are correlated (low seed diversity at T = 0.2, see protocol),
and the tasks inspected in C were chosen because they looked surprising. No significance claims.

Numbers: `docs/exploratory_tables.md`, regenerated with
`python scripts/exploratory.py eval_local_v1 > docs/exploratory_tables.md`.

## A. Does the small model's failed candidate hurt the 7B model?

The 7B model's single-shot success on the **clean** prompt (strong_only attempt 1) was compared
with its success when the prompt also carries the small model's failed candidate and the
visible-test feedback (escalate attempt 2). The comparison covers the 18 tasks where escalation
happened at least once.

- Task-averaged: **clean 83% vs anchored 73% (−10.2 pp)**. Clean was better on 4 tasks, anchored
  on 1 (kth), and 13 tied.
- Paired by (task, repeat), using the same seed: clean succeeded and anchored failed in 7 runs;
  the reverse happened in 2 runs.

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
  - The anchored 7B keeps that backward loop and the 2-D memo, and drops the row-copy line, so it
    is wrong.
  - On the clean prompt the 7B also chose a backward loop, but converted consistently to a 1-D
    memo, which is correct in 2 of 3 repeats.
  - So the anchored answer looks like a mix of its own idea and the small model's structure.
    This is weaker evidence than subsequences, because both versions independently chose the
    backward loop.

## What this suggests (to be tested, not concluded)

- The cost of escalation here is not only the extra call. Showing the strong model a weaker
  model's wrong code can lower its success.
- A hand-off that passes **only the visible-test feedback**, without the previous code, might
  keep most of the strong model's clean success. That is the proposed `escalate_clean` campaign,
  also post-hoc and exploratory.
