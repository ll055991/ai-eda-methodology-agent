"""FastAPI transport adapter for the Agent API (M6.2).

Transport only: routes validate against the shared agent contract and
delegate to :class:`~agent_backend.application.chat.ChatService`. No LLM,
no Platform access from this layer.

The published ``/openapi.json`` IS ``contracts/agent-api.yaml`` (external
``$ref``s inlined), mirroring the Platform server's contract discipline.

Configuration (environment):
    AGENT_HOST / AGENT_PORT / AGENT_LOG_LEVEL
    AEMP_PLATFORM_BASE_URL / AGENT_REQUEST_TIMEOUT_S / AEMP_CONTRACTS_DIR

Run it::

    python -m agent_backend.api.http
    uvicorn agent_backend.api.http:app
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agent_backend.application.chat import ChatService
from agent_backend.application.factory import create_agent_runtime
from agent_backend.config import AgentConfig
from agent_backend.contracts import ContractViolation, validate_document
from agent_backend.errors import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    AgentError,
    error_document,
)
from agent_backend.logging import get_logger, setup_logging
from agent_backend.models.chat import ChatRequest, ChatResumeRequest

__all__ = ["create_app", "main"]

logger = get_logger(__name__)

CONTRACT_FILENAME = "agent-api.yaml"
API_TITLE = "AEMP Agent API"
API_VERSION = "1.1.0"

_OPENAPI_CACHE: dict[str, dict[str, Any]] = {}


def create_app(
    *,
    config: AgentConfig | None = None,
    chat_service: ChatService | None = None,
) -> FastAPI:
    """Build the Agent HTTP app.

    Args:
        config: Agent settings; defaults to :meth:`AgentConfig.from_env`.
        chat_service: Application service (tests inject runtimes here).
    """
    resolved = config or AgentConfig.from_env()
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=(
            "Agent Backend API (M6.3, synchronous; runtime selected by "
            "AEMP_AGENT_RUNTIME: stub | llm; no tool calling yet). Contract "
            "source: contracts/agent-api.yaml (this /openapi.json document)."
        ),
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.config = resolved
    app.state.chat_service = chat_service or ChatService(
        runtime=create_agent_runtime(resolved)
    )
    app.openapi = lambda: _contract_openapi(resolved.contracts_dir)

    _install_middleware(app)
    _install_error_handlers(app)
    _install_routes(app)
    return app


# -- middleware --------------------------------------------------------------


def _install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_log(request: Request, call_next):
        """Log request_id/method/path/status/duration_ms. Never bodies."""
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        started = time.monotonic()
        response = await call_next(request)
        duration_ms = int((time.monotonic() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "%s %s -> %d (%d ms) request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )
        return response


# -- error handling -----------------------------------------------------------


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AgentError)
    async def agent_error_handler(_request: Request, exc: AgentError) -> JSONResponse:
        return JSONResponse(error_document(exc), status_code=exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = {
            "errors": [
                {
                    "loc": ".".join(str(part) for part in error.get("loc", ())),
                    "type": str(error.get("type", "")),
                }
                for error in exc.errors()
            ]
        }
        document = error_document(
            AgentError(INVALID_REQUEST, "Request validation failed", details=details)
        )
        return JSONResponse(document, status_code=400)

    @app.exception_handler(Exception)
    async def internal_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error: %s", exc)
        document = error_document(AgentError(INTERNAL_ERROR, "Internal server error"))
        return JSONResponse(document, status_code=500)


# -- routes -------------------------------------------------------------------


def _install_routes(app: FastAPI) -> None:
    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        """Liveness of the Agent process only; never calls the Platform."""
        return {"status": "ok"}

    @app.post("/api/v1/chat", tags=["chat"])
    def chat(
        request: Request, body: Annotated[dict[str, Any], Body()]
    ) -> JSONResponse:
        config: AgentConfig = request.app.state.config
        _validate_contract("agent-chat-request", body, config)

        chat_request = ChatRequest(
            message=str(body["message"]),
            session_id=body.get("session_id"),
        )
        response = request.app.state.chat_service.handle(chat_request)
        return _agent_response(response, config)

    @app.post("/api/v1/chat/resume", tags=["chat"])
    def chat_resume(
        request: Request, body: Annotated[dict[str, Any], Body()]
    ) -> JSONResponse:
        """Resume an interrupted execution after a human decision (M6.6)."""
        config: AgentConfig = request.app.state.config
        _validate_contract("agent-chat-resume-request", body, config)

        resume_request = ChatResumeRequest(
            session_id=str(body["session_id"]),
            approval_id=str(body["approval_id"]),
            decision=str(body["decision"]),
            reason=body.get("reason"),
        )
        response = request.app.state.chat_service.resume(resume_request)
        return _agent_response(response, config)


# -- helpers ------------------------------------------------------------------


def _agent_response(response: Any, config: AgentConfig) -> JSONResponse:
    """Serialize a ChatResponse and enforce the response contract."""
    document = response.to_dict()
    try:
        validate_document("agent-chat-response", document, config.contracts_dir)
    except ContractViolation as exc:
        # our own response violating the contract is a bug, never a 4xx
        raise AgentError(
            INTERNAL_ERROR, "Agent response violates the agent contract"
        ) from exc
    return JSONResponse(document)


def _validate_contract(name: str, document: Any, config: AgentConfig) -> None:
    try:
        validate_document(name, document, config.contracts_dir)
    except ContractViolation as exc:
        raise AgentError(
            INVALID_REQUEST,
            f"Request does not match the {name} contract",
            details={"detail": exc.detail},
        ) from exc


def _contract_openapi(contracts_dir: Path) -> dict[str, Any]:
    """Serve the contract itself as /openapi.json (design source)."""
    key = str(contracts_dir)
    if key not in _OPENAPI_CACHE:
        path = contracts_dir / CONTRACT_FILENAME
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        _OPENAPI_CACHE[key] = _resolve_external_refs(document, contracts_dir, {})
    return _OPENAPI_CACHE[key]


def _resolve_external_refs(
    node: Any, contracts_dir: Path, cache: dict[str, Any]
) -> Any:
    """Inline external ``$ref: ./schemas/*.json`` targets for the served doc."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#"):
            if ref not in cache:
                target = json.loads((contracts_dir / ref).read_text(encoding="utf-8"))
                cache[ref] = _resolve_external_refs(target, contracts_dir, cache)
            return cache[ref]
        return {
            key: _resolve_external_refs(value, contracts_dir, cache)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_resolve_external_refs(item, contracts_dir, cache) for item in node]
    return node


app = create_app()


def main() -> None:
    """Run the Agent HTTP server with environment-driven configuration."""
    import uvicorn

    config = AgentConfig.from_env()
    setup_logging(config.log_level)
    uvicorn.run(
        "agent_backend.api.http:app",
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower(),
    )


if __name__ == "__main__":
    main()
