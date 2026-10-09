"""Start the Platform HTTP server (M6.1) with environment-driven config.

Usage (from the repository root)::

    python scripts/run_platform_server.py

Environment:
    AEMP_HOST=127.0.0.1   AEMP_PORT=8000   AEMP_LOG_LEVEL=INFO
    AEMP_CORS_ORIGINS=    (empty = CORS disabled)
"""

from aemp.api.http import main

if __name__ == "__main__":
    main()
