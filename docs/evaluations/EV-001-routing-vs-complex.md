# EV-001: Does routing cost less than all-Complex, for the same accepted results?

## Status

**NOT RUN.** It needs paid model calls, and none have been made for it. No result below exists yet.

## Question and decision rule

- **Question:** on a fixed set of coding tasks, how does Optimux with routing (Conductor, Simple, Complex) compare with the same Optimux sending every request to the Complex model, in accepted results, cost and time?
- **Why:** *What routing saved* in Dashboard → Performance is an **estimate**: it prices the same tokens on the Complex model. A strong model may need fewer (or more) tokens and steps, and the cheap model may produce work that has to be redone. Only a run like this measures the difference for results that pass the same checks.
- **Decided before running:**
  - Routing is **cheaper for the same quality** if it passes at least as many acceptance checks as all-Complex, and its total cost per accepted task is lower.
  - It is **cheaper but worse** if it costs less but passes fewer checks; report both numbers.
  - **Inconclusive** with fewer than 3 runs per task and configuration, or if provider errors interrupt more than one run in ten.

## Setup (to fill in when run)

- Optimux version and commit, environment, sandbox on.
- Models: the user's usual Simple and Complex models and Conductor, with full ids, prices and thinking levels.
- Configuration A, **routed:** Auto-Router, Conductor on, Scout as configured, plan mode off.
- Configuration B, **all-Complex:** the same models, every request pinned to Complex (`/complex`, or the Complex model forced for the chat).
- Autonomy: Supervised in both, every approval given without changes (approval waits don't count towards working time).

## Tasks (to fix before running)

Six tasks of the kinds used during development, each in its own fresh copy of a small public repository, with acceptance checks written first:

1. A small fix that a failing test shows (check: the test suite passes).
2. A feature in a small API, with a test (check: the new and old tests pass).
3. A responsive menu and a footer change on a static page (check: a browser script at 390 and 1280 px).
4. A theme switch in a front end (check: a browser script).
5. A command-line tool with tests (check: its tests and two sample invocations).
6. A CI workflow file (check: it parses, and a local runner or a dry run accepts it).

## Procedure

1. For each task, configuration and run (3 runs each): copy the starting repository, open it as a project, start a new chat, send the fixed prompt, approve every command and edit as asked, and stop when the model says it's done or after a fixed limit of messages.
2. Run the acceptance checks; record pass or fail without fixing anything by hand. Any manual help is recorded in the run's row.
3. Export the task records CSV and the chat (*Copy session as Markdown*); keep the final diff.
4. Fill in the table in [TEMPLATE.md](TEMPLATE.md)'s format, then the results and the decision.

## Runs

None. **NOT RUN.**
