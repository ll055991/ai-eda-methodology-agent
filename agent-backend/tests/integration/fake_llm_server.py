"""Minimal OpenAI-compatible fake LLM server for integration tests.

Stdlib only; launched as a subprocess by the test. Responds to
``POST /chat/completions`` with a fixed completion and appends each
received request (path, Authorization header, body) as one JSON line to
``--log`` so the test can assert what the provider actually sent.

Usage:
    python fake_llm_server.py --port 12345 --log /tmp/fake-llm.jsonl
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class _Handler(BaseHTTPRequestHandler):
    log_path = ""
    model_echo = "fake-model"
    tool_call: str | None = None  # when set: first request asks for this tool
    tool_arguments = "{}"
    final_text = "fake-llm answer"
    _request_count = 0

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        body = json.loads(raw) if raw else {}
        with open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "body": body,
                    }
                )
                + "\n"
            )
        _Handler._request_count += 1
        if self.tool_call and _Handler._request_count == 1:
            message: dict[str, object] = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_fake_1",
                        "type": "function",
                        "function": {
                            "name": self.tool_call,
                            "arguments": self.tool_arguments,
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"
        else:
            message = {"role": "assistant", "content": self.final_text}
            finish_reason = "stop"
        payload = {
            "model": body.get("model", self.model_echo),
            "choices": [{"message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("x-request-id", f"fake-req-{_Handler._request_count}")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:  # silence request logging
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--tool-call", default=None, help="tool name for the first reply")
    parser.add_argument("--tool-arguments", default="{}")
    parser.add_argument("--final-text", default="fake-llm answer")
    args = parser.parse_args()
    _Handler.log_path = args.log
    _Handler.tool_call = args.tool_call
    _Handler.tool_arguments = args.tool_arguments
    _Handler.final_text = args.final_text
    server = HTTPServer(("127.0.0.1", args.port), _Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
