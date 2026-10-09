"""Unit tests for flow spec loading/validation (no Docker, no runs)."""

from pathlib import Path

import pytest

from aemp.core.errors import InvalidRequestError
from aemp.flow.spec import load_flow_spec

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_V = REPO_ROOT / "examples" / "sta" / "design.v"


def write_spec(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "flow.yaml"
    path.write_text(text, encoding="utf-8")
    return path


VALID = """\
version: "aemp.flow/v1"
flow:
  id: demo
  name: Demo
inputs:
  files:
    rtl: {rtl}
  params:
    top: simple_counter
steps:
  - id: synth
    tool: yosys
    operation: yosys.synthesize
    inputs:
      rtl: ${{inputs.rtl}}
      top: ${{inputs.top}}
    outputs:
      netlist: mapped.v
"""


class TestHappyPath:
    def test_valid_spec_loads(self, tmp_path: Path) -> None:
        spec = load_flow_spec(write_spec(tmp_path, VALID.format(rtl=DESIGN_V.as_posix())))
        assert spec.id == "demo"
        assert spec.inputs["top"].kind == "param"
        assert spec.steps[0].id == "synth"

    def test_steps_are_topologically_reordered(self, tmp_path: Path) -> None:
        # sta declared FIRST but consuming synth's output: must execute second.
        rtl = DESIGN_V.as_posix()
        text = f"""\
version: "aemp.flow/v1"
flow:
  id: demo
inputs:
  files:
    netlist: {rtl}
steps:
  - id: sta
    tool: opensta
    operation: opensta.timing_analysis
    inputs:
      netlist: ${{steps.synth.outputs.netlist}}
    outputs:
      report: timing.rpt
  - id: synth
    tool: yosys
    operation: yosys.synthesize
    inputs:
      netlist: ${{inputs.netlist}}
    outputs:
      netlist: mapped.v
"""
        spec = load_flow_spec(write_spec(tmp_path, text))
        assert [step.id for step in spec.steps] == ["synth", "sta"]


class TestValidation:
    def test_version_mismatch_rejected(self, tmp_path: Path) -> None:
        spec = write_spec(tmp_path, VALID.format(rtl=DESIGN_V.as_posix()).replace(
            "aemp.flow/v1", "aemp.flow/v9"
        ))
        with pytest.raises(InvalidRequestError, match="unsupported flow version"):
            load_flow_spec(spec)

    def test_duplicate_step_id_rejected(self, tmp_path: Path) -> None:
        rtl = DESIGN_V.as_posix()
        text = f"""\
version: "aemp.flow/v1"
flow:
  id: demo
inputs:
  files:
    rtl: {rtl}
steps:
  - id: synth
    tool: yosys
    operation: yosys.synthesize
    inputs: {{}}
    outputs: {{}}
  - id: synth
    tool: yosys
    operation: yosys.synthesize
    inputs: {{}}
    outputs: {{}}
"""
        with pytest.raises(InvalidRequestError, match="duplicate step id"):
            load_flow_spec(write_spec(tmp_path, text))

    def test_unknown_input_reference_rejected(self, tmp_path: Path) -> None:
        text = VALID.format(rtl=DESIGN_V.as_posix()).replace(
            "rtl: ${inputs.rtl}", "rtl: ${inputs.no_such_file}"
        )
        with pytest.raises(InvalidRequestError, match="unknown input"):
            load_flow_spec(write_spec(tmp_path, text))

    def test_unknown_step_reference_rejected(self, tmp_path: Path) -> None:
        text = VALID.format(rtl=DESIGN_V.as_posix()).replace(
            "top: ${inputs.top}", "top: ${steps.ghost.outputs.x}"
        )
        with pytest.raises(InvalidRequestError, match="unknown step"):
            load_flow_spec(write_spec(tmp_path, text))

    def test_unknown_output_reference_rejected(self, tmp_path: Path) -> None:
        text = VALID.format(rtl=DESIGN_V.as_posix()).replace(
            "top: ${inputs.top}", "top: ${steps.synth.outputs.timing}"
        )
        with pytest.raises(InvalidRequestError, match="does not declare"):
            load_flow_spec(write_spec(tmp_path, text))

    def test_malformed_reference_rejected(self, tmp_path: Path) -> None:
        text = VALID.format(rtl=DESIGN_V.as_posix()).replace(
            "top: ${inputs.top}", "top: ${inputs.top} extra"
        )
        with pytest.raises(InvalidRequestError, match="Malformed reference"):
            load_flow_spec(write_spec(tmp_path, text))

    def test_circular_dependency_rejected(self, tmp_path: Path) -> None:
        rtl = DESIGN_V.as_posix()
        text = f"""\
version: "aemp.flow/v1"
flow:
  id: cycle
inputs:
  files:
    a: {rtl}
steps:
  - id: step_a
    tool: yosys
    operation: yosys.synthesize
    inputs:
      a: ${{steps.step_b.outputs.b}}
    outputs:
      x: a.v
  - id: step_b
    tool: yosys
    operation: yosys.synthesize
    inputs:
      b: ${{steps.step_a.outputs.x}}
    outputs:
      b: b.v
"""
        with pytest.raises(InvalidRequestError, match="circular dependency"):
            load_flow_spec(write_spec(tmp_path, text))

    def test_missing_input_file_rejected(self, tmp_path: Path) -> None:
        text = VALID.format(rtl=(tmp_path / "missing.v").as_posix())
        with pytest.raises(InvalidRequestError, match="does not exist"):
            load_flow_spec(write_spec(tmp_path, text))
