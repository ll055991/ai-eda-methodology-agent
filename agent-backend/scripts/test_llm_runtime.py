"""MANUAL real-LLM smoke test (never part of pytest).

Requires a real provider configuration in the environment::

    AEMP_AGENT_RUNTIME=llm
    AEMP_LLM_BASE_URL=https://<provider>/v1
    AEMP_LLM_API_KEY=<secret>          # environment only, never committed
    AEMP_LLM_MODEL=<model-name>

Usage::

    python scripts/test_llm_runtime.py "In one sentence, what is WNS?"
"""

from __future__ import annotations

import sys

from agent_backend.application.factory import create_agent_runtime
from agent_backend.config import AgentConfig
from agent_backend.llm.errors import LLMConfigurationError
from agent_backend.models.chat import ChatRequest


def main() -> int:
    config = AgentConfig.from_env()
    if config.runtime != "llm":
        print("AEMP_AGENT_RUNTIME is not 'llm' - nothing to test.", file=sys.stderr)
        return 2
    try:
        runtime = create_agent_runtime(config)
    except LLMConfigurationError as exc:
        print(f"LLM configuration error: {exc}", file=sys.stderr)
        return 2

    message = " ".join(sys.argv[1:]) or (
        "In one sentence, what does WNS mean in static timing analysis?"
    )
    response = runtime.handle(ChatRequest(message=message))
    print(f"status     : {response.status}")
    print(f"session_id : {response.session_id}")
    print(f"answer     : {response.message}")
    return 0 if response.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
