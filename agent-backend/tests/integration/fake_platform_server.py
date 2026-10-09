"""Minimal contract-shaped fake Platform HTTP server for agent tests.

The Agent Backend's constitution forbids importing Platform modules to
construct internal state, and the M7.2 spec allows a fake/fixture Platform
at the end of the chain (spec §39). This stdlib server answers the exact
Platform API endpoints the MCP tools adapt, so the agent side can be tested
against contract-shaped documents without a real EDA environment.

Every request is appended to ``--log`` as one ``METHOD /path`` line, which
is how the tests prove whether a call actually travelled
agent -> MCP -> Platform HTTP (or provably did not).

The real Platform over real run directories is covered by the platform-side
integration suite (``tests/integration/test_mcp_server.py``).

Usage:
    python fake_platform_server.py --port 12345 --log out.log --run-id ID
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

RUN_DOCUMENT: dict[str, Any] = {
    "run_id": "20260101T000000Z-abcdef",
    "operation": "opensta.timing",
    "step_id": "sta",
    "lifecycle_status": "success",
    "execution_status": "success",
    "engineering_status": "unknown",
    "tool": {"name": "opensta", "version": "2.6.0"},
    "metrics": {"sta.wns_ns": -0.01, "sta.tns_ns": -0.02},
    "inputs": [],
    "artifacts": [
        {
            "artifact_id": "artifacts/timing.rpt",
            "role": "sta.report",
            "exists": True,
            "bytes": 22,
            "sha256": "0" * 64,
        }
    ],
    "diagnostics": [],
    "provenance": {"run_status": "success", "tool": "opensta"},
}

QA_DOCUMENT: dict[str, Any] = {
    "run_id": "20260101T000000Z-abcdef",
    "status": "FAIL",
    "rule_pack": {"name": "base_timing", "version": "1.0"},
    "summary": {
        "total_rules": 2,
        "passed": 0,
        "failed": 2,
        "unknown": 0,
        "warnings": 0,
    },
    "findings": [
        {
            "rule_id": "wns_non_negative",
            "metric": "sta.wns_ns",
            "check": "gte",
            "threshold": 0.0,
            "severity": "error",
            "status": "fail",
            "expected": ">= 0.0",
            "actual": -0.01,
            "message": "WNS is negative",
        }
    ],
}

ARTIFACT_LIST: dict[str, Any] = {
    "run_id": "20260101T000000Z-abcdef",
    "artifacts": RUN_DOCUMENT["artifacts"],
}

ENVIRONMENT_ERROR: dict[str, Any] = {
    "error": {
        "code": "TOOL_ENVIRONMENT_UNAVAILABLE",
        "message": "Docker daemon is unreachable (no real EDA in this environment).",
        "details": {},
    }
}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": {}}}


class _Handler(BaseHTTPRequestHandler):
    log_path = ""
    run_id = RUN_DOCUMENT["run_id"]

    # -- request handling ---------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        self._record()
        if self.path == "/healthz":
            self._json(200, {"status": "ok"})
        elif self.path == f"/api/v1/runs/{self.run_id}":
            self._json(200, {**RUN_DOCUMENT, "run_id": self.run_id})
        elif self.path == f"/api/v1/runs/{self.run_id}/artifacts":
            self._json(200, {**ARTIFACT_LIST, "run_id": self.run_id})
        else:
            self._json(404, _error("RUN_NOT_FOUND", f"Run not found: {self.path}"))

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        self._record()
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)  # bodies are never logged
        if self.path == "/api/v1/flows/run":
            # No Docker here: the Platform's own environment verdict.
            self._json(503, ENVIRONMENT_ERROR)
        elif self.path == f"/api/v1/runs/{self.run_id}/qa":
            self._json(200, {**QA_DOCUMENT, "run_id": self.run_id})
        else:
            self._json(404, _error("RUN_NOT_FOUND", f"Run not found: {self.path}"))

    def log_message(self, *args: object) -> None:
        return

    # -- helpers ------------------------------------------------------------

    def _record(self) -> None:
        with open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(f"{self.command} {self.path}\n")

    def _json(self, status: int, document: dict[str, Any]) -> None:
        data = json.dumps(document).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--run-id", default=RUN_DOCUMENT["run_id"])
    args = parser.parse_args()
    _Handler.log_path = args.log
    _Handler.run_id = args.run_id
    HTTPServer(("127.0.0.1", args.port), _Handler).serve_forever()


if __name__ == "__main__":
    main()
