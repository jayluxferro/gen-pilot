# gen-pilot — Agent Instructions

## What This Project Is

gen-pilot is an MCP server that solves the **LLM output stalling problem** — when LLM agents silently produce empty responses while attempting to generate large, complex documents.

It provides three layers of tools:
1. **Budget** — context headroom awareness (`gp_budget`, `gp_estimate`)
2. **Planning** — adaptive generation strategy (`gp_plan`, `gp_replan`)
3. **Rendering** — deferred template rendering (`gp_register_template`, `gp_render`, `gp_list_templates`)

## Origin Story

This tool was born from a real failure: an agent evaluating 9 research papers accumulated ~90K tokens of analysis data, then stalled repeatedly trying to generate a formatted Word document via python-docx. The root cause was context pressure + format complexity. Switching to LaTeX with chunked generation fixed it, but the fix was ad hoc. gen-pilot makes the fix systematic.

See `docs/ARCHITECTURE.md` for the full problem analysis and `coding_issue.md` in the parent project for the raw incident report.

## Project Structure

```
gen-pilot/
├── pyproject.toml           # Package config, dependencies, scripts
├── src/gen_pilot/
│   ├── __init__.py
│   ├── server.py            # MCP server entry point
│   └── tools/
│       ├── budget.py        # Layer 1: gp_budget, gp_estimate
│       ├── planner.py       # Layer 2: gp_plan, gp_replan
│       └── renderer.py      # Layer 3: gp_register_template, gp_render, gp_list_templates
├── tests/
│   ├── test_budget.py
│   ├── test_planner.py
│   └── test_renderer.py
├── spec/TOOLS.md            # Full tool API specification (START HERE)
├── docs/ARCHITECTURE.md     # Architecture and design rationale
├── paper/                   # arxiv paper draft
└── AGENT.md                 # Concise instructions for MCP-connected agents
```

## Development Environment

**Always use Python with `uv` package manager.**

```bash
# Install dependencies
uv sync --dev

# Run tests
uv run pytest

# Run the server
uv run gen-pilot

# Type checking
uv run mypy src/

# Linting
uv run ruff check src/ tests/
```

## Implementation Priority

1. **Start with Layer 1 (budget.py)** — `gp_estimate` first (pure function, easy to test), then `gp_budget`
2. **Then Layer 3 (renderer.py)** — `gp_register_template` and `gp_render` (Jinja2 is straightforward)
3. **Then Layer 2 (planner.py)** — `gp_plan` depends on both budget estimates and render capabilities

## Key Design Decisions

- **tiktoken for estimation**: Use `cl100k_base` encoding. Not exact for Claude, but close enough for planning.
- **Jinja2 for templates**: Standard, well-documented, supports LaTeX/Markdown/HTML.
- **Format multipliers are empirical**: Derived from the incident data. Should be validated with more samples.
- **State in `.gen_pilot/`**: Templates and plan history stored in workspace-local directory (same pattern as resilient-write's `.resilient_write/`).
- **No direct context access**: The tool cannot read the LLM's actual token count. It relies on the agent passing estimates or heuristics. This is the biggest limitation — document it clearly.

## Integration with resilient-write

gen-pilot and resilient-write are complementary:
- resilient-write handles **file I/O reliability** (atomic writes, chunking, journaling)
- gen-pilot handles **generation reliability** (budget awareness, planning, rendering)

`gp_render` should optionally delegate file writing to `rw_safe_write` when resilient-write is available. For now, use standard atomic write (tempfile → rename).

## What NOT to Do

- Don't try to intercept or modify the LLM's generation process — gen-pilot is advisory
- Don't duplicate resilient-write's chunking — recommend `rw_chunk_write` in plans instead
- Don't hardcode model token limits — use a config dict that's easy to update
