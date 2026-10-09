"""REAL HTTP integration: Agent Backend -> Platform Backend.

Launches the real Platform HTTP server as a separate process (uvicorn),
then drives it through ``PlatformClient`` over a real socket. The test
never imports Platform modules and never touches the Platform filesystem.
"""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from agent_backend.platform import PlatformApiError, PlatformClient

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def platform_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = _free_port()
    log_path = tmp_path_factory.mktemp("platform-server") / "uvicorn.log"
    log = log_path.open("w", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key != "AEMP_PROJECT_ROOT"}
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "aemp.api.http:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while True:
            if process.poll() is not None:
                log.close()
                raise RuntimeError(
                    "platform server exited early:\n"
                    + log_path.read_text(encoding="utf-8")
                )
            try:
                if httpx.get(f"{url}/healthz", timeout=1.0).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("platform server did not become healthy in 30s")
            time.sleep(0.2)
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()


def test_list_runs_over_real_http(platform_url: str) -> None:
    """Agent -> PlatformClient -> HTTP -> Platform Server -> RunIndex."""
    with PlatformClient(platform_url, contracts_dir=CONTRACTS) as client:
        document = client.list_runs(limit=5)
    assert set(document) == {"runs", "limit", "offset"}
    assert document["limit"] == 5
    assert isinstance(document["runs"], list)


def test_run_not_found_is_a_platform_business_error(platform_url: str) -> None:
    with PlatformClient(platform_url, contracts_dir=CONTRACTS) as client:
        with pytest.raises(PlatformApiError) as excinfo:
            client.get_run("20260101T000000Z-abcdef")
    assert excinfo.value.code == "RUN_NOT_FOUND"
    assert excinfo.value.http_status == 404


def test_invalid_history_filter_is_a_platform_business_error(platform_url: str) -> None:
    with PlatformClient(platform_url, contracts_dir=CONTRACTS) as client:
        with pytest.raises(PlatformApiError) as excinfo:
            client.list_runs(status="bogus")
    assert excinfo.value.code == "INVALID_REQUEST"
    assert excinfo.value.http_status == 400
