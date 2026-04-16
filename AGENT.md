# gen-pilot — MCP Agent Instructions

This workspace has the `gen-pilot` MCP server active. Use its tools to **prevent output stalling** when generating large documents.

## When to use gen-pilot

Before generating any document longer than ~2 pages or ~5KB:

1. Call `gp_estimate` on your content data to know its token footprint
2. Call `gp_budget` to check available headroom
3. Call `gp_plan` with the document description to get an optimal generation strategy
4. Follow the plan's steps — if it says "deferred render", use `gp_register_template` + `gp_render`

## Quick reference

| Situation | Tool |
|-----------|------|
| "How many tokens is this data?" | `gp_estimate` |
| "Can I generate this in one shot?" | `gp_budget` |
| "What's the best way to produce this document?" | `gp_plan` |
| "My generation attempt failed/stalled" | `gp_replan` |
| "I have a template to reuse" | `gp_register_template` |
| "Render data through a template" | `gp_render` |
| "What templates are available?" | `gp_list_templates` |

## Format preference order

When `gp_plan` selects a format, it follows this priority:
1. LaTeX → PDF (lowest token cost, best for structured reports)
2. Markdown → PDF via pandoc (simpler, good for light formatting)
3. HTML (universal, no compiler needed)
4. python-docx (only when .docx is a hard requirement AND content is small)

## Integration with resilient-write

If resilient-write is also active, `gp_plan` will recommend `rw_chunk_write` for chunked strategies and `rw_safe_write` for atomic final writes. The two servers are complementary, not competing.
