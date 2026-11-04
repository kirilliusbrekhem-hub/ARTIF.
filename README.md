# ARTIF.

**Proof that AI has a heart.**

ARTIF. is an early-stage project exploring a personal AI agent that turns a person's goals into results that are
**planned, executed and verified**. It is being built carefully, in small steps, around a few promises:

- **Permission first.** ARTIF. shows what it intends to do and asks before it acts.
- **Safety by default.** It never deletes or overwrites your files unless you explicitly allow it.
- **Verification before success.** A step only counts as done when its result has been checked.
- **Honest reporting.** It says what worked, what failed and what it is unsure about.

> **Status:** early development. ARTIF. is **not** an autonomous AI agent today, and it does not yet use an AI
> model. This README separates what exists from what is planned.

> **Naming:** ARTIF. is the new name of the project formerly called Orbisent. The Python package is still called
> `orbisent`, and some files and documents still use the old name. Those have not been renamed yet.

---

## Two development tracks

ARTIF. has two tracks of equal importance. One builds a dependable agent; the other studies the intelligence that
could one day power it.

| ARTIF Core: the agent | ARTIF Research: the intelligence |
| --- | --- |
| Goal understanding | Neural networks |
| Planning Engine | Reasoning |
| Permissions and security | Learning |
| Execution and verification | Efficient intelligence |
| Future memory and integrations | Reproducible experiments |

---

## Current development status

| Area | Status | Where |
| --- | --- | --- |
| Prototype 0.3: command-line agent with fixed plans | ✅ Implemented and tested | [`prototype/`](prototype/) |
| ARTIF 1.0 architecture | ✅ Written | [`ARCHITECTURE_1.0.md`](ARCHITECTURE_1.0.md) |
| ARTIF 1.0 shared types (`Request`, `GoalSpec`) | ✅ Implemented and tested | [`orbisent/types.py`](orbisent/types.py) |
| ARTIF 1.0 goal understanding | ✅ Implemented and tested | [`orbisent/understanding/`](orbisent/understanding/) |
| ARTIF 1.0 Planning Engine | 📝 In design, not implemented | — |
| Tools, sandbox, execution engine, verification, approval (1.0) | ⏳ Planned | [`ARCHITECTURE_1.0.md`](ARCHITECTURE_1.0.md) |
| AI model integration, memory, integrations | ⏳ Planned, later phases | [`ARCHITECTURE_1.0.md`](ARCHITECTURE_1.0.md) |
| Research experiment 01: tiny Transformer from scratch | 🧪 Educational notebook | [`notebooks/`](notebooks/) |

### Prototype 0.3 (working today)

A small Python command-line prototype that uses only the standard library.

- Two task types:
  - `prepare_project` creates `README.md`, `TASKS.md` and `LAUNCH_PLAN.md`;
  - `create_launch_checklist` creates `LAUNCH_CHECKLIST.md`.
- **Deterministic, keyword-based goal classification.** If a goal is unclear or matches more than one task type,
  the prototype says so and creates nothing. You can choose a task type yourself with `--task`.
- **Fixed plans,** run as ordered steps. Each step shows its expected outcome.
- **User approval before execution.** The plan is shown first, and nothing happens until you answer `y` (or pass
  `--yes`).
- **Verification after each step.** Even steps skipped because the file already exists are checked.
- **Stops at the first failure,** and reports every step as completed, skipped or failed.
- **Create-only file writes:** existing files are never overwritten or deleted.
- **No AI model.** Plans come from fixed rules, not from an AI.

```bash
python3 prototype/orbisent_agent.py "Prepare my project folder for launch"
python3 prototype/orbisent_agent.py "Create a launch checklist for my app"
python3 prototype/orbisent_agent.py "Launch my bakery website" --task create_launch_checklist --name bakery --yes
```

See [`prototype/README.md`](prototype/README.md) for every option, the safety rules, exit codes and how to run it in
Google Colab.

### ARTIF 1.0 foundations (in progress)

ARTIF 1.0 is being rebuilt as a modular package next to the prototype. The prototype stays unchanged, as a working
reference. The package namespace is still **`orbisent`**.

- **`orbisent.types`:** immutable `Request` and `GoalSpec` types.
  - A `GoalSpec` is either *decided* (it has a task type) or *unclear* (no task type, with the reasons why).
  - Mappings are stored as an immutable `FrozenMap`.
- **`orbisent.understanding`:** deterministic goal interpreters.
  - `ExplicitInterpreter` uses a task chosen with `--task`.
  - `RuleInterpreter` uses the prototype's keyword rules.
- **An interpreter chain that refuses to guess.** If a goal is empty, unclear or ambiguous, the result says so
  instead of choosing. An invalid `--task` never falls back to keyword matching.
- **Tests comparing behaviour with Prototype 0.3,** on a fixed set of goals: same task types, same wording, same
  "explicit task wins" rule.

There is no command-line interface for ARTIF 1.0 yet. Today only its building blocks and their tests exist.

---

## Research

[`notebooks/01_train_from_scratch.ipynb`](notebooks/01_train_from_scratch.ipynb) is an **educational** experiment.

- **The model:** a tiny character-level, GPT-style Transformer (about 1.8 million parameters).
- **Training:** from random initialisation, on the public-domain *Tiny Shakespeare* text, in Google Colab. No
  pretrained weights or external AI services are used.
- **What it covers:** a character tokenizer, a train/validation split, training with cross-entropy loss, text
  generation, and saving a checkpoint.
- **What it isn't:** a production model, or a new kind of intelligence. It is a learning exercise, and a first step
  toward reproducible experiments.
- **Recorded results:** the saved outputs show one **partial** run on a Tesla T4 GPU. Training output stops at step
  900 of 3,000 (validation loss 1.67, down from 4.25 at the start). No complete run is recorded in the notebook yet.

See [`notebooks/README.md`](notebooks/README.md) for how to open and run it in Colab. Datasets and checkpoints are
never committed to this repository.

---

## Running the tests

From the repository root, with Python 3 (use `python3` if `python` isn't Python 3 on your system):

```bash
python -m unittest discover -s prototype -v   # Prototype 0.3
python -m unittest discover -s tests -v       # ARTIF 1.0 foundations (orbisent package)
```

Last run when this README was written (Python 3.13): **58 tests passed** in `prototype/`, and **62 tests passed**
in `tests/`. No third-party packages are needed.

---

## Development principles

1. **User approval before actions:** a plan is shown before anything changes.
2. **Verification before reporting success:** an unchecked result is never called a success.
3. **No deletion or overwriting of user data by default.**
4. **Honest handling of uncertainty and errors:** unclear goals are refused, not guessed, and failures are
   reported.
5. **Small, testable development steps:** each change is reviewed, tested and merged separately.
6. **Accessible AI as a long-term objective:** a free core, local computation where practical, and open tools.
7. **Reproducible research:** experiments that others can re-run and check.

---

## Immediate roadmap

These are **plans, not implemented capabilities.**

- **ARTIF Core: the Planning Engine.** It will turn a decided `GoalSpec` into a validated, step-by-step plan.
  When information is missing it will ask for it instead of guessing, and it will never touch files on its own.
  It will be built in two small pull requests: first plan data types and a validator, then template-based
  planning with tests against Prototype 0.3.
- **ARTIF Research: a reproducible baseline.** A complete, documented training run with recorded settings, seeds
  and evaluation results, so later experiments can be compared fairly.

---

## Documentation

| Document | Contents |
| --- | --- |
| [`ARCHITECTURE_1.0.md`](ARCHITECTURE_1.0.md) | The ARTIF 1.0 architecture: components, safety model, phases |
| [`VISION.md`](VISION.md) | Long-term product vision (in Russian, written under the original name) |
| [`Prototype.md`](Prototype.md) | Scope of the first prototype |
| [`prototype/README.md`](prototype/README.md) | How to use Prototype 0.3 |
| [`notebooks/README.md`](notebooks/README.md) | How to run the research notebook in Google Colab |

---

**ARTIF.** Proof that AI has a heart.
