"""Skill security tests (M7.1 Tests 18-21): skills cannot execute or bypass.

Skills are untrusted configuration: they may inform the model, but must
never gain execution ability, registry access, policy bypass or HITL
bypass.
"""

from pathlib import Path

from agent_backend.application.approval import ApprovalManager
from agent_backend.application.langgraph_runtime import LangGraphAgentRuntime
from agent_backend.llm.models import ModelRequest, ModelResponse
from agent_backend.models.chat import CHAT_STATUS_COMPLETED, CHAT_STATUS_INTERRUPTED, ChatRequest
from agent_backend.policy import PolicyEngine
from agent_backend.skills import SkillLoader, SkillRegistry, SkillService
from agent_backend.tools.gateway import RegistryToolGateway
from agent_backend.tools.models import ToolCall, ToolDefinition, ToolResult
from agent_backend.tools.registry import ToolRegistry

AGENT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = AGENT_ROOT / "skills"
PACKAGE = AGENT_ROOT / "src" / "agent_backend" / "skills"


class ScriptedProvider:
    name = "scripted"

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    def chat(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        return self._responses[index]


class FakeRunFlow:
    definition = ToolDefinition(
        name="run_flow",
        description="Run a flow.",
        input_schema={"type": "object", "additionalProperties": False, "properties": {}},
    )

    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    def execute(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        return ToolResult.success(call, {"run_id": "r1"})


def tool_response() -> ModelResponse:
    return ModelResponse(
        content="",
        provider="scripted",
        tool_calls=(ToolCall(id="c1", name="run_flow", arguments={}),),
    )


def final() -> ModelResponse:
    return ModelResponse(content="done", provider="scripted")


class TestNoExecutionCapability:
    def test_skill_runtime_code_has_no_exec_or_tool_access(self) -> None:
        execution_tokens = ("subprocess", "os.system", "exec(", "eval(", "importlib")
        architecture_tokens = ("ToolRegistry", "ToolGateway", "PlatformClient", "aemp")
        for path in sorted(PACKAGE.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for token in execution_tokens:
                assert token not in text, f"{path.name}: {token!r}"
            if path.name == "__init__.py":
                continue  # docstring explains the boundary; no code imports
            for token in architecture_tokens:
                assert token not in text, f"{path.name}: {token!r}"

    def test_skill_files_contain_no_executable_payloads(self) -> None:
        forbidden = ("subprocess", "os.system", "exec(", "eval(", "docker run")
        for path in sorted(SKILLS_ROOT.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                assert token not in text, f"{path.name}: {token!r}"

    def test_skill_service_exposes_no_tool_methods(self) -> None:
        service = SkillService(SkillRegistry(SKILLS_ROOT), SkillLoader(SKILLS_ROOT))
        assert not hasattr(service, "execute")
        assert not hasattr(service, "call_tool")


class TestNoPolicyOrHITLBypass:
    def _runtime(self, provider: ScriptedProvider) -> tuple[LangGraphAgentRuntime, FakeRunFlow]:
        tool = FakeRunFlow()
        registry = ToolRegistry()
        registry.register(tool)
        runtime = LangGraphAgentRuntime(
            provider=provider,
            model="fake-model",
            gateway=RegistryToolGateway(registry),
            policy=PolicyEngine.default(),
            approvals=ApprovalManager(),
            skills=SkillService(SkillRegistry(SKILLS_ROOT), SkillLoader(SKILLS_ROOT)),
        )
        return runtime, tool

    def test_high_risk_tool_still_interrupts_with_skills_loaded(self) -> None:
        provider = ScriptedProvider([tool_response(), final()])
        runtime, tool = self._runtime(provider)

        response = runtime.handle(
            ChatRequest(message="Run the RTL flow to STA", session_id="skill-hitl")
        )

        assert response.status == CHAT_STATUS_INTERRUPTED
        assert response.approval_required is True
        assert tool.calls == []  # skills cannot auto-approve run_flow

        resumed = runtime.resume(
            session_id="skill-hitl",
            approval_id=response.approval_id or "",
            decision="approved",
        )
        assert resumed.status == CHAT_STATUS_COMPLETED
        assert len(tool.calls) == 1

    def test_skill_context_is_in_the_system_message(self) -> None:
        provider = ScriptedProvider([final()])
        runtime, _tool = self._runtime(provider)
        runtime.handle(ChatRequest(message="Analyze the STA timing results"))

        system_message = provider.requests[0].messages[0]
        assert system_message.role == "system"
        assert '<skill name="timing-analysis"' in system_message.content
        # the base rules must survive the skill injection
        assert "Do not invent tools" in system_message.content
