"""Integration tests — MCP server round-trip via in-memory transport."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from gen_pilot.server import create_server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _call(session: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call a tool and parse the JSON response."""
    result = await session.call_tool(name, args)
    return json.loads(result.content[0].text)


@pytest.fixture
def _isolate_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect .gen_pilot/ state dirs to tmp for test isolation."""
    templates = tmp_path / ".gen_pilot" / "templates"
    templates.mkdir(parents=True)
    plans = tmp_path / ".gen_pilot" / "plans"
    plans.mkdir(parents=True)
    monkeypatch.setattr(
        "gen_pilot.tools.renderer._state_dir", lambda: templates
    )
    monkeypatch.setattr(
        "gen_pilot.tools.planner._plans_dir", lambda: plans
    )


# ---------------------------------------------------------------------------
# Tool listing
# ---------------------------------------------------------------------------


class TestServerListTools:
    async def test_lists_all_seven_tools(self) -> None:
        server = create_server()
        async with create_connected_server_and_client_session(server) as session:
            result = await session.list_tools()
            names = sorted(t.name for t in result.tools)
            assert names == [
                "gp_budget",
                "gp_estimate",
                "gp_list_templates",
                "gp_plan",
                "gp_register_template",
                "gp_render",
                "gp_replan",
            ]

    async def test_tool_schemas_have_type_object(self) -> None:
        server = create_server()
        async with create_connected_server_and_client_session(server) as session:
            result = await session.list_tools()
            for tool in result.tools:
                assert tool.inputSchema["type"] == "object"


# ---------------------------------------------------------------------------
# Budget round-trip
# ---------------------------------------------------------------------------


class TestServerBudgetRoundTrip:
    async def test_gp_estimate_via_mcp(self) -> None:
        server = create_server()
        async with create_connected_server_and_client_session(server) as session:
            r = await _call(session, "gp_estimate", {
                "text": "Hello world",
                "format": "latex",
            })
            assert r["ok"] is True
            assert r["multiplier_applied"] == 1.3
            assert r["estimated_tokens"] > 0

    async def test_gp_budget_via_mcp(self) -> None:
        server = create_server()
        async with create_connected_server_and_client_session(server) as session:
            r = await _call(session, "gp_budget", {
                "conversation_tokens": 50_000,
            })
            assert r["ok"] is True
            assert r["recommendation"] == "direct"
            assert r["estimated_headroom"] == 950_000


# ---------------------------------------------------------------------------
# Planner round-trip
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_isolate_state")
class TestServerPlannerRoundTrip:
    async def test_gp_plan_via_mcp(self) -> None:
        server = create_server()
        async with create_connected_server_and_client_session(server) as session:
            r = await _call(session, "gp_plan", {
                "description": "Short memo",
                "estimated_content_tokens": 1000,
                "available_headroom": 100_000,
            })
            assert r["ok"] is True
            assert r["plan_id"].startswith("plan_")
            assert r["strategy"] == "direct"

    async def test_gp_replan_via_mcp(self) -> None:
        server = create_server()
        async with create_connected_server_and_client_session(server) as session:
            plan = await _call(session, "gp_plan", {
                "description": "Report",
                "estimated_content_tokens": 5000,
                "available_headroom": 50_000,
            })
            revised = await _call(session, "gp_replan", {
                "plan_id": plan["plan_id"],
                "failure_mode": "empty_response",
            })
            assert revised["ok"] is True
            assert revised["plan_id"] != plan["plan_id"]


# ---------------------------------------------------------------------------
# Renderer round-trip
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_isolate_state")
class TestServerRendererRoundTrip:
    async def test_register_and_render_via_mcp(
        self, tmp_path: Path
    ) -> None:
        server = create_server()
        async with create_connected_server_and_client_session(server) as session:
            reg = await _call(session, "gp_register_template", {
                "name": "greet",
                "content": "Hello {{ name }}!",
                "format": "text",
            })
            assert reg["ok"] is True

            out = str(tmp_path / "greet.txt")
            ren = await _call(session, "gp_render", {
                "template": "greet",
                "data": {"name": "World"},
                "output_path": out,
            })
            assert ren["ok"] is True
            assert Path(out).read_text() == "Hello World!"

    async def test_list_templates_via_mcp(self) -> None:
        server = create_server()
        async with create_connected_server_and_client_session(server) as session:
            before = await _call(session, "gp_list_templates", {})
            builtin_count = len(before["templates"])
            assert builtin_count >= 4  # 4 bundled templates

            await _call(session, "gp_register_template", {
                "name": "t1",
                "content": "{{ x }}",
                "format": "text",
            })
            listed = await _call(session, "gp_list_templates", {})
            assert len(listed["templates"]) == builtin_count + 1

    async def test_render_missing_template_via_mcp(
        self, tmp_path: Path
    ) -> None:
        server = create_server()
        async with create_connected_server_and_client_session(server) as session:
            r = await _call(session, "gp_render", {
                "template": "nonexistent",
                "data": {},
                "output_path": str(tmp_path / "x.txt"),
            })
            assert r["ok"] is False
            assert "not found" in r["error"]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestServerErrorHandling:
    async def test_unknown_tool_returns_error(self) -> None:
        server = create_server()
        async with create_connected_server_and_client_session(server) as session:
            r = await _call(session, "gp_nonexistent", {})
            assert r["ok"] is False
            assert "Unknown tool" in r["error"]

    @pytest.mark.usefixtures("_isolate_state")
    async def test_handler_exception_returns_structured_error(
        self, tmp_path: Path
    ) -> None:
        """Handler exceptions should return structured error, not crash."""
        server = create_server()
        async with create_connected_server_and_client_session(server) as session:
            # Register a valid template first
            await _call(session, "gp_register_template", {
                "name": "crash_tpl",
                "content": "{{ x }}",
                "format": "text",
            })
            # Render to an impossible path to trigger an OS error
            r = await _call(session, "gp_render", {
                "template": "crash_tpl",
                "data": {"x": "hello"},
                "output_path": "/proc/nonexistent/impossible.txt",
            })
            assert r["ok"] is False
            assert "Internal error" in r["error"]


# ---------------------------------------------------------------------------
# End-to-end pipeline
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_isolate_state")
class TestServerEndToEnd:
    async def test_budget_plan_render_pipeline(
        self, tmp_path: Path
    ) -> None:
        """Full workflow: estimate -> budget -> plan -> register -> render."""
        server = create_server()
        async with create_connected_server_and_client_session(server) as session:
            # 1. Estimate content tokens
            est = await _call(session, "gp_estimate", {
                "text": "Paper evaluation data " * 100,
                "format": "latex",
            })
            assert est["ok"] is True
            tokens = est["estimated_tokens"]

            # 2. Check budget
            bud = await _call(session, "gp_budget", {
                "conversation_tokens": 150_000,
            })
            assert bud["ok"] is True
            headroom = bud["estimated_headroom"]

            # 3. Create plan
            plan = await _call(session, "gp_plan", {
                "description": "Evaluation report",
                "target_format": "markdown",
                "estimated_content_tokens": tokens,
                "available_headroom": headroom,
            })
            assert plan["ok"] is True

            # 4. Register template
            reg = await _call(session, "gp_register_template", {
                "name": "eval_md",
                "content": "# {{ title }}\n\n{{ body }}",
                "format": "markdown",
            })
            assert reg["ok"] is True

            # 5. Render
            out = str(tmp_path / "report.md")
            ren = await _call(session, "gp_render", {
                "template": "eval_md",
                "data": {
                    "title": "Evaluation Report",
                    "body": "Results look good.",
                },
                "output_path": out,
            })
            assert ren["ok"] is True
            content = Path(out).read_text()
            assert "Evaluation Report" in content
            assert "Results look good." in content
