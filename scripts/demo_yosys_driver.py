"""M2.3 demo: Python -> YosysDriver -> Docker -> Yosys -> ToolExecutionResult.

Usage (from the repository root)::

    python scripts/demo_yosys_driver.py

Demonstrates the M2.3 workspace boundary: ONE demo workspace
(runs/demo-yosys-driver/) is mounted at /workspace - NOT the whole repo.
The script and RTL live inside that boundary; artifacts come back through
the mount. This script calls the real driver API - no demo-script chaining.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from aemp.drivers.yosys import YosysDriver

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = REPO_ROOT / "runs" / "demo-yosys-driver"

SYNTH_SCRIPT = (
    "read_verilog inputs/simple_counter.v\n"
    "hierarchy -check -top simple_counter\n"
    "synth -top simple_counter\n"
    "stat\n"
    "write_verilog -noattr artifacts/synthesized.v\n"
)


def main() -> int:
    (WORKSPACE / "inputs").mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "artifacts").mkdir(exist_ok=True)
    shutil.copy(
        REPO_ROOT / "examples" / "rtl" / "simple_counter.v",
        WORKSPACE / "inputs" / "simple_counter.v",
    )
    (WORKSPACE / "synth.ys").write_text(SYNTH_SCRIPT, encoding="utf-8")

    driver = YosysDriver.from_config(REPO_ROOT / "configs" / "tools" / "yosys.yaml")

    version = driver.get_version()
    print("yosys version        :", version)

    result = driver.run_script(workspace=WORKSPACE, script=WORKSPACE / "synth.ys")
    print("status               :", result.status)
    print("exit_code            :", result.exit_code)
    print("duration_ms          :", result.duration_ms)
    print("timed_out            :", result.timed_out)
    print("host launcher        :", result.executable_path)  # docker CLI (see models.py)
    print("command              :", result.command)
    print("stdout has stat      :", "Number of cells" in result.stdout)
    for line in result.stdout.splitlines():
        if "Number of cells" in line:
            print("stat >", line)

    netlist = WORKSPACE / "artifacts" / "synthesized.v"
    print("netlist              :", netlist.relative_to(REPO_ROOT).as_posix(),
          "-", "exists" if netlist.is_file() else "MISSING")

    if result.succeeded and netlist.is_file() and "Yosys" in version:
        print("DEMO OK: Python -> YosysDriver -> Docker -> Yosys -> ToolExecutionResult")
        return 0
    print("DEMO FAILED", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
