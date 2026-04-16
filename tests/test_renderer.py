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
