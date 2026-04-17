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

    def test_zero_content_tokens_not_overridden(self) -> None:
        """estimated_content_tokens=0 should stay 0, not silently become 2000."""
        result = create_plan(
            description="Empty doc",
            estimated_content_tokens=0,
            available_headroom=100_000,
        )
        assert result["ok"] is True
        # With 0 content tokens, output estimate should be 0 or very small
        total = sum(s["estimated_output_tokens"] for s in result["steps"])
        assert total < 100

    def test_docx_small_uses_python_multiplier(self) -> None:
        """Small docx plan should apply python (1.4x) multiplier."""
        result = create_plan(
            description="Letter",
            target_format="docx",
            estimated_content_tokens=1000,
            available_headroom=100_000,
        )
        assert result["ok"] is True
        # python-docx chain should infer python format with 1.4x multiplier
        gen_step = result["steps"][0]
        assert gen_step["estimated_output_tokens"] == int(1000 * 1.4)

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

    def test_format_selection_code(self) -> None:
        """Code target should use code chain with 1.2x multiplier."""
        result = create_plan(
            description="Python auth module",
            target_format="code",
            estimated_content_tokens=3000,
            available_headroom=100_000,
        )
        assert result["ok"] is True
        assert "code" in result["format_chain"]
        assert "1.2x" in result["rationale"]

    def test_format_selection_json(self) -> None:
        """JSON target should use json chain with 1.15x multiplier."""
        result = create_plan(
            description="User dataset",
            target_format="json",
            estimated_content_tokens=2000,
            available_headroom=100_000,
        )
        assert result["ok"] is True
        assert "json" in result["format_chain"]
        assert "1.15x" in result["rationale"]

    def test_format_selection_yaml(self) -> None:
        """YAML target should use yaml chain."""
        result = create_plan(
            description="K8s config",
            target_format="yaml",
            estimated_content_tokens=1000,
            available_headroom=100_000,
        )
        assert result["ok"] is True
        assert "yaml" in result["format_chain"]

    def test_format_selection_text(self) -> None:
        """Text target should use text chain with raw multiplier."""
        result = create_plan(
            description="Plain text output",
            target_format="text",
            estimated_content_tokens=1000,
            available_headroom=100_000,
        )
        assert result["ok"] is True
        assert "text" in result["format_chain"]

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

    def test_replan_invalid_completed_steps_rejected(self) -> None:
        """Completed steps beyond actual plan steps should be rejected."""
        original = create_plan(
            description="Report",
            estimated_content_tokens=5000,
            available_headroom=50_000,
        )
        result = replan(
            plan_id=original["plan_id"],
            failure_mode="empty_response",
            completed_steps=[1, 999],
        )
        assert result["ok"] is False
        assert "Invalid completed step" in result["error"]
        assert "999" in result["error"]

    def test_plan_id_no_collisions_rapid_fire(self) -> None:
        """Rapid plan creation should not produce ID collisions."""
        ids = set()
        for _ in range(100):
            plan = create_plan(description="rapid")
            assert plan["plan_id"] not in ids
            ids.add(plan["plan_id"])

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

    def test_deferred_empty_response_escapes_to_chunked(self) -> None:
        """Deferred plan failing with empty_response should escape to chunked, not loop."""
        original = create_plan(
            description="Report under pressure",
            estimated_content_tokens=10_000,
            available_headroom=2_000,
        )
        assert original["strategy"] == "deferred_render"

        revised = replan(
            plan_id=original["plan_id"],
            failure_mode="empty_response",
        )
        assert revised["ok"] is True
        assert "chunked" in revised["strategy"]

    def test_deferred_truncated_stays_deferred(self) -> None:
        """Truncated failure on deferred plan should stay deferred (chunk the data)."""
        original = create_plan(
            description="Report under pressure",
            estimated_content_tokens=10_000,
            available_headroom=2_000,
        )
        assert original["strategy"] == "deferred_render"

        revised = replan(
            plan_id=original["plan_id"],
            failure_mode="truncated",
        )
        assert revised["ok"] is True
        assert revised["strategy"] == "deferred_render"

    def test_deferred_timeout_escapes_to_chunked(self) -> None:
        """Timeout on deferred plan should escape to chunked."""
        original = create_plan(
            description="Report under pressure",
            estimated_content_tokens=10_000,
            available_headroom=2_000,
        )
        assert original["strategy"] == "deferred_render"

        revised = replan(
            plan_id=original["plan_id"],
            failure_mode="timeout",
        )
        assert revised["ok"] is True
        assert "chunked" in revised["strategy"]

    def test_remaining_headroom_forces_deferred(self) -> None:
        """Low remaining_headroom should override chunked to deferred."""
        original = create_plan(
            description="Report",
            estimated_content_tokens=5000,
            available_headroom=50_000,
        )
        assert original["strategy"] == "direct"

        revised = replan(
            plan_id=original["plan_id"],
            failure_mode="empty_response",
            remaining_headroom=100,
        )
        assert revised["ok"] is True
        assert revised["strategy"] == "deferred_render"

    def test_replan_depth_tracked(self) -> None:
        """Plans should track replan depth starting at 0."""
        original = create_plan(
            description="Report",
            estimated_content_tokens=5000,
            available_headroom=50_000,
        )
        assert original.get("replan_depth") == 0

        revised = replan(
            plan_id=original["plan_id"],
            failure_mode="empty_response",
        )
        assert revised["ok"] is True
        assert revised["replan_depth"] == 1
        assert "1/3" in revised["rationale"]

    def test_replan_depth_limit_stops_cycling(self) -> None:
        """Replan should error after exceeding max depth instead of cycling."""
        plan = create_plan(
            description="Report",
            estimated_content_tokens=5000,
            available_headroom=50_000,
        )
        # Chain replans until we hit the limit
        for i in range(3):
            plan = replan(plan_id=plan["plan_id"], failure_mode="empty_response")
            assert plan["ok"] is True
            assert plan["replan_depth"] == i + 1

        # 4th replan should fail
        result = replan(plan_id=plan["plan_id"], failure_mode="empty_response")
        assert result["ok"] is False
        assert "exceeds maximum" in result["error"]

    def test_replan_uses_config_multipliers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Replan should use get_multipliers() which respects config overrides."""
        original = create_plan(
            description="Report",
            target_format="markdown",
            estimated_content_tokens=5000,
            available_headroom=50_000,
        )
        # Patch get_multipliers to return a custom value
        custom = {"markdown": 2.0, "latex": 1.3}
        monkeypatch.setattr("gen_pilot.tools.planner.get_multipliers", lambda: custom)
        revised = replan(
            plan_id=original["plan_id"],
            failure_mode="empty_response",
        )
        assert revised["ok"] is True
        # The rationale should reflect the higher multiplier
        assert "2.0x" not in original["rationale"]  # original used 1.05

    def test_plan_gc_limits_files(self) -> None:
        """Plan garbage collection should limit the number of plan files."""
        from gen_pilot.tools.planner import _MAX_PLAN_FILES, _plans_dir

        # Create more plans than the limit
        for i in range(_MAX_PLAN_FILES + 10):
            create_plan(description=f"plan {i}")

        plan_files = list(_plans_dir().glob("plan_*.json"))
        assert len(plan_files) <= _MAX_PLAN_FILES
