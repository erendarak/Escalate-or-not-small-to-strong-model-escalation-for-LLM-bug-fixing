# Experiment protocol (frozen before the eval run)

This file is written and committed **before** any eval task is run. The eval
campaign is `configs/eval_local.yaml`. After the eval results are seen, nothing
in this file, the config, the prompt or the task set is changed; any re-run uses
a new campaign name and old results are never deleted.

## Setup

| Item | Frozen value |
|---|---|
| Tasks | QuixBugs Python, 31 programs; eval split = 26 tasks (stratified by `bug_family`, seed 2026, see `data/manifest.json`) |
| Dev split | 5 tasks (one per family), used for all tuning; never part of the eval numbers |
| Models | small = `qwen2.5-coder:1.5b` (Ollama digest `d7372fd82851`, Q4_K_M), strong = `qwen2.5-coder:7b` (digest `dae161e27b0e`, Q4_K_M); Ollama 0.40.0 |
| Prompt | `repair_v1` (`src/routing_pilot/prompts.py`) |
| Sampling | temperature 0.2, max_tokens 1500, seed = 2026 + repeat index (same seed for every policy within a repeat) |
| Repeats | 3 → 26 tasks × 3 policies × 3 repeats = 234 runs |
| Loop | at most 2 calls per run; attempt 2 sees the original program, the previous candidate and **visible**-test feedback only; stop early on a visible pass |
| Grading | final candidate runs on hidden tests in a fresh process; hidden results never reach a prompt or a routing decision |
| Cost | local models priced at Fireworks serverless size-tier list prices (estimate, not billed; source and date in the config) |

## Policies

| Policy | Attempt 1 | Attempt 2 |
|---|---|---|
| `small_only` | small | small |
| `strong_only` | strong | strong |
| `escalate` | small | strong (only if attempt 1 fails the visible tests) |

`strong_only` is the reference policy.

## Metrics

- **Resolution**: valid output AND all visible AND all hidden tests pass. Rates are task-averaged: first averaged over the 3 repeats of each task, then over tasks. Invalid output and timeouts stay in the denominator; only infrastructure errors are excluded (and reported).
- **Cost**: total and per solved run (list-price estimate from measured tokens).
- **Latency**: median end-to-end and median model-API seconds per run.
- **False acceptance**: final candidate passed the visible tests but failed the hidden tests.
- **Escalation rate**, **mean calls**, **patch size** (`lines_changed` on code only; `lines_changed_raw` kept).

## Research questions

- **RQ1.** What are the hidden-test resolution rates of the three policies?
- **RQ2.** How do total cost and cost per solved task of `escalate` compare with `strong_only`?
- **RQ3.** How many patches that pass the visible tests fail the hidden tests (false acceptance), per policy?
- **RQ4.** For which bug families does escalation help, and where does it add unnecessary escalation? Per-family rates are **descriptive only** (3–7 eval tasks per family); no per-family claims of significance.
- **RQ5.** Is any cost saving bought with longer latency or lower resolution?

## Decision rule (set before the eval run)

`escalate` is called **acceptable** for my use if both hold on the eval split:

1. its resolution is at most **one task** below `strong_only`: Δ ≥ −1/26 ≈ **−3.8 pp** (task-averaged), and
2. its total cost is **lower** than `strong_only`'s.

If either fails, the result is reported as "not acceptable under the pre-set rule".
This is a practical preference threshold, not a statistical non-inferiority test;
with 26 tasks and 3 repeats, differences of a few points are within noise. The
threshold is not changed after the results are seen.

## Break-even note

Ignoring that a second-attempt prompt is somewhat longer, a run costs about
`C_small + (1 − p) · C_strong` under `escalate` and `C_strong` under `strong_only`,
where `p` is the probability that the small model passes the visible tests on its
first try and `C_x` is the cost of one call to model x. Escalation saves money only if

    p_small_first_try × C_strong > C_small

On the dev runs (list prices): C_small ≈ $0.00007 and C_strong ≈ $0.00011 per first call
(the 1.5B model is half the per-token price but writes more output tokens per call), so
break-even needs p ≳ 0.65. The dev value was p = 1/5. **Expectation written before
eval:** with these two local models and these prices, `escalate` is unlikely to be
cheaper than `strong_only`; the price gap is too small. The rule is about a cheap
first model and an expensive second one, which is the frontier setting planned
for `eval_3tier`.

## Analysis plan: break-even (pre-registered, before eval)

`analysis.py` computes this from the logs for every two-model policy (`escalate`), and writes
it into `summary.md`, `break_even_escalate.svg` and `break_even_escalate.csv`:

- `p` = share of `escalate` runs whose first call (small model) passed the visible tests,
  computed per task (over 3 repeats) and then averaged over tasks; the per-task values are
  shown as ticks on the plot and listed in the CSV.
- `C_small`, `C_strong` = mean measured cost of one first-attempt call to each model.
- Escalation saves money when `C_strong / C_small > 1 / p`.
- Plot: cost ratio (log scale) against `p`, with the break-even curve `ratio = 1/p`, the measured
  local ratio, and illustrative frontier/small price ratios of 10×, 30× and 100× marked. The
  10/30/100 values are round numbers for orientation, not prices of named models; the
  `eval_3tier` campaign uses its own configured, sourced prices.
- Dev check (dev_local_v2): p = 0.20, measured ratio 1.51×, required > 5× → no saving.

## Known limitations recorded at freeze

- 26 tasks, small single-function programs; results apply to this benchmark and these two models only.
- Some tasks have very few visible tests (e.g. `wrap` has one), which limits feedback; this is left unchanged by design and reported as a finding.
- In dev, the 1.5B model often returned the buggy code unchanged; this is model behaviour, not filtered out.
- Local cost is a list-price estimate; latency depends on one laptop GPU (RTX 4070 Laptop).
- The local models run Q4_K_M-quantized, while the reference list prices are for hosted
  (typically full-precision) models; a hosted model at that price may solve more or fewer tasks.
- Seeds are applied (checked before freeze: on kheapsort, seed 0 reproduces exactly and seed 1
  gives a different response), but at temperature 0.2 diversity is low: 2 distinct responses
  out of 6 seeds on both kheapsort and possible_change. The 3 repeats are therefore correlated
  samples, not 3 independent ones, and repeat-to-repeat agreement will look high.

## Freeze record

- Frozen commit / tag: `eval-local-freeze` (the commit that adds this file and `configs/eval_local.yaml`)
- Dev evidence: `results/dev_local_v1`, `results/dev_local_v2`
