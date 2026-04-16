"""Tests for Layer 2: Generation Planning tools."""

from pathlib import Path
from unittest.mock import patch

import pytest

from gen_pilot.tools.planner import (
    create_plan,
    replan,
)


@pytest.fixture(autouse=True)
def clean_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect .gen_pilot/plans/ to a temp directory for test isolation."""
    plans = tmp_path / ".gen_pilot" / "plans"
    plans.mkdir(parents=True)
    monkeypatch.setattr("gen_pilot.tools.planner._plans_dir", lambda: plans)


class TestGpPlan:
    """Tests for gp_plan generation planning."""

    def test_small_document_direct_strategy(self) -> None:
        """Small documents with ample headroom should get 'direct' strategy."""
        result = create_plan(
            description="Short memo",
            estimated_content_tokens=1000,
            available_headroom=100_000,
        )
        assert result["ok"] is True
        assert result["strategy"] == "direct"
        assert len(result["steps"]) >= 1

    def test_large_document_chunked_strategy(self) -> None:
        """Large documents should get chunked strategy."""
        result = create_plan(
            description="Long report with many sections",
            estimated_content_tokens=10_000,
            available_headroom=12_000,
        )
        assert result["ok"] is True
        assert "chunked" in result["strategy"]
        # Should have multiple chunk steps
        chunk_steps = [s for s in result["steps"] if s["action"] == "generate_chunk"]
        assert len(chunk_steps) >= 2

    def test_low_headroom_deferred_strategy(self) -> None:
        """Low headroom should force deferred rendering strategy."""
        result = create_plan(
            description="Report under pressure",
            estimated_content_tokens=10_000,
            available_headroom=2_000,
        )
        assert result["ok"] is True
        assert result["strategy"] == "deferred_render"

    def test_format_selection_pdf_prefers_latex(self) -> None:
        """PDF target with LaTeX available should prefer LaTeX chain."""
        with patch("gen_pilot.tools.planner._has_compiler", return_value=True):
            result = create_plan(
                description="Academic paper",
                target_format="pdf",
                estimated_content_tokens=3000,
                available_headroom=100_000,
            )
        assert result["ok"] is True
        assert "latex" in result["format_chain"]
        assert "pdf" in result["format_chain"]

    def test_format_selection_docx_small(self) -> None:
        """Small docx should allow direct python-docx."""
        result = create_plan(
            description="Short letter",
            target_format="docx",
            estimated_content_tokens=1000,
            available_headroom=100_000,
        )
        assert result["ok"] is True
        assert "python-docx" in result["format_chain"] or "docx" in result["format_chain"]

    def test_format_selection_docx_large_uses_template(self) -> None:
        """Large docx should use jinja2 template approach."""
        result = create_plan(
            description="Long report",
            target_format="docx",
            estimated_content_tokens=5000,
            available_headroom=100_000,
        )
        assert result["ok"] is True
        assert "jinja2_template" in result["format_chain"]

    def test_sections_propagate_to_steps(self) -> None:
        """Explicit section names should map to chunk boundaries in steps."""
        sections = ["Introduction", "Methods", "Results"]
        result = create_plan(
            description="Research paper",
            sections=sections,
            estimated_content_tokens=6000,
            available_headroom=8_000,
        )
        assert result["ok"] is True
        chunk_steps = [s for s in result["steps"] if s["action"] == "generate_chunk"]
        # Each section should be a step
        for section in sections:
            assert any(section in s["description"] for s in chunk_steps)

    def test_plan_id_is_unique(self) -> None:
        """Each plan should get a unique plan_id."""
        p1 = create_plan(description="doc A")
        p2 = create_plan(description="doc B")
        assert p1["plan_id"] != p2["plan_id"]

    def test_plan_has_rationale_and_fallback(self) -> None:
        """Plan should include rationale and fallback."""
        result = create_plan(description="some doc", estimated_content_tokens=2000)
        assert "rationale" in result
        assert "fallback" in result
        assert len(result["rationale"]) > 0


class TestGpReplan:
    """Tests for gp_replan failure recovery."""

    def test_empty_response_downgrades_strategy(self) -> None:
        """Empty response failure should downgrade to smaller chunks or deferred."""
        original = create_plan(
            description="Report",
            estimated_content_tokens=5000,
            available_headroom=50_000,
        )
        assert original["strategy"] == "direct"

        revised = replan(
            plan_id=original["plan_id"],
            failure_mode="empty_response",
        )
        assert revised["ok"] is True
        # Should downgrade from direct to chunked
        assert "chunked" in revised["strategy"] or revised["strategy"] == "deferred_render"

    def test_truncated_reduces_chunk_size(self) -> None:
        """Truncation should produce more/smaller chunks."""
        original = create_plan(
            description="Long report",
            estimated_content_tokens=8000,
            available_headroom=10_000,
        )
        revised = replan(
            plan_id=original["plan_id"],
            failure_mode="truncated",
        )
        assert revised["ok"] is True
        chunk_steps = [s for s in revised["steps"] if s["action"] == "generate_chunk"]
        assert len(chunk_steps) >= 2

    def test_completed_steps_preserved(self) -> None:
        """Replan should not repeat already-completed steps."""
        original = create_plan(
            description="Report",
            sections=["A", "B", "C"],
            estimated_content_tokens=9000,
            available_headroom=10_000,
        )
        # Mark first step as completed
        revised = replan(
            plan_id=original["plan_id"],
            failure_mode="empty_response",
            completed_steps=[1],
        )
        assert revised["ok"] is True
        assert revised["completed_steps"] == [1]
        # New steps should start after step 1
        assert all(s["step"] > 1 for s in revised["steps"])

    def test_replan_unknown_plan_errors(self) -> None:
        """Replanning with unknown plan_id should error."""
        result = replan(plan_id="plan_nonexistent", failure_mode="empty_response")
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_timeout_uses_deferred(self) -> None:
        """Timeout failure should switch to deferred rendering."""
        original = create_plan(
            description="Report",
            estimated_content_tokens=5000,
            available_headroom=50_000,
        )
        revised = replan(
            plan_id=original["plan_id"],
            failure_mode="timeout",
        )
        assert revised["ok"] is True
        assert revised["strategy"] == "deferred_render"
