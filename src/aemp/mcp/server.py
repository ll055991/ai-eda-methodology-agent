"""Methodology MCP Server (M7.2): the agent-facing capability protocol.

    MCP client -> [MCP] -> THIS SERVER -> [HTTP] -> Platform API -> AEMP core

The server is an **adapter and nothing else** (spec §2/§50):

* it never touches EDA, Docker, ``subprocess``, SQLite or ``runs/``;
* every capability it exposes is one Platform HTTP call;
* it never re-implements Flow/QA judgment, thresholds or measurements;
* it holds no PolicyEngine/ApprovalManager: permission belongs to the
  agent runtime, capability belongs here.

Tool argument schemas are the handler signatures below - the MCP SDK
derives the JSON Schema, so the declaration exists exactly once. Results
are returned as ``CallToolResult`` with ``structuredContent`` and
``isError`` set from the outcome: a failure is data, not a crash.

Configuration (environment): see :mod:`aemp.mcp.config`.

Run it::

    python scripts/run_mcp_server.py
    uvicorn aemp.mcp.server:app_factory --factory --host 127.0.0.1 --port 8770
"""

from __future__ import annotations

import json
import os
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from aemp import __version__
from aemp.core.config import discover_settings
from aemp.core.logging import get_logger, setup_logging
from aemp.mcp.client import PlatformHttpClient
from aemp.mcp.config import (
    DEFAULT_SERVER_NAME,
    DEFAULT_SERVER_VERSION,
    McpServerConfig,
)
from aemp.mcp.resources import ResourceService, ResourceSpec
from aemp.mcp.tools import TOOL_NAMES, McpToolService, ToolOutcome

__all__ = ["SERVER_NAME", "SERVER_VERSION", "app_factory", "build_server", "main"]

logger = get_logger(__name__)

#: Defaults of the published MCP interface (overridable through
#: ``AEMP_MCP_SERVER_NAME`` / ``AEMP_MCP_SERVER_VERSION``).
SERVER_NAME = DEFAULT_SERVER_NAME
SERVER_VERSION = DEFAULT_SERVER_VERSION

STREAMABLE_HTTP_PATH = "/mcp"
HEALTH_PATH = "/healthz"

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8770

INSTRUCTIONS = (
    "Methodology MCP server for the AI EDA Methodology & QA Platform. "
    "Exposes the Platform capabilities run_flow/get_run/run_qa/get_artifacts "
    "and read-only methodology resources. It cannot execute EDA tools, run "
    "shell commands or read arbitrary files: every capability is a Platform "
    "HTTP API call, and permission decisions belong to the agent runtime."
)

_RUN_ID_DESCRIPTION = "Run id returned by run_flow."


# -- tool registration --------------------------------------------------------


def _register_tools(server: MCPServer, service: McpToolService) -> None:
    """Register exactly the four contract tools (no dynamic registration)."""

    @server.tool(
        name="run_flow",
        description=(
            "Execute a named flow spec through the Platform API as one tracked "
            "run. Returns the run id and its execution/engineering statuses. "
            "High-risk: the calling agent must obtain human approval first."
        ),
        structured_output=False,
    )
    def run_flow(
        flow: Annotated[
            str,
            Field(min_length=1, description="Flow spec path as known to the Platform."),
        ],
        inputs: Annotated[
            dict[str, str] | None,
            Field(description="Reserved; the Platform rejects overrides today."),
        ] = None,
    ) -> CallToolResult:
        return _to_result(service.run_flow(flow, inputs))

    @server.tool(
        name="get_run",
        description=(
            "Read one run: lifecycle/execution/engineering statuses, parsed "
            "metrics, artifacts and diagnostics."
        ),
        structured_output=False,
    )
    def get_run(
        run_id: Annotated[str, Field(description=_RUN_ID_DESCRIPTION)],
    ) -> CallToolResult:
        return _to_result(service.get_run(run_id))

    @server.tool(
        name="run_qa",
        description=(
            "Evaluate a QA rule pack against a run's recorded metrics. The "
            "verdict (PASS/FAIL/UNKNOWN) comes from the Platform QA engine."
        ),
        structured_output=False,
    )
    def run_qa(
        run_id: Annotated[str, Field(description=_RUN_ID_DESCRIPTION)],
        rule_pack: Annotated[
            str,
            Field(min_length=1, description="QA rule pack path as known to the Platform."),
        ],
    ) -> CallToolResult:
        return _to_result(service.run_qa(run_id, rule_pack))

    @server.tool(
        name="get_artifacts",
        description=(
            "List the artifacts recorded by a run (roles, paths, sizes and "
            "SHA-256 digests) - metadata only, never file contents."
        ),
        structured_output=False,
    )
    def get_artifacts(
        run_id: Annotated[str, Field(description=_RUN_ID_DESCRIPTION)],
    ) -> CallToolResult:
        return _to_result(service.get_artifacts(run_id))


def _to_result(outcome: ToolOutcome) -> CallToolResult:
    """One tool outcome -> one MCP result (structured, honest about failure)."""
    document = outcome.to_document()
    return CallToolResult(
        content=[
            TextContent(type="text", text=json.dumps(document, ensure_ascii=False))
        ],
        structured_content=document,
        is_error=not outcome.ok,
    )


# -- resource registration ----------------------------------------------------


def _register_resources(server: MCPServer, resources: ResourceService) -> None:
    """Register the enumerated, allowlisted resources (read-only knowledge).

    Every resource is registered by its concrete URI: the catalog is small
    and fully knowable, so nothing depends on template matching and an
    unknown URI simply does not exist.
    """
    for spec in resources.resources():
        _register_resource(server, resources, spec)


def _register_resource(
    server: MCPServer, resources: ResourceService, spec: ResourceSpec
) -> None:
    def handler() -> str:
        return resources.read(spec.uri).text

    handler.__name__ = spec.name.replace("-", "_").replace(".", "_")
    handler.__doc__ = spec.description or None
    server.resource(
        spec.uri,
        name=spec.name,
        title=spec.title,
        description=spec.description,
        mime_type=spec.mime_type,
    )(handler)


# -- health --------------------------------------------------------------------


def _register_health(server: MCPServer, config: McpServerConfig) -> None:
    """Liveness of the MCP process itself - never Platform/EDA readiness.

    The MCP protocol itself needs no extra endpoint; this is the operator
    health check the spec asks for (§37), and it deliberately says nothing
    about whether the Platform (or Docker/EDA behind it) is usable.
    """

    @server.custom_route(HEALTH_PATH, methods=["GET"], include_in_schema=False)
    async def healthz(_request: Request) -> Response:
        return JSONResponse(
            {
                "status": "ok",
                "server": config.server_name,
                "version": config.server_version,
                "mcp_path": STREAMABLE_HTTP_PATH,
            }
        )


# -- server assembly ----------------------------------------------------------


def build_server(
    config: McpServerConfig,
    *,
    tools: McpToolService | None = None,
    resources: ResourceService | None = None,
) -> MCPServer:
    """Build the Methodology MCP server (tools + resources).

    Args:
        config: Boundary configuration (bind address, Platform URL, roots).
        tools: Optional tool service (tests inject a fake Platform client).
        resources: Optional resource service (tests inject temp roots).
    """
    tool_service = tools or McpToolService(
        PlatformHttpClient(config.platform_api_url, timeout_s=config.request_timeout_s),
        server_name=config.server_name,
    )
    resource_service = resources or ResourceService(
        skills_dir=config.skills_dir,
        docs_dir=config.docs_dir,
        max_bytes=config.max_resource_bytes,
    )
    server = MCPServer(
        name=config.server_name,
        version=config.server_version,
        instructions=INSTRUCTIONS,
    )
    _register_tools(server, tool_service)
    _register_resources(server, resource_service)
    _register_health(server, config)
    logger.info(
        "mcp server built: name=%s version=%s platform=%s platform_version=%s "
        "tools=%d resources=%d patterns=%s",
        config.server_name,
        config.server_version,
        config.platform_api_url,
        __version__,
        len(TOOL_NAMES),
        len(resource_service.resources()),
        ", ".join(spec.uri for spec in resource_service.templates()) or "(none)",
    )
    return server


def streamable_http_app(
    config: McpServerConfig | None = None,
    *,
    tools: McpToolService | None = None,
    resources: ResourceService | None = None,
    json_response: bool = True,
) -> Any:
    """The ASGI app serving this server over Streamable HTTP at ``/mcp``."""
    resolved = config or _config_from_env()
    server = build_server(resolved, tools=tools, resources=resources)
    return server.streamable_http_app(
        streamable_http_path=STREAMABLE_HTTP_PATH,
        json_response=json_response,
        host=resolved.host,
    )


def app_factory() -> Any:
    """uvicorn factory entry point (``--factory``)."""
    return streamable_http_app()


def _config_from_env() -> McpServerConfig:
    settings = discover_settings()
    return McpServerConfig.from_env(project_root=settings.project_root)


def main() -> None:
    """Run the MCP server with environment-driven configuration."""
    import uvicorn

    level = os.environ.get("AEMP_LOG_LEVEL", "INFO")
    setup_logging(level)
    config = _config_from_env()
    app = streamable_http_app(config)
    logger.info(
        "starting methodology mcp server: host=%s port=%s path=%s platform=%s",
        config.host,
        config.port,
        STREAMABLE_HTTP_PATH,
        config.platform_api_url,
    )
    uvicorn.run(app, host=config.host, port=config.port, log_level=level.lower())


if __name__ == "__main__":  # pragma: no cover - manual launch
    main()
