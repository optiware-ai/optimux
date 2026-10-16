# catalog.py Agent Details

## File Overview
`catalog.py` is the model catalog behind **Dashboard → Models** (v0.3): providers, their chat models, prices per 1M tokens, context size, tool and reasoning support. **Offline by default**: everything comes from the price list LiteLLM ships with (`litellm.model_cost`, read locally because `optimux_backend/__init__.py` sets `LITELLM_LOCAL_MODEL_COST_MAP`). Online only when the setting `online_catalog` is on **and** the user clicks Refresh; nothing runs in the background.

## Providers (`PROVIDERS`)
OpenAI, Anthropic, Google Gemini, xAI, Mistral, DeepSeek, Groq, OpenRouter, Ollama. Each has: `prefix` of LiteLLM model IDs (`""` for bare IDs like `gpt-5`), `env` (the key variable; `None` = local, no key), `models_url` + `kind` (response format: OpenAI-style `data[].id`, `anthropic`, `gemini` (`models[].name`, only `generateContent`), `openrouter` (also live prices), `ollama` (`/api/tags` on `OLLAMA_API_BASE`, default `http://localhost:11434`)), `keys_url` (where to get a key). "Other" in the UI is the old free form for any LiteLLM ID.

## Offline list
- `offline_models(pid)`: chat entries of that provider, fine-tune templates dropped, IDs given the prefix when the list key lacks it (`model_id`), one row per ID, cheapest input first. Row: `id, input, output, cached` (USD per 1M), `context, max_output, tools, reasoning, deprecated` (date), `retired` (date passed; on the day itself a model may still answer) and `unpriced` and `zero_listed`: a cloud model listed at 0 in and 0 out is ambiguous (a free model such as Google's Gemma, or a switched-off experimental one), so its prices are `None` and it sorts last; local models (no key) and OpenRouter `:free` models stay at 0 (free). In `models()`, a `zero_listed` model that the provider's list offers to the key gets prices 0 and `free: True` (sorted first). New models from a provider refresh are `unpriced` when the provider gave no price. `config_manager.price_source` reads a $0 listing as `zero`.
- `models(pid)`: the offline rows merged with the last provider answer (`catalog/models-<pid>.json`): `available` per row, and models the price list doesn't know yet added as `new` (price unknown; OpenRouter gives prices). Plus `key_saved`, `models_refreshed`, `online`.
- `summary()`: providers with `key_saved` (the default `env` variable is set), refresh times, `prices_refreshed`.

## Online (`_require_online`; `CatalogError` messages are shown to the user)
- `refresh_models(pid)`: GET the provider's model list **with the user's own saved key** (Bearer; `x-api-key` + `anthropic-version` for Anthropic; `x-goog-api-key` for Gemini). Needs the key (OpenRouter's list is public). Saved to `catalog/models-<pid>.json` (ids, fetch time, OpenRouter prices; never the key). Ollama is on this machine: allowed even with the online catalog off.
- `refresh_prices()`: GET `litellm.model_cost_map_url` (LiteLLM's public list on GitHub; providers don't publish prices in their APIs), at most 40 MB, validated by `_clean_prices` (only `PRICE_KEYS`, right types, numbers 0..1e9, at least 100 entries; else the current prices stay), saved to `catalog/prices.json`, then `_apply`.
- `_apply(entries)`: merges straight into `litellm.model_cost` (not `litellm.register_model`, which logs a line per unknown model and took 2 s), adds new bare OpenAI/Anthropic IDs to LiteLLM's provider sets so they route, and calls LiteLLM's `_invalidate_model_cost_lowercase_map`. So **cost metering uses the refreshed prices**; prices set by hand on a model still win (`usage.py`).
- `load_saved_prices()`: at server start (main.py), the list downloaded earlier is applied again, with no network.
- HTTP: `httpx.get` with a 20 s timeout and the usual proxy variables; a request that carries a key never follows a redirect (httpx would drop only `Authorization`, not `x-api-key` / `x-goog-api-key`); 401/403 → "The provider refused the API key"; connection errors name the host and suggest checking `HTTPS_PROXY`.

## Files
`~/.optimux/catalog/` (mode 700): `prices.json`, `models-<provider>.json` (mode 600, `paths.write_private`).

## Links & Dependencies
- `config_manager.py` (`online_catalog` setting; `get_settings`), `paths.py`, LiteLLM, httpx (a LiteLLM dependency).
- Used by `main.py`: `GET /api/catalog`, `GET /api/catalog/{provider}`, `POST /api/catalog/online` `{on}`, `POST /api/catalog/refresh` `{what: prices|models, provider}` (422 with the message on `CatalogError`).
- Tests: `tests/test_catalog.py` (every HTTP call mocked; a real call fails the test).
