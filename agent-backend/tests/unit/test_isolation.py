"""Isolation guards: the Agent Backend must stay unable to touch the Platform.

Scans the agent_backend package source (not tests) for forbidden imports
and capabilities, and checks the package metadata for forbidden deps.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = AGENT_ROOT / "src" / "agent_backend"

FORBIDDEN_SOURCE_TOKENS = (
    "import aemp",
    "from aemp",
    "subprocess",
    "os.system",
    "sqlite3",
    "docker",
)

FORBIDDEN_DEPENDENCIES = (
    # langchain-core arrives ONLY transitively via langgraph (M6.5); no
    # langchain agent APIs may be declared or imported directly.
    "langchain",
    "deepagents",
    "openai",
    "anthropic",
)


def test_package_has_no_platform_or_execution_capability() -> None:
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_SOURCE_TOKENS:
            if token in text:
                offenders.append(f"{path.relative_to(AGENT_ROOT)}: {token!r}")
    assert not offenders, f"forbidden tokens in agent_backend: {offenders}"


def test_pyproject_declares_no_agent_framework_dependencies() -> None:
    text = (AGENT_ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    for dependency in FORBIDDEN_DEPENDENCIES:
        # quoted match: comments may mention a name without depending on it
        assert f'"{dependency}' not in text, f"forbidden dependency: {dependency}"
    assert '"langgraph' in text, "langgraph is the sanctioned M6.5 dependency"


def test_langgraph_runtime_does_not_touch_platform_client() -> None:
    text = (PACKAGE / "application" / "langgraph_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "PlatformClient" not in text
    assert "platform.client" not in text


def test_llm_layer_has_no_platform_or_execution_capability() -> None:
    llm_package = PACKAGE / "llm"
    offenders: list[str] = []
    for path in sorted(llm_package.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in (
            "PlatformClient",
            "platform.client",
            "import aemp",
            "subprocess",
            "docker",
            "sqlite3",
        ):
            if token in text:
                offenders.append(f"{path.relative_to(AGENT_ROOT)}: {token!r}")
    assert not offenders, f"forbidden tokens in llm layer: {offenders}"


def test_llm_runtime_does_not_touch_platform_client() -> None:
    text = (PACKAGE / "application" / "chat.py").read_text(encoding="utf-8")
    assert "PlatformClient" not in text
    assert "platform.client" not in text


def test_contracts_directory_holds_no_schema_copies() -> None:
    contracts_dir = AGENT_ROOT / "contracts"
    files = [path.name for path in contracts_dir.rglob("*") if path.is_file()]
    assert files == ["README.md"], f"contracts must not vendor schemas: {files}"


def test_agent_backend_is_not_inside_the_platform_package() -> None:
    assert PACKAGE.is_dir()
    assert "src/aemp" not in PACKAGE.as_posix()
    assert (AGENT_ROOT / "src" / "agent_backend").exists()
    assert REPO_ROOT.joinpath("src", "aemp").exists()  # platform still separate


def test_mcp_is_a_client_side_transport_only() -> None:
    """M7.2 sanctions the MCP SDK - but only its client half.

    The agent consumes MCP capabilities; it never exposes itself as an MCP
    server, so no ``mcp.server`` import may appear anywhere in the package.
    """
    text = (AGENT_ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    assert '"mcp' in text, "M7.2 declares the MCP SDK dependency"

    offenders: list[str] = []
    for path in sorted((PACKAGE / "mcp").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for token in ("mcp.server", "from mcp import", "MCPServer"):
            if token in source:
                offenders.append(f"{path.relative_to(AGENT_ROOT)}: {token!r}")
    assert not offenders, f"the agent must not run an MCP server: {offenders}"
