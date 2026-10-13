"""Credential-free identity established by the API authentication guard.

This identity records how a request was admitted. It does not grant a role or
profile access: authorization rules can consume it in later increments.
"""

from dataclasses import dataclass
from typing import Literal

PrincipalKind = Literal["session", "legacy_api_key", "anonymous_local", "system"]


@dataclass(frozen=True, slots=True)
class Principal:
    """An immutable request identity; credentials never enter this object."""

    kind: PrincipalKind
    username: str | None = None
    role: Literal["administrator", "operator", "viewer"] = "administrator"
