"""Layer 2 — Generation Planning tools.

gp_plan:   Given a target document description (format, sections, estimated size),
           returns a chunked generation plan: ordered steps, recommended chunk sizes,
           format selection, and template suggestion.
gp_replan: Accepts a failed/stalled generation attempt and returns a revised plan
           (smaller chunks, simpler format, or deferred-render strategy).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

from mcp.types import Tool

from gen_pilot.tools.budget import FORMAT_MULTIPLIERS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CHUNK_TOKENS = 4000

STRATEGY_DIRECT = "direct"
STRATEGY_CHUNKED = "chunked"
STRATEGY_DEFERRED = "deferred_render"

# ---------------------------------------------------------------------------
# State directory for plans
# ---------------------------------------------------------------------------


def _plans_dir() -> Path:
    d = Path(".gen_pilot") / "plans"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _generate_plan_id() -> str:
    """Generate a unique plan ID."""
    h = hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:8]
    return f"plan_{h}"


def _save_plan(plan: dict[str, Any]) -> None:
    path = _plans_dir() / f"{plan['plan_id']}.json"
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")


def _load_plan(plan_id: str) -> dict[str, Any] | None:
    path = _plans_dir() / f"{plan_id}.json"
    if not path.exists():
        return None
    result: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return result


# ---------------------------------------------------------------------------
# Format selection helpers
# ---------------------------------------------------------------------------


def _has_compiler(name: str) -> bool:
    return shutil.which(name) is not None


def _select_format_chain(target_format: str | None, content_tokens: int) -> list[str]:
    """Select the optimal format chain based on target and available tools."""
    if target_format == "pdf":
        if _has_compiler("pdflatex") or _has_compiler("xelatex"):
            return ["json_data", "jinja2_template", "latex", "pdflatex", "pdf"]
        elif _has_compiler("pandoc"):
            return ["json_data", "jinja2_template", "markdown", "pandoc", "pdf"]
        else:
            return ["json_data", "jinja2_template", "html", "browser_print", "pdf"]
    elif target_format == "docx":
        if content_tokens < 3000:
            return ["python-docx", "docx"]
        else:
            return ["json_data", "jinja2_template", "docx"]
    elif target_format == "html":
        return ["json_data", "jinja2_template", "html"]
    elif target_format == "markdown":
        return ["json_data", "jinja2_template", "markdown"]
    # Default: markdown
    return ["json_data", "jinja2_template", "markdown"]


def _infer_output_format(format_chain: list[str]) -> str:
    """Infer the intermediate output format from the chain (for multiplier lookup)."""
    for fmt in ("latex", "markdown", "html", "python"):
        if fmt in format_chain:
            return fmt
    return "raw"


# ---------------------------------------------------------------------------
# Strategy selection
# ---------------------------------------------------------------------------


def _select_strategy(
    content_tokens: int,
    headroom: int | None,
    format_multiplier: float,
) -> str:
    """Select generation strategy based on headroom and content size."""
    if headroom is None:
        # No headroom info — be conservative with large content
        if content_tokens > 8000:
            return STRATEGY_CHUNKED
        return STRATEGY_DIRECT

    estimated_output = content_tokens * format_multiplier
    if headroom > estimated_output * 1.5:
        return STRATEGY_DIRECT
    elif headroom > content_tokens * 0.5:
        return STRATEGY_CHUNKED
    else:
        return STRATEGY_DEFERRED


# ---------------------------------------------------------------------------
# Step generation
# ---------------------------------------------------------------------------


def _generate_steps(
    strategy: str,
    format_chain: list[str],
    sections: list[str] | None,
    content_tokens: int,
    format_multiplier: float,
) -> list[dict[str, Any]]:
    """Generate execution steps for the plan."""
    steps: list[dict[str, Any]] = []
    step_num = 0

    if strategy == STRATEGY_DIRECT:
        step_num += 1
        steps.append({
            "step": step_num,
            "action": "generate",
            "description": "Generate the complete document in one shot",
            "estimated_output_tokens": int(content_tokens * format_multiplier),
            "tool_hint": "Direct generation",
        })
        return steps

    if strategy == STRATEGY_DEFERRED:
        # Step 1: Generate structured data
        step_num += 1
        steps.append({
            "step": step_num,
            "action": "generate_data",
            "description": "Generate structured JSON with all content data",
            "estimated_output_tokens": content_tokens,
            "tool_hint": "rw_safe_write or rw_chunk_write",
        })
        # Step 2: Register template
        step_num += 1
        steps.append({
            "step": step_num,
            "action": "register_template",
            "description": "Register Jinja2 template for the target format",
            "estimated_output_tokens": int(content_tokens * (format_multiplier - 1) * 0.5),
            "tool_hint": "gp_register_template",
        })
        # Step 3: Render
        step_num += 1
        steps.append({
            "step": step_num,
            "action": "render",
            "description": "Render JSON data through template (deterministic, no LLM tokens)",
            "estimated_output_tokens": 0,
            "tool_hint": "gp_render",
        })
        # Step 4: Compile (if applicable)
        if any(c in format_chain for c in ("pdflatex", "xelatex", "pandoc")):
            step_num += 1
            compiler = next(c for c in format_chain if c in ("pdflatex", "xelatex", "pandoc"))
            steps.append({
                "step": step_num,
                "action": "compile",
                "description": f"Run {compiler} to produce final output",
                "estimated_output_tokens": 0,
                "tool_hint": f"Bash: {compiler}",
            })
        return steps

    # STRATEGY_CHUNKED
    if sections:
        # One chunk per section
        for section in sections:
            step_num += 1
            chunk_tokens = int((content_tokens / len(sections)) * format_multiplier)
            steps.append({
                "step": step_num,
                "action": "generate_chunk",
                "description": f"Generate section: {section}",
                "estimated_output_tokens": chunk_tokens,
                "tool_hint": "rw_chunk_write",
            })
    else:
        # Auto-split into chunks
        total_output = int(content_tokens * format_multiplier)
        chunk_size = min(DEFAULT_CHUNK_TOKENS, total_output)
        num_chunks = max(1, (total_output + chunk_size - 1) // chunk_size)
        for i in range(num_chunks):
            step_num += 1
            steps.append({
                "step": step_num,
                "action": "generate_chunk",
                "description": f"Generate chunk {i + 1} of {num_chunks}",
                "estimated_output_tokens": chunk_size,
                "tool_hint": "rw_chunk_write",
            })

    # Final assembly step
    step_num += 1
    steps.append({
        "step": step_num,
        "action": "assemble",
        "description": "Assemble chunks into final document",
        "estimated_output_tokens": 0,
        "tool_hint": "rw_chunk_compose",
    })
    return steps


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def create_plan(
    description: str,
    sections: list[str] | None = None,
    target_format: str | None = None,
    estimated_content_tokens: int | None = None,
    available_headroom: int | None = None,
) -> dict[str, Any]:
    """Create a generation plan."""
    content_tokens = estimated_content_tokens or 2000  # reasonable default
    format_chain = _select_format_chain(target_format, content_tokens)
    output_fmt = _infer_output_format(format_chain)
    multiplier = FORMAT_MULTIPLIERS.get(output_fmt, 1.0)

    strategy_base = _select_strategy(content_tokens, available_headroom, multiplier)

    # Build strategy name with format suffix
    if strategy_base == STRATEGY_DEFERRED:
        strategy = "deferred_render"
    elif strategy_base == STRATEGY_CHUNKED:
        strategy = f"chunked_{output_fmt}"
    else:
        strategy = STRATEGY_DIRECT

    steps = _generate_steps(strategy_base, format_chain, sections, content_tokens, multiplier)

    total_gen_tokens = sum(s["estimated_output_tokens"] for s in steps)
    rationale_parts = [
        f"Content: ~{content_tokens} tokens, format: {output_fmt} ({multiplier}x multiplier)."
    ]
    if available_headroom is not None:
        rationale_parts.append(f"Headroom: {available_headroom} tokens.")
        rationale_parts.append(f"Estimated output: {total_gen_tokens} tokens.")
    rationale_parts.append(f"Strategy: {strategy}.")

    plan_id = _generate_plan_id()
    plan: dict[str, Any] = {
        "ok": True,
        "plan_id": plan_id,
        "strategy": strategy,
        "format_chain": format_chain,
        "steps": steps,
        "rationale": " ".join(rationale_parts),
        "fallback": _generate_fallback(strategy_base, output_fmt, content_tokens),
    }
    _save_plan(plan)
    return plan


def _generate_fallback(strategy: str, fmt: str, content_tokens: int) -> str:
    if strategy == STRATEGY_DIRECT:
        return (
            f"If generation stalls, switch to chunked_{fmt} "
            f"strategy with ~{DEFAULT_CHUNK_TOKENS}-token chunks."
        )
    elif strategy == STRATEGY_CHUNKED:
        return (
            "If chunks stall, switch to deferred_render "
            "(template + data separation)."
        )
    else:
        chunk_count = max(2, content_tokens // DEFAULT_CHUNK_TOKENS)
        return (
            "If template rendering fails, fall back to direct "
            f"chunked generation via rw_chunk_write ({chunk_count} chunks)."
        )


def replan(
    plan_id: str,
    failure_mode: str,
    completed_steps: list[int] | None = None,
    remaining_headroom: int | None = None,
) -> dict[str, Any]:
    """Revise a failed plan based on the failure mode."""
    original = _load_plan(plan_id)
    if original is None:
        return {"ok": False, "error": f"Plan '{plan_id}' not found"}

    completed = set(completed_steps or [])
    orig_steps = original.get("steps", [])
    orig_strategy = original.get("strategy", "direct")

    # Determine new strategy based on failure
    if failure_mode == "empty_response":
        # Downgrade: direct → chunked → deferred
        new_strategy_base = (
            STRATEGY_CHUNKED if "direct" in orig_strategy else STRATEGY_DEFERRED
        )
    elif failure_mode == "truncated":
        # Keep same strategy but halve chunk size
        new_strategy_base = STRATEGY_CHUNKED
    elif failure_mode == "timeout":
        new_strategy_base = STRATEGY_DEFERRED
    else:
        # tool_error or unknown — try deferred
        new_strategy_base = STRATEGY_DEFERRED

    # Estimate remaining tokens from original plan
    remaining_tokens = sum(
        s.get("estimated_output_tokens", 0)
        for s in orig_steps
        if s["step"] not in completed
    )

    format_chain = original.get("format_chain", ["markdown"])
    output_fmt = _infer_output_format(format_chain)
    multiplier = FORMAT_MULTIPLIERS.get(output_fmt, 1.0)

    # For truncated: halve chunk size
    content_tokens = max(1000, int(remaining_tokens / multiplier))
    sections = None

    if failure_mode == "truncated" and new_strategy_base == STRATEGY_CHUNKED:
        # Double the number of chunks (halve chunk size)
        chunk_size = max(500, DEFAULT_CHUNK_TOKENS // 2)
        num_chunks = max(2, (remaining_tokens + chunk_size - 1) // chunk_size)
        sections = [f"chunk_{i+1}" for i in range(num_chunks)]

    new_steps = _generate_steps(
        new_strategy_base, format_chain, sections, content_tokens, multiplier
    )

    # Renumber steps to continue from where we left off
    max_completed = max(completed) if completed else 0
    for i, step in enumerate(new_steps):
        step["step"] = max_completed + i + 1

    if new_strategy_base == STRATEGY_DEFERRED:
        strategy = "deferred_render"
    elif new_strategy_base == STRATEGY_CHUNKED:
        strategy = f"chunked_{output_fmt}"
    else:
        strategy = "direct"

    new_plan_id = _generate_plan_id()
    plan: dict[str, Any] = {
        "ok": True,
        "plan_id": new_plan_id,
        "original_plan_id": plan_id,
        "strategy": strategy,
        "format_chain": format_chain,
        "completed_steps": sorted(completed),
        "steps": new_steps,
        "rationale": (
            f"Replanning after '{failure_mode}'. "
            f"Original strategy '{orig_strategy}' → '{strategy}'. "
            f"Completed steps: {sorted(completed) if completed else 'none'}. "
            f"Remaining output: ~{remaining_tokens} tokens."
        ),
        "fallback": _generate_fallback(new_strategy_base, output_fmt, content_tokens),
    }
    _save_plan(plan)
    return plan


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="gp_plan",
        description=(
            "Create an optimal generation plan for a document. "
            "Selects strategy (direct/chunked/deferred), format chain, "
            "and step-by-step execution instructions."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "What document to produce",
                },
                "sections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit section names/titles",
                },
                "target_format": {
                    "type": "string",
                    "enum": ["pdf", "docx", "html", "markdown"],
                    "description": "Desired output format",
                },
                "estimated_content_tokens": {
                    "type": "integer",
                    "description": "How much content data the agent is holding",
                },
                "available_headroom": {
                    "type": "integer",
                    "description": "From gp_budget — available token headroom",
                },
            },
            "required": ["description"],
        },
    ),
    Tool(
        name="gp_replan",
        description=(
            "Revise a failed generation plan. Analyzes the failure mode "
            "(empty_response, truncated, timeout, tool_error) and returns "
            "a new plan with adjusted strategy."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "plan_id": {
                    "type": "string",
                    "description": "The original plan that failed",
                },
                "failure_mode": {
                    "type": "string",
                    "enum": ["empty_response", "truncated", "timeout", "tool_error"],
                    "description": "What went wrong",
                },
                "completed_steps": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Steps that succeeded before failure",
                },
                "remaining_headroom": {
                    "type": "integer",
                    "description": "Current headroom estimate",
                },
            },
            "required": ["plan_id", "failure_mode"],
        },
    ),
]


async def handle_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Handle a planner tool call."""
    if name == "gp_plan":
        return create_plan(
            description=arguments["description"],
            sections=arguments.get("sections"),
            target_format=arguments.get("target_format"),
            estimated_content_tokens=arguments.get("estimated_content_tokens"),
            available_headroom=arguments.get("available_headroom"),
        )
    elif name == "gp_replan":
        return replan(
            plan_id=arguments["plan_id"],
            failure_mode=arguments["failure_mode"],
            completed_steps=arguments.get("completed_steps"),
            remaining_headroom=arguments.get("remaining_headroom"),
        )
    return {"ok": False, "error": f"Unknown planner tool: {name}"}
