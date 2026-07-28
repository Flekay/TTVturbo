"""System-level infrastructure: executable resolution and tool paths.

This package is the neutral home for cross-cutting concerns that sit
between the Settings layer and the service layer.  Domain modules import
from here instead of from the app entrypoint.
"""

from __future__ import annotations

from .executables import ExecutableResolver, find_executable, resolve_tools

__all__ = ["ExecutableResolver", "find_executable", "resolve_tools"]
