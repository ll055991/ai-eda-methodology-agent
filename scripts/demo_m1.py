"""Standalone M1 teaching demo (no EDA tools, safe to run repeatedly).

Usage (from the repository root)::

    python scripts/demo_m1.py

Walks the full M1 chain::

    Run -> Result Envelope -> runs/<run_id>/ -> Input/Artifact + SHA256
         -> SQLite index -> aemp history

This script only READS core aemp APIs and WRITES into runs/ (gitignored).
Deleting runs/index.sqlite3 below is safe by design: the filesystem is the
source of truth and the index is rebuilt from run directories.
"""

from __future__ import annotations

import json
import sqlite3

from aemp.core.config import discover_settings
from aemp.core.errors import InvalidRequestError
from aemp.runs.index import INDEX_FILENAME, RunIndex
from aemp.runs.manager import RunManager


def banner(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def main() -> None:
    settings = discover_settings()
    manager = RunManager(settings)

    banner("STEP 1  create a run  (RunManager.create_run)")
    run = manager.create_run("demo")
    print(f"  run_id : {run.run_id}")
    print(f"  run_dir: {run.run_dir.relative_to(settings.project_root).as_posix()}")

    banner("STEP 2  the run directory  (Filesystem = Source of Truth)")
    for path in sorted(run.run_dir.rglob("*")):
        rel = path.relative_to(run.run_dir).as_posix()
        kind = "dir " if path.is_dir() else "file"
        print(f"  [{kind}] {rel}")

    banner("STEP 3  register a real input  (size + SHA256 fingerprint)")
    record = run.add_input(settings.project_root / "data" / "demo_design.v", role="rtl")
    print(f"  role   : {record.role}")
    print(f"  path   : {record.path}")
    print(f"  exists : {record.exists}")
    print(f"  bytes  : {record.bytes}")
    print(f"  sha256 : {record.sha256}")

    banner("EXPERIMENT A  input file does not exist  (no fake hashes)")
    missing = run.add_input(settings.project_root / "data" / "does_not_exist.v")
    print(f"  path   : {missing.path}")
    print(f"  exists : {missing.exists}")
    print(f"  bytes  : {missing.bytes}   <- None: never fabricated")
    print(f"  sha256 : {missing.sha256}   <- None: never fabricated")
    print("  run.json records this as an evidence gap (exists=false), NOT a failure.")

    banner("STEP 4  register a real artifact  (output written into the run dir)")
    artifact_path = run.run_dir / "artifacts" / "demo_netlist.v"
    artifact_path.write_bytes(b"module demo_design; endmodule\n")
    artifact = run.add_artifact(artifact_path, role="netlist")
    print(f"  path   : {artifact.path}    <- stored run-relative")
    print(f"  sha256 : {artifact.sha256}")

    banner("EXPERIMENT B  claim an artifact that does not exist  -> rejected")
    try:
        run.add_artifact(run.run_dir / "artifacts" / "ghost.v", role="netlist")
    except InvalidRequestError as exc:
        print(f"  InvalidRequestError: {exc}")
        print("  A claimed output must exist - fail fast instead of faking evidence.")

    banner("STEP 5  finalize  (execution vs engineering)")
    run.finalize("success", engineering_summary="demo: M1 has no engineering judgment")
    envelope = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
    print(f"  execution.status   = {envelope['execution']['status']}")
    print(f"  engineering.status = {envelope['engineering']['status']}")
    print("  Two independent verdicts: the demo 'ran fine', but M1 cannot judge")
    print("  design quality yet - so engineering stays 'unknown' (unknown != fail).")

    banner("STEP 6  what the SQLite index holds  (index only!)")
    db_path = settings.runs_dir / INDEX_FILENAME
    conn = sqlite3.connect(db_path)
    try:
        for row in conn.execute("SELECT run_id, status, operation FROM runs"):
            print(f"  runs     : {tuple(row)}")
        for row in conn.execute('SELECT run_id, role, path, "exists" FROM artifacts'):
            print(f"  artifacts: {tuple(row)}")
    finally:
        conn.close()

    banner("STEP 7  delete the index, rebuild from the filesystem")
    print(f"  deleting {db_path.relative_to(settings.project_root).as_posix()} ...")
    db_path.unlink()
    before = RunIndex(db_path).list_runs()
    print(f"  history right now : {len(before)} run(s)  (index gone -> empty)")
    count = RunIndex(db_path).rebuild(settings.runs_dir)
    after = RunIndex(db_path).list_runs()
    print(f"  rebuild() re-indexed {count} run(s) from runs/*/run.json")
    print(f"  history again     : {len(after)} run(s)  -> nothing was ever lost")

    banner("DONE - try it yourself")
    print("  aemp history")
    print(f"  cat runs/{run.run_id}/run.json")
    print(
        "  python -c \"import hashlib;"
        "print(hashlib.sha256(open('data/demo_design.v','rb').read()).hexdigest())\""
    )
    print(f"  ...and compare with the sha256 inside runs/{run.run_id}/run.json")


if __name__ == "__main__":
    main()
