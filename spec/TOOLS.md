# gen-pilot Tool Specification

Version: 0.1.0

---

## Layer 1: Context Budget Awareness

### `gp_budget`

Returns the estimated context state and generation headroom.

**Parameters:** None required.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | string | `"claude-sonnet-4-6"` | Model name for token-limit lookup |
| `conversation_tokens` | int | *auto* | Override: manually supply current token count if known |

**Returns:**
```json
{
  "ok": true,
  "model": "claude-sonnet-4-6",
  "context_limit": 200000,
  "estimated_used": 145000,
  "estimated_headroom": 55000,
  "max_safe_output": 8000,
  "recommendation": "chunk",
  "suggested_chunk_size": 4000,
  "warning": null
}
```

**Recommendation values:**
- `"direct"` — headroom is ample; generate output in one shot
- `"chunk"` — headroom is tight; split output into chunks of `suggested_chunk_size`
- `"defer"` — headroom is critically low; use deferred rendering (template + data)
- `"compact_first"` — headroom is near-zero; recommend context compaction before proceeding

**Implementation notes:**
- Token estimation uses `tiktoken` with the `cl100k_base` encoding (closest available for Claude)
- The tool cannot directly read the LLM's context; it relies on either:
  1. The agent passing `conversation_tokens` explicitly, or
  2. A heuristic based on the conversation's JSONL file size (if accessible)
- Future: integration with Claude Code's internal token counter if/when exposed

---

### `gp_estimate`

Estimates the token count of a given text or structured data.

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `text` | string | Yes (or `data`) | Raw text to estimate |
| `data` | object | Yes (or `text`) | Structured data — serialized to JSON for estimation |
| `format` | string | `"raw"` | `"raw"`, `"json"`, `"latex"`, `"python"` — applies format-specific multipliers |

**Returns:**
```json
{
  "ok": true,
  "estimated_tokens": 3200,
  "format": "latex",
  "multiplier_applied": 1.3,
  "note": "LaTeX markup adds ~30% token overhead vs plain text"
}
```

**Format multipliers** (empirically derived):
- `raw`: 1.0
- `json`: 1.15 (brackets, keys, quoting overhead)
- `latex`: 1.3 (commands, environments, escaping)
- `python`: 1.4 (python-docx API boilerplate)
- `markdown`: 1.05

---

## Layer 2: Generation Planning

### `gp_plan`

Given a document description, returns an optimal generation plan.

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `description` | string | Yes | What document to produce (e.g., "14-page evaluation report with 9 sections + summary") |
| `sections` | list[string] | No | Explicit section names/titles |
| `target_format` | string | No | Desired output format: `"pdf"`, `"docx"`, `"html"`, `"markdown"` |
| `estimated_content_tokens` | int | No | How much content data the agent is holding |
| `available_headroom` | int | No | From `gp_budget` — if omitted, plan is format-only |

**Returns:**
```json
{
  "ok": true,
  "plan_id": "plan_a1b2c3",
  "strategy": "chunked_latex",
  "format_chain": ["json_data", "jinja2_template", "latex", "pdflatex", "pdf"],
  "steps": [
    {
      "step": 1,
      "action": "generate_data",
      "description": "Generate structured JSON with all 9 paper evaluations",
      "estimated_output_tokens": 3000,
      "tool_hint": "rw_safe_write or rw_chunk_write"
    },
    {
      "step": 2,
      "action": "register_template",
      "description": "Register LaTeX Jinja2 template for evaluation report",
      "estimated_output_tokens": 1500,
      "tool_hint": "gp_register_template"
    },
    {
      "step": 3,
      "action": "render",
      "description": "Render JSON data through template to .tex file",
      "estimated_output_tokens": 0,
      "tool_hint": "gp_render (deterministic, no LLM generation)"
    },
    {
      "step": 4,
      "action": "compile",
      "description": "Run pdflatex to produce PDF",
      "estimated_output_tokens": 0,
      "tool_hint": "Bash: pdflatex"
    }
  ],
  "rationale": "Content tokens (9000) + format overhead favor LaTeX (1.3x) over python-docx (1.4x). Available headroom (12000) is insufficient for single-shot generation (estimated 11700 tokens). Chunked deferred rendering avoids headroom pressure entirely.",
  "fallback": "If template rendering fails, fall back to direct chunked LaTeX generation via rw_chunk_write (4 chunks of ~3000 tokens each)."
}
```

**Strategy selection logic:**
```
if headroom > content_tokens * format_multiplier * 1.5:
    strategy = "direct"           # generate in one shot
elif headroom > content_tokens * 0.5:
    strategy = "chunked_{format}" # generate in chunks
else:
    strategy = "deferred_render"  # template + data separation
```

**Format selection logic:**
```
if target_format == "pdf":
    if latex_available: use latex
    elif pandoc_available: use markdown → pandoc
    else: use html → browser print
elif target_format == "docx":
    if content_tokens < 3000: use python-docx directly
    else: use jinja2 docx template (python-docx-template library)
```

---

### `gp_replan`

Called after a generation failure. Analyzes what went wrong and produces a revised plan.

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `plan_id` | string | Yes | The original plan that failed |
| `failure_mode` | string | Yes | `"empty_response"`, `"truncated"`, `"timeout"`, `"tool_error"` |
| `completed_steps` | list[int] | No | Which steps succeeded before failure |
| `remaining_headroom` | int | No | Current headroom estimate |

**Returns:** Same structure as `gp_plan`, with adjusted strategy (typically: smaller chunks, simpler format, or full deferred rendering).

---

## Layer 3: Deferred Rendering

### `gp_register_template`

Registers a Jinja2 template for later rendering.

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Template identifier (e.g., `"eval_report_latex"`) |
| `content` | string | Yes | Jinja2 template content |
| `format` | string | Yes | `"latex"`, `"markdown"`, `"html"`, `"text"` |
| `schema` | object | No | JSON Schema describing expected data variables |
| `description` | string | No | Human-readable description |

**Returns:**
```json
{
  "ok": true,
  "name": "eval_report_latex",
  "format": "latex",
  "variables": ["title", "author", "papers", "aggregate"],
  "stored_at": ".gen_pilot/templates/eval_report_latex.tex.j2",
  "sha256": "abc123..."
}
```

**Storage:** Templates saved to `.gen_pilot/templates/` in the workspace.

---

### `gp_render`

Renders structured data through a registered template.

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `template` | string | Yes | Template name (registered via `gp_register_template`) |
| `data` | object | Yes | JSON data matching the template's variable schema |
| `output_path` | string | Yes | Where to write the rendered file |
| `compile` | bool | `false` | If true and format is `latex`, auto-run `pdflatex` |
| `compile_cmd` | string | No | Override compile command (e.g., `"xelatex"`) |

**Returns:**
```json
{
  "ok": true,
  "rendered_path": "evaluation-report.tex",
  "rendered_bytes": 25221,
  "sha256": "def456...",
  "compiled": true,
  "compiled_path": "evaluation-report.pdf",
  "compiled_bytes": 152456
}
```

**Key design point:** `gp_render` is *deterministic* — no LLM generation tokens consumed. The LLM's job is producing the small JSON data blob; the template handles all formatting.

---

### `gp_list_templates`

Lists available templates.

**Parameters:** None.

**Returns:**
```json
{
  "ok": true,
  "templates": [
    {
      "name": "eval_report_latex",
      "format": "latex",
      "description": "Academic paper evaluation report",
      "variables": ["title", "author", "papers", "aggregate"],
      "created_at": "2026-04-16T12:00:00Z"
    }
  ]
}
```

---

## Integration Points

### With resilient-write
- `gp_render` can optionally write output through `rw_safe_write` for atomic writes + journaling
- `gp_plan` recommends `rw_chunk_write` for chunked strategies
- Template storage uses the same atomic-write pattern as resilient-write

### With Claude Code
- `gp_budget` benefits from Claude Code exposing token counts (future API)
- `gp_plan` checks for available compilers (`pdflatex`, `pandoc`, `python-docx`) via `shutil.which()`

### With context compaction
- `gp_budget` recommendation `"compact_first"` signals the agent to trigger `/compact` before generating
