"""gen-pilot MCP server — entry point and tool registration.

Tools are organized into three layers:
  Layer 1 (Budget):   gp_budget, gp_estimate
  Layer 2 (Planning): gp_plan, gp_replan
  Layer 3 (Render):   gp_register_template, gp_render, gp_list_templates

Each layer module exports:
  TOOLS: list[Tool]                          — tool definitions
  handle_tool(name, arguments) -> dict       — async handler
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from gen_pilot.tools import budget, planner, renderer

logger = logging.getLogger("gen-pilot")

# Collect all tool definitions from layers
ALL_TOOLS: list[Tool] = budget.TOOLS + renderer.TOOLS + planner.TOOLS

# Map tool names to their handler modules
_HANDLERS: dict[str, Any] = {}
for _mod in (budget, planner, renderer):
    for _tool in _mod.TOOLS:
        _HANDLERS[_tool.name] = _mod


def create_server() -> Server:
    server = Server("gen-pilot")

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[Tool]:
        return ALL_TOOLS

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        mod = _HANDLERS.get(name)
        if mod is None:
            result = {"ok": False, "error": f"Unknown tool: {name}"}
        else:
            result = await mod.handle_tool(name, arguments)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    server = create_server()

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
