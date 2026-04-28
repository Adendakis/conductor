"""Custom validator registry.

Projects register their own validators via the AgentRegistry:

    from conductor.validation.custom_validators import register_validator

    def register(registry):
        registry.register_validator("my_check", my_check_function)

Validators are callables with signature:
    (ticket: Ticket, context: ExecutionContext) -> ValidationResult
"""

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from conductor.executor.base import ExecutionContext
    from conductor.models.ticket import Ticket
    from conductor.validation.validator import ValidationResult

# Global registry for backward compatibility.
# Prefer using AgentRegistry.register_validator() instead.
_CUSTOM_VALIDATORS: dict[str, Callable] = {}


def register_validator(name: str, fn: Callable) -> None:
    """Register a custom validator function by name.

    The function must accept (ticket, context) and return a ValidationResult.
    """
    _CUSTOM_VALIDATORS[name] = fn


def get_registered_validators() -> dict[str, Callable]:
    """Get all registered custom validators."""
    return dict(_CUSTOM_VALIDATORS)
