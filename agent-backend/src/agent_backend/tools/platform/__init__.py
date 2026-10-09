"""Platform tools: semantic adapters over the Platform HTTP contract.

Exactly the tools declared in ``contracts/agent-api.yaml`` (x-agent-tools):
run_flow / get_run / run_qa / get_artifacts. Each one delegates to
:class:`~agent_backend.platform.client.PlatformClient` - never to a shell,
a database or the filesystem.
"""

from agent_backend.platform.client import PlatformClient
from agent_backend.tools.platform.get_artifacts import GetArtifactsTool
from agent_backend.tools.platform.get_run import GetRunTool
from agent_backend.tools.platform.run_flow import RunFlowTool
from agent_backend.tools.platform.run_qa import RunQaTool
from agent_backend.tools.registry import ToolRegistry

__all__ = [
    "GetArtifactsTool",
    "GetRunTool",
    "RunFlowTool",
    "RunQaTool",
    "build_platform_registry",
]


def build_platform_registry(client: PlatformClient) -> ToolRegistry:
    """Registry with exactly the contract-declared platform tools."""
    registry = ToolRegistry()
    registry.register(RunFlowTool(client))
    registry.register(GetRunTool(client))
    registry.register(RunQaTool(client))
    registry.register(GetArtifactsTool(client))
    return registry
