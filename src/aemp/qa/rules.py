"""Rule pack loading and validation: YAML -> QARule[] (rules are data).

Rules live in YAML (``configs/rules/*.yaml``), NOT in Python if/else:
QA standards must be maintainable as data, versioned, and freezable into
run archives. Every structural problem is rejected at LOADING time - a
malformed rule can never reach the engine.
"""

from __future__ import annotations

from pathlib import Path

from aemp.core.config import load_yaml
from aemp.core.errors import InvalidRequestError
from aemp.qa.models import QARule, RulePack

__all__ = ["RulePackLoader"]


class RulePackLoader:
    """Loads and validates a rule pack YAML file."""

    @staticmethod
    def load(path: Path) -> RulePack:
        """Load *path* into a validated :class:`RulePack`.

        Expected shape::

            pack: base_timing
            version: 1
            rules:
              - id: timing.wns.non_negative
                metric: sta.wns_ns
                check: ge
                threshold: 0.0
                severity: error
                description: optional

        Raises:
            InvalidRequestError: On missing keys, malformed rules,
                duplicate rule ids, or any rule-level validation error.
        """
        data = load_yaml(Path(path))
        pack_name = data.get("pack")
        if not isinstance(pack_name, str) or not pack_name.strip():
            msg = f"{path}: 'pack' must be a non-empty string"
            raise InvalidRequestError(msg)
        version = data.get("version")
        if not isinstance(version, int) or isinstance(version, bool):
            msg = f"{path}: 'version' must be an integer"
            raise InvalidRequestError(msg)
        rules_data = data.get("rules")
        if not isinstance(rules_data, list) or not rules_data:
            msg = f"{path}: 'rules' must be a non-empty list"
            raise InvalidRequestError(msg)

        rules: list[QARule] = []
        seen: set[str] = set()
        for raw in rules_data:
            if not isinstance(raw, dict):
                msg = f"{path}: each rule must be a mapping"
                raise InvalidRequestError(msg)
            for field in ("id", "metric", "check", "threshold"):
                if field not in raw:
                    msg = f"{path}: rule is missing required field {field!r}: {raw}"
                    raise InvalidRequestError(msg)
            rule = QARule(
                id=str(raw["id"]),
                metric=str(raw["metric"]),
                check=str(raw["check"]),
                threshold=raw["threshold"],
                severity=str(raw.get("severity", "error")),
                description=str(raw.get("description", "")),
            )
            if rule.id in seen:
                msg = f"{path}: duplicate rule id {rule.id!r}"
                raise InvalidRequestError(msg)
            seen.add(rule.id)
            rules.append(rule)

        return RulePack(name=pack_name, version=version, rules=tuple(rules))
