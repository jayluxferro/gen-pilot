"""Tests for Layer 3: Deferred Rendering tools."""

from pathlib import Path

import pytest

from gen_pilot.tools.renderer import (
    list_templates,
    register_template,
    render_template,
)


@pytest.fixture(autouse=True)
def clean_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect .gen_pilot/ to a temp directory for test isolation."""
    state = tmp_path / ".gen_pilot" / "templates"
    state.mkdir(parents=True)
    monkeypatch.setattr("gen_pilot.tools.renderer._state_dir", lambda: state)


class TestGpRegisterTemplate:
    """Tests for gp_register_template."""

    def test_register_latex_template(self) -> None:
        """Should store a LaTeX Jinja2 template and extract variable names."""
        content = (
            r"\documentclass{article}\begin{document}"
            r"\VAR{title} by \VAR{author}\end{document}"
        )
        result = register_template(name="test_latex", content=content, fmt="latex")
        assert result["ok"] is True
        assert result["name"] == "test_latex"
        assert result["format"] == "latex"
        assert "sha256" in result
        assert Path(result["stored_at"]).exists()

    def test_register_markdown_template(self) -> None:
        """Should store a Markdown Jinja2 template."""
        content = "# {{ title }}\n\nBy {{ author }}\n\n{{ body }}"
        result = register_template(name="test_md", content=content, fmt="markdown")
        assert result["ok"] is True
        assert "title" in result["variables"]
        assert "author" in result["variables"]
        assert "body" in result["variables"]

    def test_duplicate_name_overwrites(self) -> None:
        """Registering with same name should overwrite previous template."""
        register_template(name="dup", content="{{ old }}", fmt="text")
        result = register_template(name="dup", content="{{ new_var }}", fmt="text")
        assert result["ok"] is True
        assert "new_var" in result["variables"]

    def test_latex_variable_extraction_with_loops(self) -> None:
        """LaTeX templates with loops should extract top-level iterables, not loop vars."""
        content = (
            r"\documentclass{article}\begin{document}"
            r"\BLOCK{for item in items}\VAR{item.name}\BLOCK{endfor}"
            r"\VAR{title}\end{document}"
        )
        result = register_template(name="latex_loop", content=content, fmt="latex")
        assert result["ok"] is True
        assert "items" in result["variables"]
        assert "title" in result["variables"]
        # "item" is a loop variable, not a top-level variable
        assert "item" not in result["variables"]

    def test_schema_validation(self) -> None:
        """If schema is provided, it should be stored and returned."""
        schema = {"type": "object", "properties": {"title": {"type": "string"}}}
        result = register_template(
            name="with_schema",
            content="{{ title }}",
            fmt="text",
            schema=schema,
        )
        assert result["ok"] is True


class TestGpRender:
    """Tests for gp_render."""

    def test_render_markdown_template(self, tmp_path: Path) -> None:
        """Should render JSON data through a Markdown template."""
        register_template(
            name="report",
            content="# {{ title }}\n\nBy {{ author }}",
            fmt="markdown",
        )
        output = tmp_path / "output.md"
        result = render_template(
            template="report",
            data={"title": "Test Report", "author": "Alice"},
            output_path=str(output),
        )
        assert result["ok"] is True
        assert output.exists()
        text = output.read_text()
        assert "Test Report" in text
        assert "Alice" in text

    def test_render_latex_template(self, tmp_path: Path) -> None:
        """Should render data through a LaTeX template with special delimiters."""
        content = r"\documentclass{article}\begin{document}\VAR{title}\end{document}"
        register_template(name="latex_tpl", content=content, fmt="latex")
        output = tmp_path / "output.tex"
        result = render_template(
            template="latex_tpl",
            data={"title": "My Paper"},
            output_path=str(output),
        )
        assert result["ok"] is True
        text = output.read_text()
        assert "My Paper" in text

    def test_render_missing_template_errors(self, tmp_path: Path) -> None:
        """Rendering with unregistered template should return structured error."""
        result = render_template(
            template="nonexistent",
            data={"x": 1},
            output_path=str(tmp_path / "out.txt"),
        )
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_render_missing_variable_errors(self, tmp_path: Path) -> None:
        """Missing required template variable should return structured error."""
        register_template(name="strict", content="{{ required_var }}", fmt="text")
        result = render_template(
            template="strict",
            data={},
            output_path=str(tmp_path / "out.txt"),
        )
        assert result["ok"] is False
        assert "Missing" in result["error"] or "undefined" in result["error"].lower()

    def test_render_is_deterministic(self, tmp_path: Path) -> None:
        """Same data + template should produce identical output."""
        register_template(name="det", content="Hello {{ name }}!", fmt="text")
        out1 = tmp_path / "out1.txt"
        out2 = tmp_path / "out2.txt"
        r1 = render_template(template="det", data={"name": "World"}, output_path=str(out1))
        r2 = render_template(template="det", data={"name": "World"}, output_path=str(out2))
        assert r1["sha256"] == r2["sha256"]
        assert out1.read_text() == out2.read_text()

    def test_render_output_path(self, tmp_path: Path) -> None:
        """Output should be written to the specified path."""
        register_template(name="path_test", content="content: {{ val }}", fmt="text")
        output = tmp_path / "subdir" / "nested" / "out.txt"
        result = render_template(
            template="path_test",
            data={"val": "42"},
            output_path=str(output),
        )
        assert result["ok"] is True
        assert output.exists()
        assert "42" in output.read_text()


class TestGpRenderResilientWrite:
    """Tests for gp_render with resilient-write integration."""

    def test_use_rw_writes_via_resilient_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """use_rw=True should write via resilient-write safe_write."""
        # resilient-write requires workspace-relative paths,
        # so set cwd to tmp_path for this test
        monkeypatch.chdir(tmp_path)
        register_template(name="rw_test", content="RW: {{ val }}", fmt="text")
        output = tmp_path / "rw_out.txt"
        result = render_template(
            template="rw_test",
            data={"val": "hello"},
            output_path=str(output),
            use_rw=True,
        )
        assert result["ok"] is True
        assert result["write_method"] == "resilient_write"
        assert output.exists()
        assert "hello" in output.read_text()

    def test_use_rw_falls_back_when_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """use_rw=True should fall back to atomic write if rw not installed."""

        # Force ImportError for resilient_write
        import builtins

        real_import = builtins.__import__

        def mock_import(name: str, *args: object, **kwargs: object) -> object:
            if "resilient_write" in name:
                raise ImportError("mocked")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        register_template(name="fb_test", content="FB: {{ v }}", fmt="text")
        output = tmp_path / "fb_out.txt"
        result = render_template(
            template="fb_test",
            data={"v": "fallback"},
            output_path=str(output),
            use_rw=True,
        )
        assert result["ok"] is True
        assert result["write_method"] == "atomic_rename"
        assert output.exists()


class TestGpListTemplates:
    """Tests for gp_list_templates."""

    def test_empty_initially_has_builtins(self) -> None:
        """No user templates registered should still list builtins."""
        result = list_templates()
        assert result["ok"] is True
        # Builtins are always present
        builtin_names = [
            t["name"] for t in result["templates"] if t.get("source") == "builtin"
        ]
        assert "academic_report" in builtin_names
        assert "technical_doc" in builtin_names
        # No user templates
        user = [t for t in result["templates"] if t.get("source") == "user"]
        assert user == []

    def test_lists_registered_templates(self) -> None:
        """Should list user templates alongside builtins."""
        register_template(name="tpl_a", content="{{ x }}", fmt="text")
        register_template(name="tpl_b", content="{{ y }}", fmt="markdown")
        result = list_templates()
        assert result["ok"] is True
        names = [t["name"] for t in result["templates"]]
        assert "tpl_a" in names
        assert "tpl_b" in names
        # Builtins still present
        assert "academic_report" in names

    def test_list_templates_includes_schema(self) -> None:
        """list_templates should include schema for templates that have one."""
        schema = {
            "type": "object",
            "required": ["title"],
            "properties": {"title": {"type": "string"}},
        }
        register_template(
            name="schema_test",
            content="{{ title }}",
            fmt="text",
            schema=schema,
        )
        result = list_templates()
        tpl = next(t for t in result["templates"] if t["name"] == "schema_test")
        assert tpl["schema"] == schema

    def test_builtin_templates_include_schema(self) -> None:
        """Builtin templates should include their schema in list_templates."""
        result = list_templates()
        academic = next(
            t for t in result["templates"] if t["name"] == "academic_report"
        )
        assert academic["schema"] is not None
        assert "properties" in academic["schema"]
        assert "sections" in academic["schema"]["properties"]


class TestGpRenderWhitespace:
    """Tests for Jinja2 whitespace trimming."""

    def test_no_extra_blank_lines_in_loops(self, tmp_path: Path) -> None:
        """Rendered output should not have extra blank lines from Jinja2 blocks."""
        content = "Items:\n{% for item in items %}\n- {{ item }}\n{% endfor %}\nDone."
        register_template(name="ws_test", content=content, fmt="text")
        output = tmp_path / "ws.txt"
        result = render_template(
            template="ws_test",
            data={"items": ["a", "b"]},
            output_path=str(output),
        )
        assert result["ok"] is True
        text = output.read_text()
        # trim_blocks should remove the newline after block tags
        assert "\n\n\n" not in text


class TestGpRenderBuiltinTemplates:
    """Tests for rendering builtin templates with optional fields omitted."""

    def test_academic_report_without_optional_fields(self, tmp_path: Path) -> None:
        """academic_report should render without subsections or references."""
        from gen_pilot.templates import get_builtin_template

        builtin = get_builtin_template("academic_report")
        assert builtin is not None
        content, meta = builtin
        register_template(
            name="academic_report",
            content=content,
            fmt=meta["format"],
        )
        output = tmp_path / "report.tex"
        result = render_template(
            template="academic_report",
            data={
                "title": "Test",
                "author": "Author",
                "date": "2026",
                "abstract": "An abstract.",
                "sections": [{"title": "Intro", "body": "Hello world."}],
            },
            output_path=str(output),
        )
        assert result["ok"] is True
        text = output.read_text()
        assert "Test" in text
        assert "Hello world." in text


class TestGpSecurityGuards:
    """Tests for security hardening."""

    def test_html_auto_escapes_xss(self, tmp_path: Path) -> None:
        """HTML templates should auto-escape data to prevent XSS."""
        register_template(
            name="html_xss",
            content="<p>{{ content }}</p>",
            fmt="html",
        )
        output = tmp_path / "xss.html"
        result = render_template(
            template="html_xss",
            data={"content": "<script>alert('xss')</script>"},
            output_path=str(output),
        )
        assert result["ok"] is True
        text = output.read_text()
        assert "<script>" not in text
        assert "&lt;script&gt;" in text

    def test_path_traversal_rejected(self) -> None:
        """Template name with '..' should be rejected."""
        result = register_template(
            name="../escape", content="{{ x }}", fmt="text"
        )
        assert result["ok"] is False
        assert ".." in result["error"]

    def test_slash_in_name_rejected(self) -> None:
        """Template name with '/' should be rejected."""
        result = register_template(
            name="sub/dir", content="{{ x }}", fmt="text"
        )
        assert result["ok"] is False
        assert "/" in result["error"]

    def test_empty_name_rejected(self) -> None:
        """Empty template name should be rejected."""
        result = register_template(name="", content="{{ x }}", fmt="text")
        assert result["ok"] is False

    def test_render_path_traversal_rejected(self, tmp_path: Path) -> None:
        """Rendering with traversal name should be rejected."""
        result = render_template(
            template="../escape",
            data={"x": 1},
            output_path=str(tmp_path / "out.txt"),
        )
        assert result["ok"] is False
        assert ".." in result["error"]

    def test_sandbox_blocks_class_access(self, tmp_path: Path) -> None:
        """SandboxedEnvironment should block Python class introspection."""
        register_template(
            name="ssti_test",
            content="{{ [].__class__.__bases__ }}",
            fmt="text",
        )
        result = render_template(
            template="ssti_test",
            data={},
            output_path=str(tmp_path / "out.txt"),
        )
        assert result["ok"] is False
        assert "security" in result["error"].lower() or "unsafe" in result["error"].lower()
