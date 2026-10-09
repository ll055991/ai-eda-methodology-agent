"""Flow Engine (M3): declarative flow specs -> validated, tracked,
multi-tool runs. The engine orchestrates; RunManager records; drivers
execute. See AGENTS.md §16 for the M3 discipline."""

from aemp.flow.engine import FlowEngine, ToolEntry, ToolRegistry, default_registry
from aemp.flow.models import (
    FLOW_SCHEMA_VERSION,
    FlowInput,
    FlowSpec,
    FlowStep,
    InputRef,
    OutputRef,
    parse_ref,
)
from aemp.flow.spec import load_flow_spec

__all__ = [
    "FLOW_SCHEMA_VERSION",
    "FlowInput",
    "FlowSpec",
    "FlowStep",
    "FlowEngine",
    "InputRef",
    "OutputRef",
    "ToolEntry",
    "ToolRegistry",
    "default_registry",
    "load_flow_spec",
    "parse_ref",
]
