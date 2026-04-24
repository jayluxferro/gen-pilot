# gen-pilot

**Context-aware generation planning and deferred document rendering for LLM coding agents.**

gen-pilot is an MCP server that prevents LLM output stalling — the silent failure mode where agents produce empty responses when attempting to generate large, complex documents.

## The Problem

LLM coding agents fail silently when generating large outputs:

```
Agent has 90K tokens of analysis data
Agent tries to generate a 500-line python-docx script
Agent produces... nothing. Empty response. No error.
Agent retries. Empty again. Tokens burned.
```

This happens because:
1. The model has no awareness of its remaining generation capacity
2. Complex formats (python-docx, HTML) inflate token costs beyond headroom
3. Content generation and format rendering are conflated in a single generation step

## The Solution

gen-pilot provides three layers of tools:

| Layer | Tools | Purpose |
|-------|-------|---------|
| **Budget** | `gp_budget`, `gp_estimate` | Know your limits before generating |
| **Planning** | `gp_plan`, `gp_replan` | Choose the right strategy and format |
| **Rendering** | `gp_register_template`, `gp_render` | Separate content from formatting |

### Quick Example

```
# Before: LLM generates 15K tokens of python-docx code → stalls

# After with gen-pilot:
1. gp_budget()        → "You have 12K tokens of headroom"
2. gp_plan(...)       → "Use deferred rendering with LaTeX template"
3. LLM generates 3K tokens of structured JSON data
4. gp_render(...)     → Template produces .tex → pdflatex → .pdf (0 LLM tokens)
```

## Installation

```bash
# Using uv (recommended)
uv pip install gen-pilot

# Or from source
git clone https://github.com/jayluxferro/gen-pilot
cd gen-pilot
uv sync
```

## Usage with Claude Code

Add to your Claude Code MCP config:

```json
{
  "mcpServers": {
    "gen-pilot": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/gen-pilot", "gen-pilot"]
    }
  }
}
```

## Transports

gen-pilot supports three transport modes via the `--transport` flag.

### stdio (default)

Standard MCP stdio transport — used by Claude Desktop, Claude Code, and the MCP CLI.

```bash
gen-pilot
# or explicitly:
gen-pilot --transport stdio
```

### SSE (Server-Sent Events)

Classic HTTP + SSE transport. Exposes two endpoints:
- `GET /sse` — clients open the SSE stream here
- `POST /messages/` — clients POST messages here

```bash
gen-pilot --transport sse
gen-pilot --transport sse --host 0.0.0.0 --port 9000
```

MCP config for SSE:

```json
{
  "mcpServers": {
    "gen-pilot": {
      "url": "http://127.0.0.1:8000/sse"
    }
  }
}
```

### Streamable HTTP

Modern MCP Streamable HTTP transport (MCP spec ≥ 2025-03-26). Single endpoint at `/mcp`.

```bash
gen-pilot --transport streamable-http
gen-pilot --transport streamable-http --host 0.0.0.0 --port 9000
```

MCP config for Streamable HTTP:

```json
{
  "mcpServers": {
    "gen-pilot": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

### HTTP transport dependencies

SSE and Streamable HTTP require `starlette` and `uvicorn`:

```bash
uv pip install "gen-pilot[http]"
```

## Development

```bash
uv sync --dev
uv run pytest
uv run mypy src/
uv run ruff check src/ tests/
```

## Origin

Born from a real incident where an agent burned ~50K tokens producing empty responses while trying to generate a Word document. Full incident analysis in `docs/ARCHITECTURE.md`.

## Related

- [resilient-write](https://github.com/jayluxferro/resilient-write) — Durable file I/O layer (complementary to gen-pilot)
- [MCP Specification](https://modelcontextprotocol.io) — The protocol gen-pilot implements

## License

MIT
