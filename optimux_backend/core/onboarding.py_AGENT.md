# onboarding.py Agent Details

## File Overview
`onboarding.py` is **Getting started** (v0.6.1): the first-run checklist shown as a card on the chat page of a new install and as a list on Dashboard → Overview. It never calls a model.

## Core Functionality
- **`status()`:** every step with `id`, `title`, `why`, `done`, `target` (the Dashboard section **Go →** opens; `chat` focuses the message box), `optional`, `info`; plus `done`/`total` (required steps), `next`, `ready`, `dismissed`, `existing`, `ready_seen` and `show_in_chat`.
- **Steps, ticked from the real settings:** `models` (a model is usable: `get_models_config()["available"]`), `routing` (Simple, Complex and the Conductor or Off), `workspace` (the folder exists), `sandbox` (works and on; outside Linux it is done, with `info` saying why), `features` (the only manual one, see below), `project` (an active project), `first` (an executor or planner call in `usage`); optional `budget` and, in server mode, `totp`.
- **Card state** in the `meta` table, key `onboarding` (JSON): `started`, `existing`, `dismissed`, `features_ok`, `ready_seen`. Created by the first `state()`; `existing` is true when the install already had requests then, so an update never pops up a tutorial.
- **`act(action)`:** `features_ok`, `dismiss`, `reopen` (also clears `existing`: asked for on Overview), `ready_seen`. Unknown actions raise `OnboardingError`.
- **`note_settings_change(before, after)`:** called by `POST /api/settings`; a change to `memory_curator`, `plan_mode` or `skills_mode` counts as choosing the features.
- `show_in_chat`: not dismissed, not existing, and either not ready or "You're ready" not seen yet.

## Links & Dependencies
- Uses `store` (meta, usage), `config_manager`, `sandbox`, `budgets`, `auth`.
- `main.py`: `GET /api/onboarding`, `POST /api/onboarding {action}`, and the hook in `save_app_settings`.
- Page: `OnboardSteps` component (`onboard-steps`, `compact` on the chat card: only the next step shows its text) in web/app.js; `onboard*` state; the card and the Overview `<details>` in index.html. The page refreshes it on connect, after each reply until ready, when the Dashboard closes and when the active project changes.
- Tests: `tests/test_onboarding.py`. Guide: *First-time setup*.
