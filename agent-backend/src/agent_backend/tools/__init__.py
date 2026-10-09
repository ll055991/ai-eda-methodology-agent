"""Agent tool layer: definitions, registry, gateway and platform tools.

Kept import-light on purpose: ``tools.models`` is pure data and is also
consumed by the LLM layer (tool schemas / tool calls), so this package
must not pull in the platform client at import time.
"""
