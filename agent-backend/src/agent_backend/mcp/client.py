"""MCPClient: the agent's MCP transport over Streamable HTTP (M7.2).

One direction only (spec §19)::

    LangGraph runtime -> MCPGateway -> MCPClient -> MCP server -> Platform HTTP

The runtime never sees the MCP SDK: this class is the whole seam. It is a
**synchronous facade** because the agent runtime is synchronous (the HTTP
layer runs it in a worker thread): a private event loop lives on a daemon
thread, the SDK's async ``Client`` is entered there once, and every public
method submits one coroutine to that loop with a hard deadline.

Error discipline (spec §34): no SDK exception ever escapes. ``ExceptionGroup``
from anyio, ``httpx`` transport errors and protocol errors are unwrapped and
normalized into :class:`~agent_backend.mcp.errors.MCPClientError` codes.
There is no automatic fallback to the local tools (spec §35).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from contextlib import AsyncExitStack
from typing import Any, TypeVar

from mcp.client.client import Client as SdkClient

from agent_backend.logging import get_logger
from agent_backend.mcp.errors import (
    MCP_CALL_TIMEOUT,
    MCP_CONNECTION_FAILED,
    MCP_INVALID_RESULT,
    MCP_RESOURCE_READ_FAILED,
    MCP_SERVER_UNAVAILABLE,
    MCP_TOOL_CALL_FAILED,
    MCPClientError,
)
from agent_backend.mcp.models import (
    McpResourceContent,
    McpResourceSpec,
    McpServerSpec,
    McpToolCallResult,
    McpToolSpec,
)

__all__ = ["MCPClient"]

logger = get_logger(__name__)

_T = TypeVar("_T")


class _DeadlineExceeded(Exception):
    """Internal: one submitted coroutine missed its deadline."""


class _EventLoopThread:
    """A daemon thread owning one asyncio loop (the SDK is async-only)."""

    def __init__(self, name: str) -> None:
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise MCPClientError(MCP_CONNECTION_FAILED, "mcp event loop did not start")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()
        loop.close()

    def submit(self, coroutine: Coroutine[Any, Any, _T], timeout: float) -> _T:
        """Run one coroutine on the loop and wait for it (bounded)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            raise MCPClientError(MCP_CONNECTION_FAILED, "mcp event loop is not running")
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout=timeout)
        except TimeoutError as exc:
            future.cancel()
            raise _DeadlineExceeded(f"exceeded {timeout:g}s") from exc
        except BaseException:
            future.cancel()
            raise

    def stop(self) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)


class MCPClient:
    """Synchronous MCP client for one server (connect / discover / call).

    Args:
        server: The server to talk to (name + streamable HTTP URL).
        connect_timeout_s: Deadline for connect + initialize.
        call_timeout_s: Deadline for one discovery/call/read.
    """

    def __init__(
        self,
        server: McpServerSpec,
        *,
        connect_timeout_s: float = 5.0,
        call_timeout_s: float = 30.0,
    ) -> None:
        if connect_timeout_s <= 0 or call_timeout_s <= 0:
            raise ValueError("mcp timeouts must be positive")
        self._server = server
        self._connect_timeout_s = connect_timeout_s
        self._call_timeout_s = call_timeout_s
        self._loop: _EventLoopThread | None = None
        self._stack: AsyncExitStack | None = None
        self._client: SdkClient | None = None
        self._lock = threading.Lock()
        self._server_info: dict[str, Any] | None = None

    # -- lifecycle ----------------------------------------------------------

    def connect(self) -> None:
        """Connect and initialize once (idempotent, thread-safe).

        Raises:
            MCPClientError: MCP_SERVER_UNAVAILABLE / MCP_CONNECTION_FAILED /
                MCP_CALL_TIMEOUT when the server cannot be reached.
        """
        with self._lock:
            if self._client is not None:
                return
            loop = self._loop or _EventLoopThread(f"mcp-{self._server.name}")
            self._loop = loop
            try:
                loop.submit(self._aconnect(), self._connect_timeout_s)
            except _DeadlineExceeded as exc:
                self._discard()
                raise MCPClientError(
                    MCP_SERVER_UNAVAILABLE,
                    f"mcp server did not answer within {self._connect_timeout_s:g}s",
                ) from exc
            except MCPClientError:
                self._discard()
                raise
            except BaseException as exc:  # noqa: BLE001 - normalized below
                self._discard()
                raise _normalize_connect_error(exc) from exc
            logger.info(
                "mcp connected: server=%s url=%s info=%s",
                self._server.name,
                self._server.url,
                self._server_info,
            )

    async def _aconnect(self) -> None:
        stack = AsyncExitStack()
        client = await stack.enter_async_context(
            SdkClient(self._server.url, read_timeout_seconds=self._call_timeout_s)
        )
        self._stack = stack
        self._client = client
        info = getattr(client, "server_info", None)
        self._server_info = (
            {"name": getattr(info, "name", ""), "version": getattr(info, "version", "")}
            if info is not None
            else None
        )

    def close(self) -> None:
        """Close the session and stop the loop; safe to call repeatedly."""
        loop = self._loop
        stack = self._stack
        if loop is not None and stack is not None:
            try:
                loop.submit(stack.aclose(), self._call_timeout_s)
            except BaseException as exc:  # noqa: BLE001 - closing is best effort
                logger.warning(
                    "mcp close failed: server=%s error=%s",
                    self._server.name,
                    type(exc).__name__,
                )
        self._discard()

    def _discard(self) -> None:
        loop, self._loop = self._loop, None
        self._stack = None
        self._client = None
        self._server_info = None
        if loop is not None:
            loop.stop()

    def __enter__(self) -> MCPClient:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def server(self) -> McpServerSpec:
        return self._server

    @property
    def server_info(self) -> dict[str, Any] | None:
        """Handshake result (name/version), or ``None`` before connecting."""
        return self._server_info

    # -- discovery ----------------------------------------------------------

    def list_tools(self) -> tuple[McpToolSpec, ...]:
        """``tools/list`` - what the server offers (discovery is not permission)."""
        result = self._call(self._alist_tools(), "tools/list")
        specs: list[McpToolSpec] = []
        for tool in getattr(result, "tools", ()) or ():
            name = str(getattr(tool, "name", "") or "")
            if not name:
                continue
            schema = getattr(tool, "input_schema", None)
            specs.append(
                McpToolSpec(
                    name=name,
                    description=str(getattr(tool, "description", "") or ""),
                    input_schema=dict(schema) if isinstance(schema, dict) else {},
                    title=getattr(tool, "title", None),
                    server_name=self._server.name,
                )
            )
        return tuple(specs)

    async def _alist_tools(self) -> Any:
        return await self._require_client().list_tools()

    def list_resources(self) -> tuple[McpResourceSpec, ...]:
        """``resources/list`` + ``resources/templates/list`` (metadata only)."""
        specs: list[McpResourceSpec] = []
        listed = self._call(self._alist_resources(), "resources/list")
        for resource in getattr(listed, "resources", ()) or ():
            specs.append(
                McpResourceSpec(
                    uri=str(getattr(resource, "uri", "") or ""),
                    name=str(getattr(resource, "name", "") or ""),
                    description=str(getattr(resource, "description", "") or ""),
                    mime_type=str(getattr(resource, "mime_type", "") or ""),
                    server_name=self._server.name,
                )
            )
        templates = self._call(self._alist_templates(), "resources/templates/list")
        for template in getattr(templates, "resource_templates", ()) or ():
            specs.append(
                McpResourceSpec(
                    uri=str(getattr(template, "uri_template", "") or ""),
                    name=str(getattr(template, "name", "") or ""),
                    description=str(getattr(template, "description", "") or ""),
                    mime_type=str(getattr(template, "mime_type", "") or ""),
                    server_name=self._server.name,
                    template=True,
                )
            )
        return tuple(specs)

    async def _alist_resources(self) -> Any:
        return await self._require_client().list_resources()

    async def _alist_templates(self) -> Any:
        return await self._require_client().list_resource_templates()

    # -- calls --------------------------------------------------------------

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> McpToolCallResult:
        """``tools/call`` - the only way a capability is executed."""
        result = self._call(self._acall_tool(name, arguments or {}), f"tools/call {name}")
        structured = getattr(result, "structured_content", None)
        texts = [
            getattr(item, "text", None)
            for item in (getattr(result, "content", ()) or ())
            if getattr(item, "text", None) is not None
        ]
        text = "\n".join(str(part) for part in texts) or None
        is_error = bool(getattr(result, "is_error", False))
        if structured is None and text is None:
            raise MCPClientError(
                MCP_INVALID_RESULT, f"mcp tool {name!r} returned no usable content"
            )
        return McpToolCallResult(
            is_error=is_error,
            structured=dict(structured) if isinstance(structured, dict) else None,
            text=text,
        )

    async def _acall_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self._require_client().call_tool(name, arguments)

    def read_resource(self, uri: str) -> McpResourceContent:
        """``resources/read`` - read-only knowledge, never execution."""
        result = self._call(self._aread_resource(uri), "resources/read")
        for item in getattr(result, "contents", ()) or ():
            text = getattr(item, "text", None)
            if text is None:
                continue
            return McpResourceContent(
                uri=str(getattr(item, "uri", uri) or uri),
                mime_type=str(getattr(item, "mime_type", "") or ""),
                text=str(text),
            )
        raise MCPClientError(
            MCP_INVALID_RESULT, f"mcp resource {uri!r} returned no text content"
        )

    async def _aread_resource(self, uri: str) -> Any:
        return await self._require_client().read_resource(uri)

    # -- internals ----------------------------------------------------------

    def _require_client(self) -> SdkClient:
        client = self._client
        if client is None:
            raise MCPClientError(
                MCP_CONNECTION_FAILED, "mcp client is not connected"
            )
        return client

    def _call(self, coroutine: Coroutine[Any, Any, _T], what: str) -> _T:
        """Run one SDK coroutine on the loop, normalizing every failure."""
        try:
            self.connect()
        except BaseException:
            coroutine.close()  # never scheduled: do not leak an un-awaited coroutine
            raise
        loop = self._loop
        if loop is None:  # pragma: no cover - connect() guarantees it
            coroutine.close()
            raise MCPClientError(MCP_CONNECTION_FAILED, "mcp client is not connected")
        try:
            return loop.submit(coroutine, self._call_timeout_s)
        except _DeadlineExceeded as exc:
            raise MCPClientError(
                MCP_CALL_TIMEOUT, f"mcp {what} exceeded {self._call_timeout_s:g}s"
            ) from exc
        except MCPClientError:
            raise
        except BaseException as exc:  # noqa: BLE001 - normalized below
            raise _normalize_call_error(exc, what) from exc


# -- error normalization ------------------------------------------------------


def _root_cause(exc: BaseException) -> BaseException:
    """Unwrap anyio ``ExceptionGroup``/``BaseExceptionGroup`` nesting."""
    seen: set[int] = set()
    current = exc
    while isinstance(current, BaseExceptionGroup) and current.exceptions:
        if id(current) in seen:  # pragma: no cover - defensive
            break
        seen.add(id(current))
        current = current.exceptions[0]
    return current


def _normalize_connect_error(exc: BaseException) -> MCPClientError:
    root = _root_cause(exc)
    name = type(root).__name__
    if isinstance(root, TimeoutError) or _is_timeout_text(root) or _looks_unreachable(root):
        return MCPClientError(
            MCP_SERVER_UNAVAILABLE, f"mcp server is unavailable ({name})"
        )
    return MCPClientError(MCP_CONNECTION_FAILED, f"mcp connection failed ({name})")


def _normalize_call_error(exc: BaseException, what: str) -> MCPClientError:
    root = _root_cause(exc)
    name = type(root).__name__
    if isinstance(root, TimeoutError) or _is_timeout_text(root):
        return MCPClientError(MCP_CALL_TIMEOUT, f"mcp {what} timed out ({name})")
    if _looks_unreachable(root):
        return MCPClientError(
            MCP_SERVER_UNAVAILABLE, f"mcp server is unavailable ({what})"
        )
    if what.startswith("resources/"):
        return MCPClientError(
            MCP_RESOURCE_READ_FAILED, f"mcp {what} failed ({name})"
        )
    return MCPClientError(MCP_TOOL_CALL_FAILED, f"mcp {what} failed ({name})")


def _is_timeout_text(exc: BaseException) -> bool:
    """The SDK reports transport deadlines as protocol errors, not TimeoutError."""
    return "timed out" in str(exc).lower()


def _looks_unreachable(exc: BaseException) -> bool:
    """True for transport errors that mean 'nothing is listening'."""
    name = type(exc).__name__
    if name in (
        "ConnectError",
        "ConnectTimeout",
        "ConnectionError",
        "ConnectionRefusedError",
    ):
        return True
    text = str(exc).lower()
    return "refused" in text or ("connect" in text and "error" in text)
