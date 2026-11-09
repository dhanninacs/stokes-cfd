"""Typed errors shared by every client (UI, CLI, HTTP, MCP).

Every error carries a stable ``code`` so callers can branch on it, and
``to_dict`` so the same object is serialised identically everywhere.
"""

from __future__ import annotations

from typing import Any


class StokesError(Exception):
    """Base class. Subclasses set ``code``; messages are written for engineers."""

    code: str = "stokes.error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """``code`` overrides the class's own, for the one case that needs it: **an error
        reconstructed from a remote.** The agent protocol sends a code with every failure, and
        the client must raise something whose ``code`` is what the server said — otherwise
        `agent.busy` arrives as `agent.remote` and no caller can branch on it.
        `agent.remote` and no caller can branch on it.
        """
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}

    def __str__(self) -> str:
        return self.message


class ValidationError(StokesError):
    code = "validation"


class UnitMismatch(ValidationError):
    code = "units.mismatch"


class UnitConversionError(ValidationError):
    """A bare unit conversion that cannot be made: unknown unit, other dimension, bad text."""

    code = "units.convert"


class ExpressionError(ValidationError):
    code = "units.expression"


class CycleError(ExpressionError):
    code = "units.cycle"


class UnknownNode(StokesError):
    code = "model.unknown_node"


class UnknownNodeType(StokesError):
    code = "model.unknown_type"


class UnknownParameter(StokesError):
    code = "model.unknown_parameter"


class ModelError(StokesError):
    code = "model.error"


class ReferencedNode(ModelError):
    """A node other nodes refer to through a ``ref`` parameter cannot be removed (ADR-0022
    decision 2.8); ``details`` lists the referrers."""

    code = "model.referenced"


class PersistenceError(StokesError):
    code = "model.persistence"


class UnknownCommand(StokesError):
    code = "api.unknown_command"


class NoProject(StokesError):
    code = "api.no_project"


class LicenceError(StokesError):
    code = "licence.invalid"


class LicenceFeatureMissing(StokesError):
    code = "licence.feature_missing"


class ProtocolError(StokesError):
    code = "agent.protocol"
