# gen-pilot Architecture

## Problem Statement

LLM coding agents silently fail when generating large, complex outputs. The failure mode is **output stalling** — the model produces empty responses without any error signal. This wastes tokens, frustrates users, and leaves no structured recovery path.

### Root Cause Analysis (from real incident, April 2026)

An agent was asked to produce a 14-page Word document evaluating 9 research papers. It had all the data (9 agent analyses, ~90K tokens) and a clear plan. It failed to produce any output across 5+ consecutive attempts.

**Three compounding factors:**

1. **Format complexity**: python-docx requires imperative Python code (~500 LOC for 14 pages). The code-to-content ratio is ~60% boilerplate, 40% actual data. This makes the generation task much larger than the content warrants.

2. **Context exhaustion**: The 90K tokens of analysis data + system prompts + tool definitions left insufficient headroom for generating a large output. The model's effective generation capacity was ~8K tokens, but the required output was ~15K.

3. **No metacognition**: The model had no way to know it was about to stall. It would *plan* the output in its thinking block, then fail to *emit* the tool call. No error. No signal. Just silence.

**Resolution**: Switching to LaTeX (declarative, lower token overhead) with chunked generation (4 × ~6K chunks via resilient-write) produced the document on the first attempt.

## Three-Layer Architecture

gen-pilot addresses each root cause with a dedicated layer:

```
┌─────────────────────────────────────────────────┐
│                   LLM Agent                      │
│  (Claude Code, Cursor, Aider, etc.)              │
└────────────┬──────────────┬──────────────┬──────┘
             │              │              │
             ▼              ▼              ▼
┌────────────────┐ ┌───────────────┐ ┌──────────────┐
│  Layer 1       │ │  Layer 2      │ │  Layer 3     │
│  BUDGET        │ │  PLANNING     │ │  RENDERING   │
│                │ │               │ │              │
│  gp_budget     │ │  gp_plan      │ │  gp_register │
│  gp_estimate   │ │  gp_replan    │ │  gp_render   │
│                │ │               │ │  gp_list     │
│                │ │               │ │              │
│  "Can I?"      │ │  "How should  │ │  "Do it      │
│                │ │   I?"         │ │   without me" │
└────────────────┘ └───────────────┘ └──────────────┘
      ↕                   ↕                  ↕
  Addresses:          Addresses:         Addresses:
  Context             Format             LLM generation
  exhaustion          complexity         bottleneck
```

### Layer 1: Budget (Metacognition)

**Problem**: The model doesn't know its own limits.  
**Solution**: Give it a tool that estimates context usage and remaining headroom.

- `gp_estimate`: "This data is ~3200 tokens in LaTeX format"
- `gp_budget`: "You have ~12K tokens of headroom; recommend chunked output"

**Key limitation**: gen-pilot cannot directly read the LLM's context. It relies on:
- The agent explicitly reporting token counts
- Heuristic estimation from conversation JSONL file size
- Format-specific multipliers (empirically derived)

This is the weakest layer architecturally. A first-class integration with Claude Code's internal token counter would make it much more reliable. For now, it's "better than nothing" — which is what the model currently has.

### Layer 2: Planning (Strategy)

**Problem**: The model picks a generation strategy without considering constraints.  
**Solution**: A planning tool that selects format, chunk boundaries, and strategy based on budget and available compilers.

The strategy selection is a decision tree:

```
headroom > content × multiplier × 1.5?
  YES → direct (one-shot generation)
  NO  → headroom > content × 0.5?
          YES → chunked (split into N chunks)
          NO  → deferred (template + data separation)
```

Format selection follows a cost model:
```
Format overhead (tokens per content token):
  Markdown:    1.05×
  LaTeX:       1.30×
  JSON:        1.15×
  python-docx: 1.40×  ← most expensive
  HTML:        1.20×
```

This is why python-docx failed and LaTeX succeeded — the format overhead difference (1.4× vs 1.3×) compounds across large documents and can push the total output past the headroom threshold.

### Layer 3: Rendering (Separation of Concerns)

**Problem**: The LLM generates both content AND formatting code simultaneously.  
**Solution**: Separate them. The LLM generates small structured data; a template engine handles formatting.

```
Traditional (what failed):
  LLM → [500-line python-docx script with embedded data] → .docx
  Token cost: ~15K tokens of LLM generation

Deferred rendering (gen-pilot):
  LLM → [structured JSON, ~3K tokens] → gp_render → template → .tex → pdflatex → .pdf
  Token cost: ~3K tokens of LLM generation + 0 tokens for rendering
```

The rendering step is **deterministic** — no LLM tokens consumed. The template is registered once and reused. The LLM's only job is producing the data, which is small, structured, and easy to validate.

## Data Flow: Complete Example

Using the original incident as the example:

```
Agent has: 9 paper evaluations (90K tokens in context)

Step 1: gp_estimate(data=evaluations, format="latex")
        → 9200 tokens estimated

Step 2: gp_budget(conversation_tokens=145000)
        → headroom=55000, max_safe_output=8000
        → recommendation="chunk"

Step 3: gp_plan(
          description="14-page evaluation report",
          sections=["Paper 1"..."Paper 9", "Aggregate"],
          target_format="pdf",
          estimated_content_tokens=9200,
          available_headroom=55000
        )
        → strategy="deferred_render"
        → steps: [generate_json, register_template, render, compile]

Step 4: Agent generates evaluations.json (~3K tokens of LLM output)
Step 5: Agent calls gp_register_template(name="eval_report", content=<latex_jinja2>)
Step 6: Agent calls gp_render(template="eval_report", data=<from json>, output_path="report.tex", compile=true)
        → report.pdf produced deterministically, 0 LLM tokens
```

**Total LLM generation: ~4.5K tokens** (vs. ~15K that caused stalling)

## State Management

```
.gen_pilot/
├── templates/           # Registered Jinja2 templates
│   └── eval_report.tex.j2
├── plans/               # Plan history (for replan reference)
│   └── plan_a1b2c3.json
└── config.json          # Model token limits, format multipliers
```

## Open Questions

1. **Token estimation accuracy**: `tiktoken` with `cl100k_base` is approximate for Claude. How far off is it? Should we offer calibration?

2. **Context access**: Can Claude Code expose a `context_tokens_used()` API that gen-pilot can call? This would eliminate the biggest accuracy bottleneck.

3. **Template library**: Should gen-pilot ship with built-in templates for common document types (reports, papers, READMEs)? Or keep it minimal and let users register their own?

4. **resilient-write integration depth**: Should `gp_render` directly call `rw_safe_write` via MCP tool-to-tool invocation, or should it use its own atomic write and let the agent chain them?

5. **Multiplier calibration**: The format multipliers (1.3× for LaTeX, 1.4× for python-docx) are from one incident. How do they hold across diverse documents?
