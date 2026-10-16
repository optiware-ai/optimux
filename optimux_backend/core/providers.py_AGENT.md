# providers.py Agent Details

## File Overview
`providers.py` classifies LLM provider errors and keeps per-model cooldowns, so Optimux stops spending requests on models that are known to be down.

## Core Functionality
- **`classify(exc) -> ProviderError`:** Sorts an error into one of three kinds, each with a short one-line `summary` for the UI.
  - `quota`: the plan limit or credit is used up, detected from markers like `insufficient_quota`, `exceeded your current quota`, `RESOURCE_EXHAUSTED` or Anthropic's `credit balance` (shown as "out of credit (credit balance too low)"), or from a retry delay over 120s. Example summary: "quota exhausted (free tier, 20/day), resets in ~6h07m".
  - `transient`: overload, short rate limit, timeout or connection error (LiteLLM exception types, or 408/429/5xx).
  - `fatal`: everything else. A missing model (404, `not_found_error`, LiteLLM `NotFoundError`) reads "model not found: check the model ID (it may be retired or misspelled)". Otherwise the summary is the provider's own `"message"` from the error's JSON when there is one (`refused (400): Thinking budget is not supported for this model.`; Gemini's first line is just `... - {`), else the first line of the error, truncated to 200 characters.
- **`parse_retry_after(text)`:** Reads Google's `"retryDelay": "22030s"` or a "retry in 6h7m10s" phrase.
- **Cooldowns:**
  - `record_failure(model, exc)` first gives the error to `ratelimits.record_error` (Google names the limit it hit), then classifies it and puts the model in cooldown: quota errors for the provider's retry delay (default 30 min), transient errors that survived their retry for 60s, fatal errors not at all.
  - `cooldown(model)` returns the reason with a live "resets in"; `is_available(model)` checks for one.
  - `active_cooldowns()` serves `/api/cooldowns`. `clear_model(model)` and `clear_models(models)` end specific cooldowns (used when a key is saved, after a successful test, and by `/api/cooldowns/clear`); `clear()` ends them all.
  - Cooldowns are process-wide and in memory, so a server restart clears them.
- **Exceptions:** `ModelUnavailable` (the model was skipped because it is in cooldown) and `ProviderFailure` (a classified failure after retry handling). Both carry `.model` and `.info`.

## Links & Dependencies
- Used by `main.py` (`call_model`, `run_with_fallback`, tier selection, `/api/cooldowns`) and `llm_client.py` (the Conductor is skipped while in cooldown, and its failures are recorded).
- Uses LiteLLM's exception types.
