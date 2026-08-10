"""Pure Trigger contracts for integration-owned runtimes."""

from powercontext.triggers.models import PolicyTransition
from powercontext.triggers.protocols import Trigger

__all__ = [
    "PolicyTransition",
    "Trigger",
]
