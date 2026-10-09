"""Standalone M2.2 demo: run real Yosys synthesis inside the EDA container.

Usage (from the repository root)::

    python scripts/demo_yosys_docker.py

Prerequisites:
    - Docker Desktop running
    - the EDA image built:  docker build -t aemp-eda-base:0.1.0 docker/

This script is deliberately NOT coupled to RunManager / ToolDriver / the
result envelope (that wiring is M2.4). It only proves the environment:
Docker -> Yosys -> real RTL -> netlist + logs, with exit code, stdout and
stderr captured to files so nothing has to be trusted from memory.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

IMAGE = "aemp-eda-base:0.1.0"
REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "runs" / "demo-yosys"


def main() -> int:
    (OUTPUT_DIR / "logs").mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "artifacts").mkdir(parents=True, exist_ok=True)

    mount = f"{REPO_ROOT.as_posix()}:/workspace"
    cmd = [
        "docker", "run", "--rm",
        "-v", mount,
        "-w", "/workspace",
        IMAGE,
        "yosys", "-s", "scripts/synth.ys",
    ]
    print("command:", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)

    stdout_log = OUTPUT_DIR / "logs" / "yosys.stdout.log"
    stderr_log = OUTPUT_DIR / "logs" / "yosys.stderr.log"
    stdout_log.write_text(result.stdout, encoding="utf-8")
    stderr_log.write_text(result.stderr, encoding="utf-8")

    netlist = OUTPUT_DIR / "artifacts" / "synthesized.v"
    print(f"exit code          : {result.returncode}")
    print(f"stdout log         : {stdout_log.relative_to(REPO_ROOT).as_posix()}"
          f" ({len(result.stdout)} chars)")
    print(f"stderr log         : {stderr_log.relative_to(REPO_ROOT).as_posix()}"
          f" ({len(result.stderr)} chars)")
    state = "exists" if netlist.is_file() else "MISSING"
    print(f"netlist            : {netlist.relative_to(REPO_ROOT).as_posix()} ({state})")

    for line in result.stdout.splitlines():
        if "Number of cells" in line or "Yosys" in line and "git sha1" in line:
            print("stdout >", line)

    if result.returncode != 0 or not netlist.is_file():
        print("DEMO FAILED", file=sys.stderr)
        return 1
    print("DEMO OK: RTL -> Yosys (Docker) -> netlist + logs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
