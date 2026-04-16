"""Layer 1 — Context Budget tools.

gp_budget:   Returns estimated context usage, remaining headroom, and recommended
             max output size for the next generation step.
gp_estimate: Estimates token count for a given text or structured data blob,
             so the agent can decide whether to checkpoint or chunk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import tiktoken
from mcp.types import Tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_LIMITS: dict[str, int] = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-opus-4-5": 200_000,
    "claude-3-opus": 200_000,
    "claude-3-sonnet": 200_000,
    "claude-3-haiku": 200_000,
    "claude-3.5-sonnet": 200_000,
    "claude-3.5-haiku": 200_000,
}

DEFAULT_MODEL = "claude-sonnet-4-6"

FORMAT_MULTIPLIERS: dict[str, float] = {
    "raw": 1.0,
    "markdown": 1.05,
    "json": 1.15,
    "latex": 1.3,
    "python": 1.4,
}

FORMAT_NOTES: dict[str, str] = {
    "raw": "No format overhead applied",
    "markdown": "Markdown markup adds ~5% token overhead vs plain text",
    "json": "JSON brackets, keys, and quoting add ~15% token overhead",
    "latex": "LaTeX markup adds ~30% token overhead vs plain text",
    "python": "python-docx API boilerplate adds ~40% token overhead",
}

# Max safe output as fraction of headroom (conservative)
MAX_SAFE_OUTPUT_RATIO = 0.6

# Thresholds for recommendations (as fraction of context_limit)
DIRECT_THRESHOLD = 0.25      # >25% headroom → direct
CHUNK_THRESHOLD = 0.10       # >10% headroom → chunk
DEFER_THRESHOLD = 0.05       # >5% headroom → defer
# Below DEFER_THRESHOLD → compact_first

# ---------------------------------------------------------------------------
# Config override from .gen_pilot/config.json
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(".gen_pilot") / "config.json"


def _load_config_multipliers() -> dict[str, float]:
    """Load format multiplier overrides from .gen_pilot/config.json."""
    if _CONFIG_PATH.exists():
        try:
            cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            overrides = cfg.get("format_multipliers", {})
            if isinstance(overrides, dict):
                merged = dict(FORMAT_MULTIPLIERS)
                merged.update(overrides)
                return merged
        except (json.JSONDecodeError, OSError):
            pass
    return FORMAT_MULTIPLIERS


def get_multipliers() -> dict[str, float]:
    """Return format multipliers, with config overrides applied."""
    return _load_config_multipliers()


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_encoder: tiktoken.Encoding | None = None


def _get_encoder() -> tiktoken.Encoding:
    global _encoder
    if _encoder is None:
        _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


def count_tokens(text: str) -> int:
    """Count tokens using cl100k_base encoding."""
    return len(_get_encoder().encode(text))


# ---------------------------------------------------------------------------
# Core logic (testable without MCP)
# ---------------------------------------------------------------------------


def estimate_tokens(
    text: str | None = None,
    data: Any = None,
    fmt: str = "raw",
) -> dict[str, Any]:
    """Estimate token count for text or structured data with format multiplier."""
    if text is None and data is None:
        return {"ok": False, "error": "Either 'text' or 'data' must be provided"}

    source = text if text is not None else json.dumps(data, ensure_ascii=False, indent=2)
    raw_tokens = count_tokens(source)
    multiplier = get_multipliers().get(fmt, 1.0)
    estimated = int(raw_tokens * multiplier)
    note = FORMAT_NOTES.get(fmt, f"Custom format multiplier {multiplier}x applied")

    return {
        "ok": True,
        "estimated_tokens": estimated,
        "raw_tokens": raw_tokens,
        "format": fmt,
        "multiplier_applied": multiplier,
        "note": note,
    }


def compute_budget(
    model: str = DEFAULT_MODEL,
    conversation_tokens: int | None = None,
) -> dict[str, Any]:
    """Compute context budget, headroom, and generation recommendation."""
    context_limit = MODEL_LIMITS.get(model, 200_000)

    if conversation_tokens is None:
        return {
            "ok": True,
            "model": model,
            "context_limit": context_limit,
            "estimated_used": None,
            "estimated_headroom": None,
            "max_safe_output": None,
            "recommendation": None,
            "suggested_chunk_size": None,
            "warning": (
                "Cannot auto-estimate token usage. "
                "Pass conversation_tokens for a recommendation."
            ),
        }

    estimated_headroom = max(0, context_limit - conversation_tokens)
    headroom_ratio = estimated_headroom / context_limit if context_limit > 0 else 0
    max_safe_output = int(estimated_headroom * MAX_SAFE_OUTPUT_RATIO)

    if headroom_ratio > DIRECT_THRESHOLD:
        recommendation = "direct"
        suggested_chunk_size = None
    elif headroom_ratio > CHUNK_THRESHOLD:
        recommendation = "chunk"
        suggested_chunk_size = max(1000, max_safe_output // 2)
    elif headroom_ratio > DEFER_THRESHOLD:
        recommendation = "defer"
        suggested_chunk_size = None
    else:
        recommendation = "compact_first"
        suggested_chunk_size = None

    warning = None
    if headroom_ratio < DEFER_THRESHOLD:
        warning = (
            f"Critical: only {estimated_headroom} tokens "
            f"(~{headroom_ratio:.1%}) headroom remaining. "
            "Recommend compacting context before any generation."
        )
    elif headroom_ratio < CHUNK_THRESHOLD:
        warning = (
            f"Low headroom: {estimated_headroom} tokens (~{headroom_ratio:.1%}). "
            "Use deferred rendering to avoid stalling."
        )

    return {
        "ok": True,
        "model": model,
        "context_limit": context_limit,
        "estimated_used": conversation_tokens,
        "estimated_headroom": estimated_headroom,
        "max_safe_output": max_safe_output,
        "recommendation": recommendation,
        "suggested_chunk_size": suggested_chunk_size,
        "warning": warning,
    }


# ---------------------------------------------------------------------------
# Tool definitions (used by server.py for centralized registration)
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="gp_estimate",
        description=(
            "Estimate token count for text or structured data. "
            "Applies format-specific multipliers (raw, json, latex, python, markdown) "
            "to predict actual generation cost."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Raw text to estimate tokens for",
                },
                "data": {
                    "description": "Structured data (serialized to JSON for estimation)",
                },
                "format": {
                    "type": "string",
                    "enum": ["raw", "json", "latex", "python", "markdown"],
                    "default": "raw",
                    "description": "Output format — applies format-specific multiplier",
                },
            },
        },
    ),
    Tool(
        name="gp_budget",
        description=(
            "Returns estimated context state and generation headroom. "
            "Recommends strategy: direct, chunk, defer, or compact_first."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "default": DEFAULT_MODEL,
                    "description": "Model name for token-limit lookup",
                },
                "conversation_tokens": {
                    "type": "integer",
                    "description": "Current conversation token count (if known)",
                },
            },
        },
    ),
]


async def handle_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Handle a budget tool call. Returns result dict."""
    if name == "gp_estimate":
        return estimate_tokens(
            text=arguments.get("text"),
            data=arguments.get("data"),
            fmt=arguments.get("format", "raw"),
        )
    elif name == "gp_budget":
        return compute_budget(
            model=arguments.get("model", DEFAULT_MODEL),
            conversation_tokens=arguments.get("conversation_tokens"),
        )
    return {"ok": False, "error": f"Unknown budget tool: {name}"}
