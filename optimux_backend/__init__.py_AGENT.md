# AGENT Context: optimux_backend/__init__.py

## Purpose
Runs before any other module of the package (the server, `admin`, the tests), so it holds settings that other libraries read when they are **imported**.

## Current content
- `LITELLM_LOCAL_MODEL_COST_MAP=True`: LiteLLM uses the price list it ships with instead of downloading it from GitHub on import. Before v0.2, `main.py` imported LiteLLM before `llm_client.py` set this, so every start made that download; without a network (no proxy in the service, an offline server) each start waited over a minute.

## Rules
- Keep it tiny: only environment settings, no imports of Optimux modules (it would load them before `paths.prepare_home()` runs).
- `tests/test_no_network_at_start.py` imports `optimux_backend.main` and `optimux_backend.admin` in a fresh Python and fails on any connection or DNS lookup to a non-local host.
