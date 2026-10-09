"""Standalone EDA environment check: Docker -> EDA image -> Yosys.

Usage (from the repository root)::

    python scripts/check_eda_env.py      # exit 0 = READY

M2.2 keeps this as a script on purpose: the ``aemp doctor`` CLI stays
M0-stable (AGENTS.md §12); folding environment checks into the CLI can
happen later without changing what is verified here.
"""

from __future__ import annotations

import json
import subprocess
import sys

IMAGE = "aemp-eda-base:0.1.0"


def _run(args: list[str], timeout: float = 120.0) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    version = _run(["docker", "--version"])
    checks.append(("Docker CLI", version.returncode == 0, version.stdout.strip()))

    server = _run(["docker", "version", "--format", "{{.Server.Version}}"])
    detail = server.stdout.strip() or server.stderr.strip()[:120]
    checks.append(("Docker daemon", server.returncode == 0, detail))

    inspect = _run(["docker", "image", "inspect", IMAGE])
    if inspect.returncode == 0:
        created = json.loads(inspect.stdout)[0].get("Created", "?")
        detail = f"created {created}"
    else:
        detail = "missing - run: docker build -t aemp-eda-base:0.1.0 docker/"
    checks.append((f"EDA image {IMAGE}", inspect.returncode == 0, detail))

    yosys = _run(["docker", "run", "--rm", IMAGE, "yosys", "-V"])
    checks.append(("Yosys in container", yosys.returncode == 0, yosys.stdout.strip()))

    all_ok = True
    for name, passed, detail in checks:
        print(f"  [{'OK' if passed else 'FAIL'}] {name}: {detail}")
        all_ok = all_ok and passed
    print("EDA environment:", "READY" if all_ok else "NOT READY")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
