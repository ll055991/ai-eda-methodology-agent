"""AEMP Agent Backend (M6.2): chat API stub + PlatformClient.

This package MUST NOT import Platform code (`aemp`); it reaches the
Platform exclusively through the HTTP Platform API contract.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agent-backend")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
