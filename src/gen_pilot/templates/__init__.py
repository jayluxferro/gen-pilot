"""Built-in template library for gen-pilot.

Templates are loaded via importlib.resources and can be registered
into the renderer's workspace with load_builtin_templates().
"""

from __future__ import annotations

import importlib.resources
import json
from pathlib import Path
from typing import Any


def _templates_dir() -> Path:
    """Return the path to the bundled templates directory."""
    return Path(str(importlib.resources.files("gen_pilot.templates")))


def list_builtin_templates() -> list[dict[str, Any]]:
    """List all bundled templates with their metadata."""
    tpl_dir = _templates_dir()
    templates = []
    for meta_file in sorted(tpl_dir.glob("*.meta.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            templates.append(meta)
        except (json.JSONDecodeError, KeyError):
            continue
    return templates


def get_builtin_template(name: str) -> tuple[str, dict[str, Any]] | None:
    """Return (content, metadata) for a builtin template by name, or None."""
    tpl_dir = _templates_dir()
    meta_file = tpl_dir / f"{name}.meta.json"
    if not meta_file.exists():
        return None
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    tpl_file = tpl_dir / meta["file"]
    if not tpl_file.exists():
        return None
    content = tpl_file.read_text(encoding="utf-8")
    return content, meta


def load_builtin_templates() -> list[dict[str, Any]]:
    """Register all bundled templates into the renderer workspace.

    Returns list of registration results. User templates with the same
    name take precedence (not overwritten).
    """
    from gen_pilot.tools.renderer import _meta_path, register_template

    results = []
    for meta in list_builtin_templates():
        name = meta["name"]
        # Don't overwrite user-registered templates
        if _meta_path(name).exists():
            continue
        pair = get_builtin_template(name)
        if pair is None:
            continue
        content, m = pair
        result = register_template(
            name=name,
            content=content,
            fmt=m["format"],
            schema=m.get("schema"),
            description=m.get("description"),
        )
        results.append(result)
    return results
