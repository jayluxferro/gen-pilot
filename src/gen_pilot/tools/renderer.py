"""Layer 3 — Deferred Rendering tools.

gp_register_template: Registers a Jinja2 template (LaTeX, Markdown, HTML, text)
                      for later rendering. Templates are stored on disk and reusable.
gp_render:            Accepts structured data (JSON) + a registered template name,
                      renders the final document, and writes it to disk.
gp_list_templates:    Lists available templates with their format and variable schema.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jinja2
import jinja2.meta
from jinja2.sandbox import SandboxedEnvironment
from mcp.types import Tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RENDER_BYTES = 10 * 1024 * 1024  # 10 MB

FORMAT_EXTENSIONS: dict[str, str] = {
    "latex": ".tex.j2",
    "markdown": ".md.j2",
    "html": ".html.j2",
    "text": ".txt.j2",
}

# ---------------------------------------------------------------------------
# State directory
# ---------------------------------------------------------------------------


def _state_dir() -> Path:
    """Return (and create) the .gen_pilot/templates/ directory."""
    d = Path(".gen_pilot") / "templates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _meta_path(name: str) -> Path:
    return _state_dir() / f"{name}.meta.json"


def _template_path(name: str, fmt: str) -> Path:
    ext = FORMAT_EXTENSIONS.get(fmt, ".j2")
    return _state_dir() / f"{name}{ext}"


# ---------------------------------------------------------------------------
# Variable extraction from Jinja2 source
# ---------------------------------------------------------------------------

def _extract_variables(content: str, fmt: str = "text") -> list[str]:
    """Extract top-level variable names from Jinja2 template source."""
    if fmt == "latex":
        # LaTeX templates use custom delimiters — parse with matching Environment
        env = SandboxedEnvironment(
            block_start_string="\\BLOCK{",
            block_end_string="}",
            variable_start_string="\\VAR{",
            variable_end_string="}",
            comment_start_string="\\#{",
            comment_end_string="}",
        )
    else:
        env = SandboxedEnvironment()
    ast = env.parse(content)
    variables = sorted(jinja2.meta.find_undeclared_variables(ast))
    return variables


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _validate_template_name(name: str) -> str | None:
    """Return error message if name is invalid, None if OK."""
    if not name or name != name.strip():
        return "Template name must be non-empty with no leading/trailing whitespace."
    if ".." in name or "/" in name or "\\" in name or "\0" in name:
        return "Template name must not contain '..', '/', '\\', or null bytes."
    return None


def register_template(
    name: str,
    content: str,
    fmt: str,
    schema: dict[str, Any] | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Register a Jinja2 template and persist it to disk."""
    if err := _validate_template_name(name):
        return {"ok": False, "error": f"Invalid template name '{name}': {err}"}
    tpl_path = _template_path(name, fmt)
    tpl_path.write_text(content, encoding="utf-8")

    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    variables = _extract_variables(content, fmt)

    meta = {
        "name": name,
        "format": fmt,
        "description": description,
        "variables": variables,
        "schema": schema,
        "sha256": sha,
        "stored_at": str(tpl_path),
        "created_at": datetime.now(UTC).isoformat(),
    }
    _meta_path(name).write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "name": name,
        "format": fmt,
        "variables": variables,
        "stored_at": str(tpl_path),
        "sha256": sha,
    }


def render_template(
    template: str,
    data: dict[str, Any],
    output_path: str,
    compile: bool = False,
    compile_cmd: str | None = None,
    use_rw: bool = False,
) -> dict[str, Any]:
    """Render data through a registered template and write to output_path.

    If use_rw=True, writes via resilient-write's safe_write for atomic
    writes with journaling. Falls back to built-in atomic write if
    resilient-write is not installed.
    """
    if err := _validate_template_name(template):
        return {"ok": False, "error": f"Invalid template name '{template}': {err}"}
    meta_file = _meta_path(template)
    if not meta_file.exists():
        # Try loading from builtins
        from gen_pilot.templates import get_builtin_template

        builtin = get_builtin_template(template)
        if builtin is not None:
            content, meta = builtin
            register_template(
                name=template,
                content=content,
                fmt=meta["format"],
                schema=meta.get("schema"),
                description=meta.get("description"),
            )
            meta_file = _meta_path(template)

    if not meta_file.exists():
        return {
            "ok": False,
            "error": (
                f"Template '{template}' not found. "
                "Register it first with gp_register_template."
            ),
        }

    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    fmt = meta["format"]
    tpl_path = _template_path(template, fmt)

    if not tpl_path.exists():
        return {"ok": False, "error": f"Template file missing: {tpl_path}"}

    tpl_content = tpl_path.read_text(encoding="utf-8")

    # Configure sandboxed Jinja2 environment — prevents SSTI attacks
    if fmt == "latex":
        env = SandboxedEnvironment(
            block_start_string="\\BLOCK{",
            block_end_string="}",
            variable_start_string="\\VAR{",
            variable_end_string="}",
            comment_start_string="\\#{",
            comment_end_string="}",
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=jinja2.StrictUndefined,
        )
    elif fmt == "html":
        env = SandboxedEnvironment(
            autoescape=True,
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=jinja2.StrictUndefined,
        )
    else:
        env = SandboxedEnvironment(
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=jinja2.StrictUndefined,
        )

    try:
        tpl = env.from_string(tpl_content)
    except jinja2.TemplateSyntaxError as e:
        return {"ok": False, "error": f"Template syntax error: {e}"}

    try:
        rendered = tpl.render(**data)
    except jinja2.UndefinedError as e:
        return {"ok": False, "error": f"Missing template variable: {e}"}
    except jinja2.exceptions.SecurityError as e:
        return {"ok": False, "error": f"Template security violation: {e}"}

    rendered_bytes = len(rendered.encode("utf-8"))
    if rendered_bytes > MAX_RENDER_BYTES:
        return {
            "ok": False,
            "error": (
                f"Rendered output too large ({rendered_bytes:,} bytes, "
                f"limit {MAX_RENDER_BYTES:,} bytes). "
                "Consider splitting into smaller templates."
            ),
        }
    rendered_sha = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    out = Path(output_path)

    if use_rw:
        try:
            from resilient_write.safe_write import safe_write as rw_write

            workspace = Path.cwd()
            # resilient-write requires workspace-relative paths
            try:
                rel_path = str(out.resolve().relative_to(workspace.resolve()))
            except ValueError:
                rel_path = output_path  # let rw handle the error

            rw_result = rw_write(
                workspace,
                path=rel_path,
                content=rendered,
                mode="overwrite",
                caller="gen-pilot:gp_render",
            )
            rw_ok = rw_result.get("ok", True)
            result_dict: dict[str, Any] = {
                "ok": rw_ok,
                "rendered_path": str(out),
                "rendered_bytes": rendered_bytes,
                "sha256": rendered_sha,
                "write_method": "resilient_write",
                "rw_journal_id": rw_result.get("journal_id"),
                "compiled": False,
                "compiled_path": None,
                "compiled_bytes": None,
            }
            if not rw_ok:
                result_dict["error"] = rw_result.get("error", "resilient-write failed")
            return result_dict
        except ImportError:
            pass  # fall through to built-in atomic write

    # Atomic write: temp file → rename
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(out.parent), suffix=".tmp")
    fd_closed = False
    try:
        os.write(fd, rendered.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        fd_closed = True
        os.replace(tmp, str(out))
    except Exception:
        if not fd_closed:
            os.close(fd)
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    result: dict[str, Any] = {
        "ok": True,
        "rendered_path": str(out),
        "rendered_bytes": rendered_bytes,
        "sha256": rendered_sha,
        "write_method": "atomic_rename",
        "compiled": False,
        "compiled_path": None,
        "compiled_bytes": None,
    }

    # Optional compilation (LaTeX → PDF)
    if compile and fmt == "latex":
        cmd = compile_cmd or "pdflatex"
        pdf_path = out.with_suffix(".pdf")
        try:
            proc = subprocess.run(
                [cmd, "-interaction=nonstopmode", "-output-directory", str(out.parent), str(out)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if pdf_path.exists():
                result["compiled"] = True
                result["compiled_path"] = str(pdf_path)
                result["compiled_bytes"] = pdf_path.stat().st_size
            else:
                result["compile_warning"] = (
                    f"{cmd} ran but PDF not produced. "
                    f"stderr: {proc.stderr[:500]}"
                )
        except FileNotFoundError:
            result["compile_warning"] = f"Compiler '{cmd}' not found on PATH"
        except subprocess.TimeoutExpired:
            result["compile_warning"] = "Compilation timed out after 60s"

    return result


def list_templates() -> dict[str, Any]:
    """List all registered templates (user + builtin) with metadata."""
    from gen_pilot.templates import list_builtin_templates

    templates = []
    seen_names: set[str] = set()

    # User-registered templates first (they shadow builtins)
    state = _state_dir()
    for meta_file in sorted(state.glob("*.meta.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            name = meta["name"]
            seen_names.add(name)
            templates.append({
                "name": name,
                "format": meta["format"],
                "description": meta.get("description"),
                "variables": meta.get("variables", []),
                "schema": meta.get("schema"),
                "created_at": meta.get("created_at"),
                "source": "user",
            })
        except (json.JSONDecodeError, KeyError):
            continue

    # Builtin templates (only those not shadowed)
    for meta in list_builtin_templates():
        name = meta["name"]
        if name not in seen_names:
            templates.append({
                "name": name,
                "format": meta["format"],
                "description": meta.get("description"),
                "variables": meta.get("variables", []),
                "schema": meta.get("schema"),
                "source": "builtin",
            })

    return {"ok": True, "templates": templates}


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="gp_register_template",
        description=(
            "Register a Jinja2 template for later rendering. "
            "Templates are stored in .gen_pilot/templates/ and can be reused. "
            "Supports LaTeX, Markdown, HTML, and plain text formats."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Template identifier (e.g. 'eval_report_latex')",
                },
                "content": {
                    "type": "string",
                    "description": "Jinja2 template content",
                },
                "format": {
                    "type": "string",
                    "enum": ["latex", "markdown", "html", "text"],
                    "description": "Template format",
                },
                "schema": {
                    "type": "object",
                    "description": "JSON Schema for expected data variables (optional)",
                },
                "description": {
                    "type": "string",
                    "description": "Human-readable description of the template",
                },
            },
            "required": ["name", "content", "format"],
        },
    ),
    Tool(
        name="gp_render",
        description=(
            "Render structured data through a registered Jinja2 template. "
            "Deterministic — no LLM tokens consumed. "
            "Optionally compiles LaTeX to PDF."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "template": {
                    "type": "string",
                    "description": "Registered template name",
                },
                "data": {
                    "type": "object",
                    "description": "JSON data matching template variables",
                },
                "output_path": {
                    "type": "string",
                    "description": "Where to write the rendered file",
                },
                "compile": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true and format is LaTeX, run pdflatex",
                },
                "compile_cmd": {
                    "type": "string",
                    "description": "Override compile command (e.g. 'xelatex')",
                },
                "use_rw": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Write output via resilient-write's rw_safe_write "
                        "for atomic writes with journaling. "
                        "Falls back to built-in atomic write if not installed."
                    ),
                },
            },
            "required": ["template", "data", "output_path"],
        },
    ),
    Tool(
        name="gp_list_templates",
        description=(
            "List all registered templates with metadata "
            "(name, format, variables, description)."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
]


async def handle_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Handle a renderer tool call. Returns result dict."""
    if name == "gp_register_template":
        return register_template(
            name=arguments["name"],
            content=arguments["content"],
            fmt=arguments["format"],
            schema=arguments.get("schema"),
            description=arguments.get("description"),
        )
    elif name == "gp_render":
        return render_template(
            template=arguments["template"],
            data=arguments["data"],
            output_path=arguments["output_path"],
            compile=arguments.get("compile", False),
            compile_cmd=arguments.get("compile_cmd"),
            use_rw=arguments.get("use_rw", False),
        )
    elif name == "gp_list_templates":
        return list_templates()
    return {"ok": False, "error": f"Unknown renderer tool: {name}"}
