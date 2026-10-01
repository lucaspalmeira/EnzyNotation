"""Base contract for external evidence-provider tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from enzynotation.backends.base import CommandSpec
from enzynotation.exceptions import EnzyNotationError


class ToolConfigurationError(EnzyNotationError):
    """Raised when an external tool configuration is unsafe or incomplete."""


class ExternalTool(ABC):
    """An external executable with deterministic command construction."""

    tool_id: str

    @abstractmethod
    def build_command(self, *, query: Path, output: Path) -> CommandSpec:
        """Build the executable command without invoking a shell."""

    @abstractmethod
    def database_artifacts(self) -> dict[str, Path]:
        """Return database files that participate in cache invalidation."""
