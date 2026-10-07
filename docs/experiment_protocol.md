# Experiment protocol

This protocol was fixed before the eval run and tagged `eval-local-freeze`. The file at that tag
is the exact frozen text. This copy is edited for readability only. No rule, threshold or
number has changed. After the results were seen, nothing in the config, prompt or task set was
changed. Any rerun uses a new campaign name, and old results are never deleted.

## Setup

| Item | Frozen value |
|---|---|
| Tasks | QuixBugs Python, 31 programs. Eval split: 26 tasks, stratified by `bug_family`, seed 2026 (`data/manifest.json`) |
| Dev split | 5 tasks, one per family. Used for all tuning and never part of the eval numbers |
| Models | small = `qwen2.5-coder:1.5b` (Ollama digest `d7372fd82851`, Q4_K_M), strong = `qwen2.5-coder:7b` (digest `dae161e27b0e`, Q4_K_M), Ollama 0.40.0 |
| Prompt | `repair_v1` (`src/routing_pilot/prompts.py`) |
| Sampling | temperature 0.2, max_tokens 1500, seed = 2026 + repeat index, the same for every policy within a repeat |
| Repeats | 3, so 26 tasks x 3 policies x 3 repeats = 234 runs |
| Loop | At most 2 calls per run. Attempt 2 sees the original program, the previous candidate and visible-test feedback only. The run stops early on a visible pass |
| Grading | The final candidate runs on the hidden tests in a fresh process. Hidden results never reach a prompt or a routing decision |
| Cost | Fireworks serverless size-tier list prices. Estimates, not billed. Source and date are in the config |

## Policies

| Policy | Attempt 1 | Attempt 2 |
|---|---|---|
| `small_only` | small | small |
| `strong_only` (reference) | strong | strong |
| `escalate` | small | strong, only if attempt 1 fails the visible tests |

## Metrics

- Resolution: valid output that passes all visible and all hidden tests. Averaged over the 3
  repeats of each task, then over tasks. Invalid output and timeouts stay in the denominator.
  Only infrastructure errors are excluded, and they are reported.
- Cost: total, and per solved run.
- Latency: median end-to-end and median model-API seconds per run.
- False acceptance: the final candidate passed the visible tests but failed the hidden tests.
- Escalation rate, mean calls, and patch size (`lines_changed` counts code only;
  `lines_changed_raw` is the plain text diff).

## Research questions

1. What are the hidden-test resolution rates of the three policies?
2. How do the total cost and the cost per solved task of `escalate` compare with `strong_only`?
3. How many patches that pass the visible tests fail the hidden tests, per policy?
4. For which bug families does escalation help, and where does it escalate for nothing? With
   3-7 eval tasks per family, this is descriptive only.
5. Is any cost saving bought with longer latency or lower resolution?

## Decision rule

`escalate` is acceptable if both conditions hold on the eval split:

1. its resolution is at most one task below `strong_only`: a difference of at least -1/26,
   about -3.8 percentage points, task-averaged, and
2. its total cost is lower than that of `strong_only`.

Otherwise the result is "not acceptable under the pre-set rule". This is a practical threshold,
not a statistical non-inferiority test. With 26 tasks, differences of a few points are within
noise.

## Break-even analysis

Ignoring that the second prompt is longer, a run costs about `C_small + (1 - p) * C_strong` under
`escalate` and `C_strong` under `strong_only`. Here `p` is the probability that the small model
passes the visible tests on its first try and `C_x` is the cost of one call to model x.
Escalation saves money only if `p * C_strong > C_small`, that is if `C_strong / C_small > 1 / p`.

`analysis.py` computes this from the logs for every two-model policy and writes it to
`summary.md`, `break_even_<policy>.svg` and `break_even_<policy>.csv`:

- `p` is computed per task over the 3 repeats and then averaged over tasks. Per-task values are
  ticks on the plot and rows in the CSV.
- `C_small` and `C_strong` are the mean measured cost of one first-attempt call to each model.
- The plot shows cost ratio (log scale) against `p`, the curve `ratio = 1/p`, the measured local
  ratio, and illustrative 10x, 30x and 100x ratios. The illustrative ratios are round numbers,
  not prices of named models.

Expectation stated before the eval: on dev, C_small was about $0.00007 and C_strong about
$0.00011 per first call, so break-even needed p of about 0.65 or more. Dev p was 0.20. With these
two local models, `escalate` was therefore unlikely to be cheaper than `strong_only`.

## Known limitations at freeze

- 26 small single-function programs. Results apply to this benchmark and these two models only.
- Some tasks have very few visible tests (`wrap` has one). This is left unchanged.
- In dev, the 1.5B model often returned the buggy code unchanged. These runs are not filtered out.
- Local cost is a list-price estimate. Latency depends on one laptop GPU (RTX 4070 Laptop).
- The local models are Q4_K_M quantized, while the list prices are for hosted, usually
  full-precision models.
- Seeds are applied: on kheapsort, seed 0 reproduced exactly and seed 1 gave a different answer.
  At temperature 0.2, though, there were only 2 distinct answers in 6 seeds on both kheapsort and
  possible_change. The 3 repeats are correlated samples, not independent ones.

## Freeze record

- Tag: `eval-local-freeze`, the commit that adds this protocol and `configs/eval_local.yaml`.
- Dev evidence: `results/dev_local_v1`, `results/dev_local_v2`.
