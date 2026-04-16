#!/usr/bin/env python3
"""Calibrate format multipliers against sample content.

Encodes sample text into each format's representative markup, counts
tokens, and compares against the current multipliers.

Usage:
    uv run python scripts/calibrate_multipliers.py [sample.txt]

If no file is given, uses a built-in sample paragraph.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import tiktoken

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gen_pilot.tools.budget import FORMAT_MULTIPLIERS, count_tokens


# ---------------------------------------------------------------------------
# Sample format wrappers — simulate what each format looks like
# ---------------------------------------------------------------------------


def _as_raw(text: str) -> str:
    return text


def _as_markdown(text: str) -> str:
    lines = text.split(". ")
    out = "# Sample Document\n\n"
    out += "## Introduction\n\n"
    out += ". ".join(lines[:len(lines) // 2]) + ".\n\n"
    out += "## Details\n\n"
    out += "- " + "\n- ".join(lines[len(lines) // 2:]) + "\n"
    return out


def _as_json(text: str) -> str:
    sentences = [s.strip() for s in text.split(". ") if s.strip()]
    data = {
        "title": "Sample Document",
        "sections": [
            {"heading": f"Section {i+1}", "content": s}
            for i, s in enumerate(sentences)
        ],
    }
    return json.dumps(data, indent=2)


def _as_latex(text: str) -> str:
    lines = text.split(". ")
    out = "\\documentclass{article}\n"
    out += "\\usepackage[utf8]{inputenc}\n"
    out += "\\begin{document}\n"
    out += "\\title{Sample Document}\n\\maketitle\n\n"
    out += "\\section{Introduction}\n"
    out += ". ".join(lines[:len(lines) // 2]) + ".\n\n"
    out += "\\section{Details}\n"
    out += "\\begin{itemize}\n"
    out += "".join(f"  \\item {l.strip()}\n" for l in lines[len(lines) // 2:])
    out += "\\end{itemize}\n"
    out += "\\end{document}\n"
    return out


def _as_python(text: str) -> str:
    """Simulate python-docx API code to produce a document."""
    lines = text.split(". ")
    out = "from docx import Document\n\n"
    out += "doc = Document()\n"
    out += "doc.add_heading('Sample Document', 0)\n\n"
    out += "doc.add_heading('Introduction', level=1)\n"
    for line in lines[:len(lines) // 2]:
        out += f"doc.add_paragraph({line.strip()!r})\n"
    out += "\ndoc.add_heading('Details', level=1)\n"
    for line in lines[len(lines) // 2:]:
        out += f"doc.add_paragraph({line.strip()!r}, style='List Bullet')\n"
    out += "\ndoc.save('output.docx')\n"
    return out


FORMAT_GENERATORS = {
    "raw": _as_raw,
    "markdown": _as_markdown,
    "json": _as_json,
    "latex": _as_latex,
    "python": _as_python,
}

DEFAULT_SAMPLE = (
    "The research team conducted a comprehensive analysis of nine "
    "academic papers spanning machine learning, natural language "
    "processing, and computer vision. Each paper was evaluated on "
    "methodology, reproducibility, clarity, and impact. The aggregate "
    "scores revealed strong performance in methodology but weaker "
    "results in reproducibility. Several papers lacked sufficient "
    "detail in their experimental setup to allow independent "
    "replication. The top-scoring paper achieved a perfect mark in "
    "clarity, with well-structured arguments and clear visualizations. "
    "Recommendations include establishing a minimum reproducibility "
    "checklist and requiring code availability for all submissions."
)


def main() -> None:
    if len(sys.argv) > 1:
        sample = Path(sys.argv[1]).read_text(encoding="utf-8")
    else:
        sample = DEFAULT_SAMPLE

    raw_tokens = count_tokens(sample)
    print(f"Raw text: {raw_tokens} tokens\n")
    print(f"{'Format':<12} {'Tokens':>8} {'Measured':>10} {'Current':>10} {'Delta':>8}")
    print("-" * 52)

    for fmt, generator in FORMAT_GENERATORS.items():
        formatted = generator(sample)
        fmt_tokens = count_tokens(formatted)
        measured = fmt_tokens / raw_tokens if raw_tokens > 0 else 0
        current = FORMAT_MULTIPLIERS[fmt]
        delta = measured - current
        sign = "+" if delta >= 0 else ""
        print(
            f"{fmt:<12} {fmt_tokens:>8} {measured:>10.3f}x {current:>10.2f}x "
            f"{sign}{delta:>7.3f}"
        )

    print(f"\nTo update multipliers, edit .gen_pilot/config.json:")
    print(json.dumps({"format_multipliers": FORMAT_MULTIPLIERS}, indent=2))


if __name__ == "__main__":
    main()
