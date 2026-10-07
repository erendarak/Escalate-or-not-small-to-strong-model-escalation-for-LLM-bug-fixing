# Routing Pilot: small → strong model escalation for LLM bug-fixing agents

*Independent pilot study. Not commissioned, reviewed or endorsed by any company. Uses only public data.*

**Question.** If a bug-fixing agent starts with a cheap/small model and escalates to a stronger model only when visible tests fail, how much test-based repair success does it keep compared with always using the strong model, and what does that cost in money, latency and tokens?

## Design (short)

| Item | Choice |
|---|---|
| Benchmark | QuixBugs (Python): 31 single-function programs, each with one real bug (MIT licence) |
| Split | 5 dev tasks (for tuning, one per bug family) / 26 eval tasks (only after freezing); stratified by `bug_family`, seed 2026 |
| Tests | ≤3 **visible** cases shown to the agent; the rest are **hidden** and only used for grading |
| Agent | Bounded repair loop: propose fix → run visible tests → at most one retry with visible feedback |
| Policies | `small_only` (S→S), `strong_only` (L→L), `escalate` (S→L). Every policy gets **at most 2 calls** |
| Models | Local: Qwen2.5-Coder 1.5B / 7B via Ollama. Later: one frontier API model |
| Metrics | Test-based resolution rate (overall and per bug family), cost per solved task, latency, tokens, escalation rate, false acceptance (passes visible, fails hidden) |
| Sampling | temperature 0.2, seed = base seed + repeat index, so repeats are independent samples |
| Cost | Local models priced at a standardized list-price estimate (Fireworks serverless size tiers, see config comments); tokens and latency are measured |

Fairness rules: the same prompt for every model, hidden results never reach a prompt or a routing decision, transport errors are retried and logged as infrastructure errors (not model failures), invalid model output stays in the denominator, and bad answers are never re-sampled.

## Bug labels

Each task's `meta.json` carries `bug_type_ye`, `bug_type` and `bug_family` (analysis only; they never reach a prompt, see `tests/test_infra.py`).
`bug_type_ye` is the per-program label from Ye et al., Table 1 (labelled on the Java versions; the QuixBugs paper itself only gives class counts).
**22 labels taken as-is from Ye et al. Table 1; 9 adapted to the Python diff**, each with a `bug_type_note`:

| Task | Ye et al. (Java) | Used (Python diff) |
|---|---|---|
| lcs_length | Incorrect array slice | Incorrect array index |
| flatten | Missing function call | Extra function call |
| levenshtein | Missing '+1' | Added '+1' |
| kheapsort | Missing function call | Missing array slice |
| longest_common_subsequence | Missing function call | Missing array slice |
| subsequences | Missing lines with a function call | Incorrect data structure constant |
| get_factors | Wrong constructor call | Incorrect data structure constant |
| kth | Reference to an incorrect variable | Incorrect argument value |
| mergesort | Incorrect arithmetic expression | Incorrect boundary condition |

The 17 Ye types are too sparse for 31 tasks, so analysis reports 5 coarse families
(`boundary`, `operator`, `variable`, `condition`, `missing_code`; mapping in `scripts/prepare_tasks.py`).
Eval has 3-7 tasks per family, so **per-family rates are descriptive only**, not evidence that a policy is better for a bug type.
Labels and families were fixed before any real-model run.

## Layout

```
configs/            dev_fake.yaml (plumbing), dev_local.yaml (Ollama), eval_3tier.TEMPLATE.yaml
data/public/        buggy.py, public_cases.json, meta.json      <- the only thing prompts can see
data/private/       reference.py, hidden_cases.json             <- evaluator only
scripts/prepare_tasks.py   rebuild data/ from a QuixBugs checkout and apply the acceptance gate
src/routing_pilot/
  harness.py   runs one test case in its own process      sandbox.py  per-case subprocess + timeout
  candidate.py extract/validate the model's code           prompts.py  identical prompt for all models
  models.py    Ollama/OpenAI-compatible + fake clients     experiment.py  repair loop, logging, resume, budget stop
  analysis.py  rebuilds every number from raw logs         cli.py      run / check / analyse
tests/test_infra.py  tests of the evaluator itself (overfitting caught, no leakage, resume, ...)
results/<campaign>/  attempts.jsonl, runs.jsonl, summary.md, per_task.csv, artifacts/
```

## Quick start

```bash
pip install -e ".[dev]"
python -m pytest -q                                            # evaluator tests
python -m routing_pilot.cli run --config configs/dev_fake.yaml # no model needed
python -m routing_pilot.cli check --config configs/dev_local.yaml
python -m routing_pilot.cli run --config configs/dev_local.yaml
```

## Known limitations

- QuixBugs is old and public, so it is probably in the models' training data (contamination). Results are about *this* benchmark and *these* models.
- Small tasks (single functions); this does not represent multi-file, real-repository work.
- Per-family results rest on 3-8 tasks per family (3-7 in eval) and are descriptive only.
- "Resolved" means all available tests pass, not proven correctness.
- Local model costs are list-price estimates from a hosted provider's size tiers, not money spent; hardware/energy cost is not included.
- Code produced by models runs in a separate process with an import/call deny-list. That is isolation, not a security sandbox.

## Attribution

Tasks derived from QuixBugs (Lin, Koppel, Chen, Solar-Lezama, 2017), https://github.com/jkoppel/QuixBugs, MIT licence. See `data/QUIXBUGS_LICENSE`.

Bug-type labels from Ye, Martinez, Durieux, Monperrus, "A Comprehensive Study of Automatic Program Repair on the QuixBugs Benchmark", *Journal of Systems and Software* (2021), arXiv:1805.03454, Table 1 (9 adapted to Python, listed above).
