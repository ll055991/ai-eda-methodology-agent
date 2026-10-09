"""Start the Methodology MCP Server (M7.2) over Streamable HTTP.

Usage (from the repository root)::

    python scripts/run_mcp_server.py

Environment:
    AEMP_MCP_HOST=127.0.0.1            bind host
    AEMP_MCP_PORT=8770                 bind port
    AEMP_MCP_PLATFORM_URL=http://127.0.0.1:8765
                                       Platform API this server adapts
    AEMP_MCP_REQUEST_TIMEOUT_S=30
    AEMP_MCP_SKILLS_DIR=agent-backend/skills   read-only resource root
    AEMP_MCP_DOCS_DIR=docs                     read-only resource root
    AEMP_MCP_MAX_RESOURCE_BYTES=65536
    AEMP_LOG_LEVEL=INFO

The endpoint is ``http://<host>:<port>/mcp`` (MCP Streamable HTTP). The
server exposes four tools over the Platform HTTP API and read-only
methodology resources; it has no shell/subprocess/Docker/SQLite access.
"""

from aemp.mcp.server import main

if __name__ == "__main__":
    main()
