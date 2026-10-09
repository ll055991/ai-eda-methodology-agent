"""aemp command line interface.

M0 commands: ``doctor`` (implemented) plus reserved stubs for later
milestones.

Exit codes:
    0 - success (and doctor READY)
    1 - handled error, or doctor NOT READY
    2 - command is reserved for a later milestone
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from aemp import __version__
from aemp import doctor as doctor_module
from aemp.core.config import discover_settings
from aemp.core.errors import AEMPError, InvalidRequestError
from aemp.core.logging import get_logger, setup_logging
from aemp.drivers.opensta import OpenSTADriver
from aemp.drivers.yosys import YosysDriver
from aemp.flow.engine import FlowEngine, default_registry
from aemp.parsers import OpenSTAParser, ReportParserRegistry
from aemp.qa.engine import QAEngine
from aemp.qa.rules import RulePackLoader
from aemp.qa.service import QAService
from aemp.runs.index import INDEX_FILENAME, RunIndex
from aemp.runs.manager import RunManager
from aemp.service import RunService

logger = get_logger(__name__)

# Reserved commands are registered but not implemented; the value is the
# milestone that will implement them (see docs/01 §6).
_RESERVED_COMMANDS = {
    "ask": "M6 (minimal agent)",
}

_HISTORY_COLUMNS = (("RUN ID", 30), ("STATUS", 11), ("OPERATION", 17))


def _cmd_doctor(_args: argparse.Namespace) -> int:
    """Implement ``aemp doctor``: environment diagnosis, no EDA workloads."""
    settings = discover_settings()
    report = doctor_module.run_doctor(settings)
    print(doctor_module.render_report(report))
    return 0 if report.overall_status == doctor_module.OVERALL_READY else 1


def _cmd_run(args: argparse.Namespace) -> int:
    """Implement ``aemp run``: one yosys synthesis step as a formal run (M2.4)."""
    settings = discover_settings()
    service = RunService(
        manager=RunManager(settings),
        yosys=YosysDriver.from_config(settings.config_dir / "tools" / "yosys.yaml"),
    )
    run = service.run_synth(
        rtl=args.rtl, top=args.top, timeout_s=args.timeout, run_id=args.run_id
    )
    return _print_run_summary(run)


def _cmd_sta(args: argparse.Namespace) -> int:
    """Implement ``aemp sta``: one opensta timing step as a formal run (M2.5)."""
    settings = discover_settings()
    service = RunService(
        manager=RunManager(settings),
        opensta=OpenSTADriver.from_config(
            settings.config_dir / "tools" / "opensta.yaml"
        ),
    )
    run = service.run_sta(
        netlist=args.netlist,
        liberty=args.liberty,
        sdc=args.sdc,
        top=args.top,
        timeout_s=args.timeout,
        run_id=args.run_id,
    )
    return _print_run_summary(run)


def _print_run_summary(run) -> int:
    """Print the run summary read from run.json (filesystem = truth)."""
    envelope = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
    print(f"Run ID  : {envelope['run_id']}")
    print(f"Status  : {envelope['provenance']['run_status'].upper()}")
    print(f"Step    : {envelope['step_id']}")
    print(f"Tool    : {envelope['tool']['name']} ({envelope['tool']['version']})")
    print(f"Duration: {envelope['execution']['duration_ms']} ms")
    print("Artifacts:")
    for artifact in envelope["artifacts"]:
        print(f"  - {artifact['path']} ({artifact['role']})")
    return 0 if run.status == "success" else 1


def _cmd_parse(args: argparse.Namespace) -> int:
    """Implement ``aemp parse``: report -> structured metrics (M4)."""
    registry = ReportParserRegistry()
    registry.register("opensta", OpenSTAParser())
    parser = registry.resolve(args.tool)
    report = parser.parse(args.report)

    print(f"Tool    : {report.tool}")
    version = report.tool_version or "UNKNOWN (not stated in report)"
    print(f"Version : {version}")
    print(f"Source  : {report.source}")
    print("Metrics:")
    if not report.metrics:
        print("  (none)")
    for metric in report.metrics:
        print(f"  {metric.name} = {metric.value} {metric.unit}")
    if report.paths:
        print(f"Paths ({len(report.paths)}):")
        for path in report.paths:
            slack = "UNKNOWN" if path.slack_ns is None else f"{path.slack_ns} ns"
            print(f"  {path.startpoint} -> {path.endpoint} [{path.status}] slack={slack}")
    for diagnostic in report.diagnostics:
        print(f"{diagnostic.severity}: {diagnostic.code}: {diagnostic.message}")
    return 0


def _print_qa_summary(summary) -> None:
    print(f"QA Status: {summary.status.upper()}")
    print(f"Rule pack: {summary.rule_pack} v{summary.rule_pack_version}")
    print(
        f"Rules     : {summary.total_rules} total | {summary.passed} passed | "
        f"{summary.failed} failed | {summary.unknown} unknown | "
        f"{summary.warnings} warnings"
    )
    print("Findings:")
    for finding in summary.findings:
        actual = "UNKNOWN" if finding.actual is None else finding.actual
        print(
            f"  [{finding.status.upper():<7}] {finding.rule_id} "
            f"({finding.severity}): {finding.metric} = {actual}, "
            f"expected {finding.expected}"
        )


def _cmd_qa(args: argparse.Namespace) -> int:
    """Implement ``aemp qa``: metrics + rules -> findings (M5)."""
    pack = RulePackLoader.load(Path(args.rules))
    if args.run is not None:
        settings = discover_settings()
        service = QAService(manager=RunManager(settings))
        summary = service.evaluate_run(args.run, Path(args.rules))
    else:
        metrics = json.loads(Path(args.metrics).read_text(encoding="utf-8"))
        summary = QAEngine().evaluate(metrics, pack)
    _print_qa_summary(summary)
    # 0 = pass; non-zero gates CI: fail AND unknown both block (unknown
    # means "no evidence", which must never be treated as success)
    return 0 if summary.status == "pass" else 1


def _cmd_flow_run(args: argparse.Namespace) -> int:
    """Implement ``aemp flow run <spec>``: multi-step flow execution (M3)."""
    settings = discover_settings()
    engine = FlowEngine(registry=default_registry(settings), manager=RunManager(settings))
    run = engine.run(args.spec, timeout_s=args.timeout, run_id=args.run_id)
    envelope = json.loads((run.run_dir / "run.json").read_text(encoding="utf-8"))
    steps = envelope["provenance"].get("steps", [])
    print(f"Flow: {envelope['operation'].removeprefix('flow.')}")
    print(f"Run : {envelope['run_id']}")
    for index, step in enumerate(steps, start=1):
        status = str(step["status"]).upper()
        print(f"[{index}/{len(steps)}] {step['id']:<10} {step['tool']:<10} {status}")
        for artifact in envelope["artifacts"]:
            if artifact["role"].startswith(f"{step['id']}."):
                print(f"      artifact: {artifact['path']}")
    print(f"Flow {'SUCCESS' if run.status == 'success' else 'FAILED'}")
    return 0 if run.status == "success" else 1


def _cmd_history(args: argparse.Namespace) -> int:
    """Implement ``aemp history``: list recent runs from the SQLite index."""
    if args.limit < 1:
        msg = f"--limit must be >= 1, got {args.limit}"
        raise InvalidRequestError(msg)
    settings = discover_settings()
    index = RunIndex(settings.runs_dir / INDEX_FILENAME)
    rows = index.list_runs(limit=args.limit)
    if not rows:
        print("No runs found.")
        return 0
    header = "".join(label.ljust(width) for label, width in _HISTORY_COLUMNS) + "CREATED"
    print(header)
    for row in rows:
        line = "".join(
            str(row[key]).ljust(width)
            for (_, width), key in zip(
                _HISTORY_COLUMNS, ("run_id", "status", "operation"), strict=True
            )
        )
        print(line + str(row["created_at"]))
    return 0


def _make_stub(command: str, milestone: str):
    """Return a handler that reports a command as reserved for a later milestone."""

    def handler(_args: argparse.Namespace) -> int:
        print(
            f"aemp {command}: not implemented in M0 (planned for {milestone})",
            file=sys.stderr,
        )
        return 2

    return handler


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="aemp",
        description="AI EDA Methodology & QA Platform command line interface.",
        epilog=(
            "Exit codes: 0 = success, 1 = handled error or doctor NOT READY, "
            "2 = command reserved for a later milestone."
        ),
    )
    parser.add_argument("--version", action="version", version=f"aemp {__version__}")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable DEBUG logging (default: INFO)",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="check the local environment (Python, OS, EDA tools on PATH)",
        description="Locate EDA tools on PATH and probe their official version commands.",
    )
    doctor_parser.set_defaults(func=_cmd_doctor)

    history_parser = subparsers.add_parser(
        "history",
        help="list recent runs (SQLite index over the runs/ directories)",
        description="List recent runs. Filesystem run directories remain the source of truth.",
    )
    history_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        metavar="N",
        help="maximum number of runs to show (default: 20)",
    )
    history_parser.set_defaults(func=_cmd_history)

    parse_parser = subparsers.add_parser(
        "parse",
        help="parse an EDA report into structured metrics (M4)",
        description="Parse a tool report into structured metrics. Pure parsing - no verdicts.",
    )
    parse_parser.add_argument(
        "--tool", default="opensta", help="report format to parse (default: opensta)"
    )
    parse_parser.add_argument(
        "--report", type=Path, required=True, help="report file to parse"
    )
    parse_parser.set_defaults(func=_cmd_parse)

    qa_parser = subparsers.add_parser(
        "qa",
        help="evaluate a QA rule pack against run metrics (M5)",
        description=(
            "Deterministic QA: metrics + YAML rules -> findings. "
            "No LLM, fully reproducible."
        ),
    )
    qa_parser.add_argument(
        "--rules", type=Path, required=True, help="QA rule pack YAML file"
    )
    qa_source = qa_parser.add_mutually_exclusive_group(required=True)
    qa_source.add_argument(
        "--run", help="run id whose recorded metrics are evaluated and archived"
    )
    qa_source.add_argument(
        "--metrics", type=Path, help="metrics JSON file (evaluate only, no archive)"
    )
    qa_parser.set_defaults(func=_cmd_qa)

    run_parser = subparsers.add_parser(
        "run",
        help="run one yosys synthesis step as a formal, traceable run (M2.4)",
        description=(
            "Create a run, freeze inputs into its workspace, synthesize with "
            "Yosys in the EDA container, record execution facts and artifacts."
        ),
    )
    run_parser.add_argument(
        "--rtl", type=Path, required=True, help="RTL file to synthesize"
    )
    run_parser.add_argument("--top", required=True, help="top module name")
    run_parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        metavar="S",
        help="wall-clock bound for the container execution in seconds (default: 600)",
    )
    run_parser.add_argument("--run-id", default=None, help="optional explicit run id")
    run_parser.set_defaults(func=_cmd_run)

    sta_parser = subparsers.add_parser(
        "sta",
        help="run one opensta timing analysis step as a formal run (M2.5)",
        description=(
            "Create a run, freeze netlist/liberty/sdc into its workspace, "
            "execute OpenSTA in the pinned container, record everything."
        ),
    )
    sta_parser.add_argument(
        "--netlist", type=Path, required=True,
        help="Liberty-MAPPED netlist (generic synthesis output cannot be linked)",
    )
    sta_parser.add_argument(
        "--liberty", type=Path, required=True,
        help="standard-cell Liberty library with timing models",
    )
    sta_parser.add_argument("--sdc", type=Path, required=True, help="timing constraints (SDC)")
    sta_parser.add_argument("--top", required=True, help="top module name")
    sta_parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        metavar="S",
        help="wall-clock bound for the container execution in seconds (default: 600)",
    )
    sta_parser.add_argument("--run-id", default=None, help="optional explicit run id")
    sta_parser.set_defaults(func=_cmd_sta)

    flow_parser = subparsers.add_parser(
        "flow",
        help="flow orchestration (M3)",
    )
    flow_subparsers = flow_parser.add_subparsers(
        dest="flow_command", metavar="<command>", required=True
    )
    flow_run_parser = flow_subparsers.add_parser(
        "run",
        help="execute a flow spec as one tracked run (fail-fast)",
        description=(
            "Validate and execute a flow spec: steps run in artifact-"
            "dependency order inside one run workspace."
        ),
    )
    flow_run_parser.add_argument("spec", type=Path, help="flow spec YAML file")
    flow_run_parser.add_argument(
        "--timeout",
        type=float,
        default=900.0,
        metavar="S",
        help="per-step wall-clock bound in seconds (default: 900)",
    )
    flow_run_parser.add_argument("--run-id", default=None, help="optional explicit run id")
    flow_run_parser.set_defaults(func=_cmd_flow_run)

    for command, milestone in sorted(_RESERVED_COMMANDS.items()):
        stub_parser = subparsers.add_parser(command, help=f"reserved for {milestone}")
        stub_parser.set_defaults(func=_make_stub(command, milestone))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    Expected (``aemp.core.errors.AEMPError``) failures are reported as one
    clean error line; unexpected bugs crash with a traceback on purpose.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    try:
        return int(args.func(args))
    except AEMPError as exc:
        logger.error("error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
