# ratelimits.py Agent Details

## File Overview
`ratelimits.py` keeps the **rate limits of the user's own keys**, shown in **Dashboard → Models** (v0.3): the Prices table ("Your limits"), the chosen model in "Add a model", and each model card. No public list has them (they depend on the account and its tier; LiteLLM's `rpm`/`tpm` figures are partial and often the wrong tier), so they are **learned from calls Optimux makes anyway**. No extra request is ever sent.

## Sources
- **Reply headers** (`record_response(model, response)`): LiteLLM puts the provider's headers in `response._hidden_params["additional_headers"]` (also on a finished stream), the provider's own ones prefixed `llm_provider-`. `parse_headers` reads:
  - `x-ratelimit-(limit|remaining)-<what>[-<period>]` (OpenAI, Groq, xAI, Mistral, ...). Without a period: per minute, except Groq's request limit, which is per day.
  - `anthropic-ratelimit-(requests|tokens|input-tokens|output-tokens)-(limit|remaining)` (per minute).
  Items: `{what, per, limit, remaining}`; only items with a limit are kept. A header record replaces the model's entry.
- **Errors** (`record_error(model, exc)`): Google sends no headers; its 429 error lists `quotaId` (e.g. `GenerateRequestsPerMinutePerProjectPerModel-FreeTier`) and `quotaValue`. `parse_error` turns them into items with `hit: True` and `free_tier`; they are merged into the entry (`hit_at`). Called from `providers.record_failure` (every chat/routing failure) and from `POST /api/models/test`.

## Storage
`~/.optimux/catalog/ratelimits.json` (mode 600 via `paths.write_private`), keyed by `model_key` (the catalog's ID: `anthropic/` and `openai/` prefixes dropped). Only the parsed numbers, never a header value as such, so no key can end up there. Kept in memory (`_cache`); written at once when the limits change or an error names one, otherwise at most every `WRITE_EVERY` (60 s), since "remaining" changes on every call. `get(model)` returns `{items, seen, source, hit_at?}` or `None`; `reset_cache()` for tests.

## Safety
Every function catches its own errors and only prints a line: recording limits must never break a reply.

## Links & Dependencies
- Called by `main.py` (`stream_once`, `test_model`, the skill draft, the memory curator), `llm_client.py` (the Conductor) and `providers.record_failure`.
- Read by `catalog.models()` (`limits` per row; providers carry `limits_url`, set for Google) and `config_manager.get_models_config()` (`limits` per model card).
- Tests: `tests/test_ratelimits.py` (header formats, Google 429, privacy, LiteLLM end to end with a local OpenAI-style server, streaming and not).
