# Evaluations: what has been checked, and how

This page separates four kinds of evidence. Only the last one says anything about how well **real models** do **real tasks** in Optimux, and none of that kind is published yet.

| Kind | What it proves | What it doesn't prove |
|---|---|---|
| 1. Unit tests | Optimux's own logic: prices, savings arithmetic, autonomy rules, paths, backups… | Anything about model quality |
| 2. Integration tests with a fake model | The whole request loop (WebSocket, routing, approvals, tools, metering) behaves as designed when a model answers in a scripted way | That a real model answers that way |
| 3. Install smoke tests | A fresh install on Ubuntu works end to end, with a fake model and one approved command | Anything about models, macOS, or other distributions |
| 4. Real-model task evaluations | Whether tasks get done, at what cost and time, with which models | — |

CI success (the *Tests* badge) covers kinds 1 to 3. **It is not evidence of model task quality or of real savings.**

## 1–2. The automated test suite

- `tests/`: 60 test files, run with `pytest -q`, without network access or API keys; model calls are mocked and a temporary `OPTIMUX_HOME` is used.
  - About half of the files replace the model with scripted answers (kind 2); most go through the real web server and WebSocket.
  - 6 files run the **real bubblewrap sandbox** where it works and skip those tests where it doesn't (`pytest -rs` lists the skips).
- `tests/test_guide.py` checks that defaults quoted in the Guide match the code.
- CI ([`.github/workflows/tests.yml`](../../.github/workflows/tests.yml)): pytest on Python 3.10 and 3.13 (with bubblewrap installed), and `shellcheck` on the installer and the `optimux` command.

## 3. Install smoke tests

- [`scripts/smoke-test.py`](../../scripts/smoke-test.py) starts a fake OpenAI-compatible model on `127.0.0.1`, signs in, adds that model, creates a project, sends one message, approves the command it asks for, and checks that it ran in the project (in the sandbox when it is on).
- CI runs install, status, this check, update and uninstall on fresh **Ubuntu 22.04 and 24.04**, each as a **local** and a **server** install.

Browser checks made during development (Playwright scripts against a scripted model) are not in the repository and not in CI; commits mention them in their *Validation* section.

## 4. Real-model task evaluations

**Completed and reproducible: none yet.**

| ID | Question | Status |
|---|---|---|
| [EV-001](EV-001-routing-vs-complex.md) | On a fixed set of tasks, does routing cost less than sending everything to the Complex model, for the same accepted results? | **NOT RUN** (planned; needs paid model calls) |

New evaluations use [TEMPLATE.md](TEMPLATE.md) and are added to this table, whatever the result: passed, failed or inconclusive.

### Historical observations (not reproducible)

These come from manual test runs during development. Their raw records (chat exports, project folders, model settings) were not kept, and each task ran once, so they are **observations, not results**: they can't be checked or repeated as they are. They are listed so that nothing quoted elsewhere looks better supported than it is.

| When | What was tried | What was seen | Where it is quoted |
|---|---|---|---|
| Oct 2026 (v0.4) | Plan mode vs. no plan, 6 everyday tasks (landing page, small API, theme switch, small fix, CLI tool with tests, CI setup), one run each | Plans never gave a better result and cost about 18% more in total | Guide, *Plan mode* |
| Oct 2026 (v0.4) | The same web task (mobile menu and footer year) in a copy of a repository that also held a large unrelated file (t7) and in a cleaned copy (t8) | $0.443 and 1.2M tokens vs. $0.098 and 164k tokens; both met the task's checks | Development notes only |
| Oct 2026 (v0.4) | gpt-5-nano as both the Conductor and the Simple model (t9) | The Conductor call failed on every message; nano garbled two files and the Complex model repaired them; both problems were fixed in a later commit | Development notes only |
| Oct 2026 (v0.4) | The same task after that fix (t10) | Clean edits, $0.068 in 26 seconds | Development notes only |

## The demos

The animations in the README and on the website were **recorded with a scripted model** (no real provider calls) on a demo install with **seeded usage history**. Routing, approvals, the sandbox, the test runs, file edits, git and metering in them are the real app; the costs shown are computed by the real meter from the scripted token counts, so they are typical figures, not measurements.
