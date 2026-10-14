import json
import os
import time

import litellm  # its price list stays local: see optimux_backend/__init__.py
from litellm import acompletion
from optimux_backend.core.config_manager import get_routing_config, litellm_key_kwargs
from optimux_backend.core.agents import get_router_prompt
from optimux_backend.core import providers

CONDUCTOR_OFF = "off"
CONDUCTOR_TIMEOUT = 10  # seconds; on timeout the request starts on the simple tier
TIERS = ("simple", "complex")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "execute_bash",
            "description": "Execute a bash command on the local Linux machine. Use it for what the user asked for; never to ask questions or echo text to the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The exact bash command to execute."}
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_markdown_memory",
            "description": (
                "CRITICAL: ONLY use this tool to SAVE a NEW preference, rule, or context. "
                "The AI context automatically injects the contents of these files, so you DO NOT need to read them manually."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_type": {"type": "string", "enum": ["personal", "professional", "project"], "description": "Which memory file to append to."},
                    "content": {"type": "string", "description": "The markdown content to append to the file."}
                },
                "required": ["file_type", "content"],
            },
        },
    }
]


def _strip_markdown(content: str) -> str:
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:-3].strip()
    elif content.startswith("```"):
        content = content[3:-3].strip()
    return content


def _parse_complexity(content: str) -> str:
    """Return 'complex' or 'simple'; anything unexpected (including the
    retired 'tool' class) counts as simple."""
    clean = _strip_markdown(content or "")
    start, end = clean.find("{"), clean.rfind("}")
    if start != -1 and end != -1:
        clean = clean[start:end + 1]
    data = json.loads(clean)
    return "complex" if data.get("complexity") == "complex" else "simple"


def _parse_skill(content: str, skills: dict):
    """The skill name from the Conductor's JSON, only if it's a known skill."""
    try:
        clean = _strip_markdown(content or "")
        start, end = clean.find("{"), clean.rfind("}")
        name = json.loads(clean[start:end + 1]).get("skill")
    except Exception:
        return None
    return name if isinstance(name, str) and name in skills else None


def conductor_thinking(model: str) -> dict:
    """The Conductor only classifies, so keep thinking as low as the model allows.
    Gemini 3 thinks by default (hidden, billed); "minimal" is its lowest level.
    Other providers don't think unless asked, so nothing is sent for them."""
    if model.startswith("gemini/"):
        return {"reasoning_effort": "minimal"}
    return {}


def _supports_json_mode(model: str) -> bool:
    try:
        return "response_format" in (litellm.get_supported_openai_params(model=model) or [])
    except Exception:
        return False


async def route_model(prompt: str) -> str:
    """Return the tier ('simple' or 'complex') for a prompt."""
    complexity = (await route_model_with_usage(prompt))[0]
    return complexity


async def route_model_with_usage(prompt: str, skills: dict | None = None) -> tuple:
    """Ask the Conductor for a tier.

    Returns (tier, conductor_model, usage, duration_ms, skill). skill is the name
    the Conductor picked from `skills` (validated), or None. usage is None when no
    call was made (Conductor off) or the call failed before returning usage.
    Bad output, errors and timeouts all fall back to 'simple'; escalation in
    main.py recovers if that was wrong.
    """
    conductor_model = get_routing_config().get("conductor_model", "")
    if not conductor_model:
        raise Exception("Routing not configured. Please assign a Conductor Model (or set it to Off) in Settings.")
    if conductor_model == CONDUCTOR_OFF:
        return "simple", CONDUCTOR_OFF, None, 0, None
    if not providers.is_available(conductor_model):
        # Known to be down (quota or repeated errors): don't spend a request
        return "simple", conductor_model, None, 0, None
    from optimux_backend.core.skills import catalog_text
    catalog = catalog_text(skills) if skills else ""

    kwargs = {
        "model": conductor_model,
        "messages": [
            {"role": "system", "content": get_router_prompt(catalog)},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "timeout": CONDUCTOR_TIMEOUT,
        **conductor_thinking(conductor_model),
        **litellm_key_kwargs(conductor_model),
    }
    if _supports_json_mode(conductor_model):
        kwargs["response_format"] = {"type": "json_object"}

    usage = None
    started = time.monotonic()
    try:
        response = await acompletion(**kwargs)
        usage = getattr(response, "usage", None)
        content = response.choices[0].message.content
        tier = _parse_complexity(content)
        skill = _parse_skill(content, skills or {})
    except Exception as e:
        info = providers.record_failure(conductor_model, e)
        print(f"Routing failed ({info.kind}: {info.summary}): {e}")
        tier = "simple"
        skill = None
    return tier, conductor_model, usage, int((time.monotonic() - started) * 1000), skill
