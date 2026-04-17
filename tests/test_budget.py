"""Tests for Layer 1: Context Budget tools."""


from gen_pilot.tools.budget import (
    compute_budget,
    estimate_tokens,
)


class TestGpEstimate:
    """Tests for gp_estimate token estimation."""

    def test_raw_text_estimation(self) -> None:
        """Plain text should use multiplier 1.0."""
        result = estimate_tokens(text="Hello, world!", fmt="raw")
        assert result["ok"] is True
        assert result["multiplier_applied"] == 1.0
        assert result["estimated_tokens"] == result["raw_tokens"]
        assert result["estimated_tokens"] > 0

    def test_latex_multiplier(self) -> None:
        """LaTeX format should apply ~1.3x multiplier."""
        result = estimate_tokens(text="Some content for a document section", fmt="latex")
        assert result["ok"] is True
        assert result["multiplier_applied"] == 1.3
        assert result["estimated_tokens"] == int(result["raw_tokens"] * 1.3)

    def test_python_multiplier(self) -> None:
        """Python/python-docx format should apply ~1.4x multiplier."""
        result = estimate_tokens(text="Some content for a document section", fmt="python")
        assert result["ok"] is True
        assert result["multiplier_applied"] == 1.4
        assert result["estimated_tokens"] == int(result["raw_tokens"] * 1.4)

    def test_json_data_estimation(self) -> None:
        """Structured data passed as object should be serialized then estimated."""
        data = {"title": "Report", "items": [1, 2, 3]}
        result = estimate_tokens(data=data, fmt="json")
        assert result["ok"] is True
        assert result["multiplier_applied"] == 1.15
        assert result["estimated_tokens"] > 0

    def test_no_input_returns_error(self) -> None:
        """Neither text nor data should return error."""
        result = estimate_tokens()
        assert result["ok"] is False
        assert "error" in result

    def test_markdown_multiplier(self) -> None:
        """Markdown format should apply 1.05x multiplier."""
        result = estimate_tokens(text="# Heading\nSome paragraph text.", fmt="markdown")
        assert result["ok"] is True
        assert result["multiplier_applied"] == 1.05

    def test_code_multiplier(self) -> None:
        """Code format should apply 1.2x multiplier."""
        result = estimate_tokens(text="def foo(x: int) -> str: pass", fmt="code")
        assert result["ok"] is True
        assert result["multiplier_applied"] == 1.2

    def test_html_multiplier(self) -> None:
        """HTML format should apply 1.2x multiplier."""
        result = estimate_tokens(text="<div>Hello</div>", fmt="html")
        assert result["ok"] is True
        assert result["multiplier_applied"] == 1.2

    def test_yaml_multiplier(self) -> None:
        """YAML format should apply 1.05x multiplier."""
        result = estimate_tokens(text="key: value", fmt="yaml")
        assert result["ok"] is True
        assert result["multiplier_applied"] == 1.05

    def test_toml_multiplier(self) -> None:
        """TOML format should apply 1.1x multiplier."""
        result = estimate_tokens(text='[section]\nkey = "value"', fmt="toml")
        assert result["ok"] is True
        assert result["multiplier_applied"] == 1.1


class TestGpBudget:
    """Tests for gp_budget headroom calculation."""

    def test_direct_recommendation(self) -> None:
        """Ample headroom should recommend 'direct' strategy."""
        # 500K used out of 1M = 500K headroom = 50% → direct
        result = compute_budget(model="claude-sonnet-4-6", conversation_tokens=500_000)
        assert result["ok"] is True
        assert result["recommendation"] == "direct"
        assert result["estimated_headroom"] == 500_000
        assert result["warning"] is None

    def test_chunk_recommendation(self) -> None:
        """Moderate headroom should recommend 'chunk' strategy."""
        # 850K used out of 1M = 150K headroom = 15% → chunk
        result = compute_budget(model="claude-sonnet-4-6", conversation_tokens=850_000)
        assert result["ok"] is True
        assert result["recommendation"] == "chunk"
        assert result["suggested_chunk_size"] is not None
        assert result["suggested_chunk_size"] > 0

    def test_defer_recommendation(self) -> None:
        """Low headroom should recommend 'defer' strategy."""
        # 920K used out of 1M = 80K headroom = 8% → defer
        result = compute_budget(model="claude-sonnet-4-6", conversation_tokens=920_000)
        assert result["ok"] is True
        assert result["recommendation"] == "defer"

    def test_compact_first_recommendation(self) -> None:
        """Near-zero headroom should recommend 'compact_first'."""
        # 970K used out of 1M = 30K headroom = 3% → compact_first
        result = compute_budget(model="claude-sonnet-4-6", conversation_tokens=970_000)
        assert result["ok"] is True
        assert result["recommendation"] == "compact_first"
        assert result["warning"] is not None
        assert "Critical" in result["warning"]

    def test_manual_token_override(self) -> None:
        """conversation_tokens param should override auto-estimation."""
        result = compute_budget(conversation_tokens=100_000)
        assert result["ok"] is True
        assert result["estimated_used"] == 100_000
        assert result["estimated_headroom"] == 900_000

    def test_no_conversation_tokens(self) -> None:
        """Without conversation_tokens, should return warning."""
        result = compute_budget()
        assert result["ok"] is True
        assert result["recommendation"] is None
        assert result["warning"] is not None
        assert "Cannot auto-estimate" in result["warning"]

    def test_headroom_never_negative(self) -> None:
        """Headroom should never go below zero."""
        result = compute_budget(conversation_tokens=9_999_999)
        assert result["ok"] is True
        assert result["estimated_headroom"] == 0

    def test_negative_conversation_tokens_clamped(self) -> None:
        """Negative conversation_tokens should be clamped to zero with a warning."""
        result = compute_budget(conversation_tokens=-100)
        assert result["ok"] is True
        assert result["estimated_used"] == 0
        assert result["estimated_headroom"] == result["context_limit"]
        assert result["estimated_headroom"] > 0
        assert result["warning"] is not None
        assert "Negative" in result["warning"] or "clamped" in result["warning"].lower()

    def test_unknown_model_warns(self) -> None:
        """Unknown model should produce a warning about defaulting."""
        result = compute_budget(model="gpt-4o", conversation_tokens=50_000)
        assert result["ok"] is True
        assert result["warning"] is not None
        assert "Unknown model" in result["warning"]
        # Unknown models default to 200K
        assert result["context_limit"] == 200_000

    def test_singular_token_grammar(self) -> None:
        """Warning should use 'token' (singular) when headroom is 1."""
        # Use context_limit - 1 to get exactly 1 token headroom
        result = compute_budget(conversation_tokens=999_999)
        assert result["ok"] is True
        assert result["warning"] is not None
        assert "1 token " in result["warning"]
