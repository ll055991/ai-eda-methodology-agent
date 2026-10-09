"""Platform API application layer (M6.0 boundary).

This package is the ONLY sanctioned entry point for programmatic
consumers of the platform: it validates requests and responses against
``contracts/`` (language-neutral JSON Schema) and delegates to the
existing deterministic core. It adds no Flow / Run / QA logic.

Dependency direction: ``api`` -> ``flow``/``qa``/``runs``/``core`` (outermost
layer, like ``cli``). The future Agent Backend must NOT import this
package; it calls the HTTP contract (M6.1) instead.
"""

from aemp.api.errors import (
    API_ERROR_CODES,
    API_ERROR_HTTP_STATUS,
    ApiError,
    error_document,
)
from aemp.api.schemas import (
    CONTRACT_SCHEMA_FILES,
    default_contracts_dir,
    load_contract_schema,
    validate_contract,
)
from aemp.api.services import PlatformApi

__all__ = [
    "API_ERROR_CODES",
    "API_ERROR_HTTP_STATUS",
    "CONTRACT_SCHEMA_FILES",
    "ApiError",
    "PlatformApi",
    "default_contracts_dir",
    "error_document",
    "load_contract_schema",
    "validate_contract",
]
