"""FastAPI transport adapter for the Platform API contract (M6.1).

The HTTP layer is a Transport Adapter, never a business layer::

    HTTP request -> route -> PlatformApi -> existing deterministic core

Routes never touch drivers, Docker, SQLite or the filesystem directly;
they only call :class:`~aemp.api.services.PlatformApi`, which validates
requests/responses against ``contracts/`` and delegates to the core.

Contract discipline: ``contracts/platform-api.yaml`` is the design source.
The published ``/openapi.json`` IS that contract (external ``$ref``s
inlined), not a document regenerated from the implementation.

Configuration (environment):
    AEMP_HOST       bind host (default 127.0.0.1)
    AEMP_PORT       bind port (default 8000)
    AEMP_LOG_LEVEL  log level (default INFO)
    AEMP_CORS_ORIGINS  comma-separated allowlist; empty = CORS disabled

Run it::

    uvicorn aemp.api.http:app
    python -m aemp.api.http
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

from aemp.api.errors import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    ApiError,
    error_document,
)
from aemp.api.schemas import default_contracts_dir
from aemp.api.services import PlatformApi
from aemp.core.config import discover_settings
from aemp.core.logging import get_logger, setup_logging

__all__ = ["create_app", "main"]

logger = get_logger(__name__)

CONTRACT_FILENAME = "platform-api.yaml"
API_TITLE = "AEMP Platform API"
API_VERSION = "1.0.0"

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000
_DEFAULT_LOG_LEVEL = "INFO"

_OPENAPI_CACHE: dict[str, dict[str, Any]] = {}


def create_app(
    *,
    api: PlatformApi | None = None,
    contracts_dir: Path | None = None,
) -> FastAPI:
    """Build the Platform HTTP app.

    Args:
        api: Optional :class:`PlatformApi` (tests inject one); when
            ``None`` it is created lazily on the first request from the
            discovered project settings.
        contracts_dir: Directory holding the contract files; resolved
            lazily from the project root when ``None``.
    """
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=(
            "Deterministic Platform Backend HTTP API. Contract source: "
            "contracts/platform-api.yaml (this /openapi.json document)."
        ),
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.api = api
    app.state.contracts_dir = Path(contracts_dir) if contracts_dir is not None else None
    app.openapi = lambda: _contract_openapi(_resolve_contracts_dir(app))

    _install_middleware(app)
    _install_error_handlers(app)
    _install_routes(app)
    return app


# -- middleware -------------------------------------------------------------


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

    origins = [
        origin.strip()
        for origin in os.environ.get("AEMP_CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]
    if origins:  # CORS stays disabled unless explicitly configured
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )


# -- error handling ----------------------------------------------------------


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(error_document(exc), status_code=exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # HTTP DTO shape errors are INVALID_REQUEST (400); business
        # validation still happens in PlatformApi/core.
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
            ApiError(INVALID_REQUEST, "Request validation failed", details=details)
        )
        return JSONResponse(document, status_code=400)

    @app.exception_handler(Exception)
    async def internal_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        # Log the traceback server-side; never leak it (or str(exc)) to clients.
        logger.exception("unhandled error: %s", exc)
        document = error_document(ApiError(INTERNAL_ERROR, "Internal server error"))
        return JSONResponse(document, status_code=500)


# -- routes ------------------------------------------------------------------


def _install_routes(app: FastAPI) -> None:
    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        """Liveness of the HTTP server itself (not EDA readiness)."""
        return {"status": "ok"}

    @app.post("/api/v1/flows/run", tags=["flows"])
    def run_flow(
        request: Request, body: Annotated[dict[str, Any], Body()]
    ) -> JSONResponse:
        return JSONResponse(_get_api(request).run_flow(body))

    @app.get("/api/v1/runs", tags=["runs"])
    def list_runs(
        request: Request,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
    ) -> JSONResponse:
        document = _get_api(request).list_runs(limit=limit, offset=offset, status=status)
        return JSONResponse(document)

    @app.get("/api/v1/runs/{run_id}", tags=["runs"])
    def get_run(run_id: str, request: Request) -> JSONResponse:
        return JSONResponse(_get_api(request).get_run(run_id))

    @app.post("/api/v1/runs/{run_id}/qa", tags=["qa"])
    def run_qa(
        run_id: str, request: Request, body: Annotated[dict[str, Any], Body()]
    ) -> JSONResponse:
        return JSONResponse(_get_api(request).run_qa(run_id, body))

    @app.get("/api/v1/runs/{run_id}/artifacts", tags=["artifacts"])
    def list_artifacts(run_id: str, request: Request) -> JSONResponse:
        return JSONResponse(_get_api(request).list_artifacts(run_id))

    # :path keeps run-relative artifact paths addressable (contract:
    # "URL-encoded"); the allowlist check in PlatformApi is the security
    # boundary, never a filesystem join.
    @app.get("/api/v1/runs/{run_id}/artifacts/{artifact_id:path}", tags=["artifacts"])
    def get_artifact(run_id: str, artifact_id: str, request: Request) -> JSONResponse:
        return JSONResponse(_get_api(request).get_artifact(run_id, artifact_id))


# -- helpers -----------------------------------------------------------------


def _get_api(request: Request) -> PlatformApi:
    """Lazily create the application service on first use."""
    api = request.app.state.api
    if api is None:
        api = PlatformApi(
            discover_settings(), contracts_dir=_resolve_contracts_dir(request.app)
        )
        request.app.state.api = api
    return api


def _resolve_contracts_dir(app: FastAPI) -> Path:
    if app.state.contracts_dir is None:
        app.state.contracts_dir = default_contracts_dir()
    return app.state.contracts_dir


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
                target = json.loads(
                    (contracts_dir / ref).read_text(encoding="utf-8")
                )
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
    """Run the server with environment-driven configuration."""
    import uvicorn

    level = os.environ.get("AEMP_LOG_LEVEL", _DEFAULT_LOG_LEVEL)
    setup_logging(level)
    uvicorn.run(
        "aemp.api.http:app",
        host=os.environ.get("AEMP_HOST", _DEFAULT_HOST),
        port=int(os.environ.get("AEMP_PORT", str(_DEFAULT_PORT))),
        log_level=level.lower(),
    )


if __name__ == "__main__":
    main()
