# EV-NNN: <the question, in one line>

> Copy this file to `EV-NNN-short-name.md`, fill in every section, and add a row to the table in [README.md](README.md). Publish failures and inconclusive results too. Write **NOT RUN** in *Status* until the runs are done; never fill in results you didn't measure.

## Status

NOT RUN | RUNNING | DONE (passed / failed / inconclusive)

## Question and decision rule

- **Question:** what this evaluation should answer.
- **Decided before running:** what result counts as a yes, a no, or inconclusive (for example: "routing passes at least as many checks as all-Complex, and costs at least 30% less per accepted task; fewer than 3 runs per task is inconclusive").

## Setup

| | |
|---|---|
| Optimux version | `vX.Y.Z (commit)` from Dashboard → System |
| Environment | OS, Python, local or server, sandbox on/off and its network setting |
| Models | the full model ids, provider, and settings per role (Conductor, Simple, Complex): thinking level, Base URL if any, prices used |
| Routing | Auto / pinned tier, Conductor on/off, Scout, plan mode, autonomy level, step limits |
| Memory and skills | what was in personal, professional and project memory; skills mode |

## Tasks

For each task:

- **Input:** the starting repository (URL and commit, or an attached archive) and the exact prompt.
- **Acceptance checks, written before running:** commands and their expected results (tests, a script, a browser check), and what a person checks by hand, if anything.

## Runs

One row per attempt; repeat each task the same number of times in every configuration.

| Task | Config | Run | Checks passed | Cost ($) | Duration | Model calls | Retries / provider errors | Escalations | Manual help | Notes |
|---|---|---|---|---|---|---|---|---|---|---|

- **Cost and duration** come from Optimux's own records: the task records CSV (Dashboard → Usage → Download CSV) and the cost line of each reply.
- **Manual help** is anything you did beyond approving: a correction, a hint, a reverted file. Say what.

## Results

- Totals per configuration: checks passed, cost per accepted task, time, failures.
- Whether the decision rule says yes, no or inconclusive, and why.

## Artifacts

Where the raw evidence is kept, so someone else can check it: the task records CSV, chat exports (*Copy session as Markdown*), the final diff or repository state, and the check output. Remove keys, tokens and private data first.

## Limitations

Sample size, one person judging, provider variability, prices on the day, anything that could make the result look better or worse than it is.
