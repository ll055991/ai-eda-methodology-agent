"""Start the Agent Backend HTTP server (M6.2).

Usage (from agent-backend/ or with the package installed)::

    python scripts/run_agent_server.py

Environment:
    AGENT_HOST=127.0.0.1   AGENT_PORT=8100   AGENT_LOG_LEVEL=INFO
    AEMP_PLATFORM_BASE_URL=http://127.0.0.1:8000
"""

from agent_backend.api.http import main

if __name__ == "__main__":
    main()
