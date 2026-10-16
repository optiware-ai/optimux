# llm_client.py Agent Details

## File Overview
`llm_client.py` holds the tool schemas offered to the executors and the Conductor call that picks a tier.

## Core Functionality
- **`TOOLS`:** OpenAI-style schemas for `execute_bash` (run a shell command; the user approves each one) and `update_markdown_memory` (append to `personal`, `professional` or `project` memory). Both tiers receive them.
- **`route_model_with_usage(prompt)`:** Returns `(tier, conductor_model, usage, duration_ms)`, where `tier` is `simple` or `complex`. A successful Conductor reply goes to `ratelimits.record_response` (the key's rate limits from its headers).
  - For Gemini Conductors, `conductor_thinking()` sends `reasoning_effort="minimal"`: Gemini 3 thinks by default (hidden but billed), and the Conductor only classifies. Gemma models (also `gemini/`) get nothing: Google refuses it with "Thinking budget is not supported for this model". Other providers don't think unless asked, so nothing is sent for them.
  - A `litellm.BadRequestError` when extras were sent (`reasoning_effort`, `response_format`) is retried once without them, so a model that refuses one still routes.
  - The call uses `temperature=0`, `timeout=CONDUCTOR_TIMEOUT` (10s), the Conductor model's own API key (`litellm_key_kwargs`), plus `response_format=json_object` when LiteLLM says the model supports it.
  - Bad output, errors and timeouts fall back to `simple`; `main.py` escalates if that was wrong. The retired `tool` class also maps to `simple`.
  - **Seventh value, `note`:** why the Conductor didn't decide ("" when it did): `the call failed: <summary>`, `it is paused after errors: <reason>` (cooldown), or `its answer wasn't the JSON it was asked for` (a bad answer is not recorded as a provider failure). `main.resolve_tier` shows it in the chat, so a broken Conductor is never silent. Older callers read `result[:6]`.
  - If `conductor_model` is `"off"`, or the Conductor is in cooldown (`providers.py`), no call is made and the result is `simple`. Conductor failures are recorded so that quota errors start a cooldown. An empty Conductor setting raises.
- **`route_model(prompt)`:** Returns only the tier.

## Data Relations
- Conductor output: `{"complexity": "simple" | "complex"}`. Markdown fences are tolerated.

## Links & Dependencies
- `config_manager.get_routing_config` (Conductor model) and `agents.get_router_prompt`.
- Called by `main.py` (`resolve_tier`).
- LiteLLM uses its bundled price list: `LITELLM_LOCAL_MODEL_COST_MAP=True` is set in `optimux_backend/__init__.py`, which runs before any module imports LiteLLM (main.py imported it before this file, so every start downloaded the list from GitHub; `tests/test_no_network_at_start.py` guards it). Prices for newer models may be missing; see `usage.py`.

- **`execute_bash` description:** no longer mentions `create-repo-github.sh`; project creation is a workspace tool (`workspace.py`).

- **Plan mode (v0.4):** `route_model_with_usage(prompt, skills=None, plan_mode=False)` returns a sixth value, `plan`: true only when `plan_mode` is on and `_parse_plan` finds `"plan": true` for a complex task. With the Conductor Off, in cooldown, or on an error it is False.
- **Skill selection:** `route_model_with_usage(prompt, skills=None)` returns `(tier, conductor_model, usage, duration_ms, skill)`. The Conductor sees the skill catalog, and `_parse_skill` accepts only names that exist; anything else becomes `None`.

- **`FINISH_STEP_TOOL` (v0.4):** `finish_step(status: done|blocked, result, details?)`, offered only inside a plan step (`main.run_plan_step`), never in a normal chat.
- **`FINISH_CHECK_TOOL` (v0.4-5a):** `finish_check(verdict: passed|failed|not_verified, reason)`, given only to a plan's final check (main.py `run_final_check`).
