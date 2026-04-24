"""gen-pilot MCP server — entry point and tool registration.

Tools are organized into three layers:
  Layer 1 (Budget):   gp_budget, gp_estimate
  Layer 2 (Planning): gp_plan, gp_replan
  Layer 3 (Render):   gp_register_template, gp_render, gp_list_templates

Each layer module exports:
  TOOLS: list[Tool]                          — tool definitions
  handle_tool(name, arguments) -> dict       — async handler

Transport modes (--transport flag):
  stdio            — default, stdin/stdout (for Claude Desktop / MCP CLI)
  sse              — HTTP + Server-Sent Events on /sse and /messages/
  streamable-http  — MCP Streamable HTTP on /mcp
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
from typing import Any
from collections.abc import AsyncGenerator

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
            try:
                result = await mod.handle_tool(name, arguments)
            except Exception as exc:
                logger.exception("Tool %s raised: %s", name, exc)
                result = {"ok": False, "error": f"Internal error in {name}: {exc}"}
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    return server


# ---------------------------------------------------------------------------
# Transport: SSE (GET /sse  +  POST /messages/)
# ---------------------------------------------------------------------------

def _run_sse(server: Server, host: str, port: int) -> None:
    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.routing import Mount, Route

    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request: Request) -> None:
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream, server.create_initialization_options()
            )

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse_transport.handle_post_message),
        ]
    )

    logger.info("SSE transport: http://%s:%d/sse", host, port)
    uvicorn.run(app, host=host, port=port)


# ---------------------------------------------------------------------------
# Transport: Streamable HTTP (POST/GET /mcp)
# ---------------------------------------------------------------------------

def _run_streamable_http(server: Server, host: str, port: int) -> None:
    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.routing import Mount

    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=False,
        stateless=False,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None, Any]:
        async with session_manager.run():
            yield

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Mount("/mcp", app=session_manager.handle_request),
        ],
    )

    logger.info("Streamable-HTTP transport: http://%s:%d/mcp", host, port)
    uvicorn.run(app, host=host, port=port)


# ---------------------------------------------------------------------------
# Transport: stdio (default)
# ---------------------------------------------------------------------------

def _run_stdio(server: Server) -> None:
    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="gen-pilot",
        description="gen-pilot MCP server",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="Transport mode (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host for HTTP transports (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port for HTTP transports (default: 8000)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    server = create_server()

    if args.transport == "sse":
        _run_sse(server, args.host, args.port)
    elif args.transport == "streamable-http":
        _run_streamable_http(server, args.host, args.port)
    else:
        _run_stdio(server)


if __name__ == "__main__":
    main()
