"""Deliverable validation after agent execution."""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from conductor.executor.base import ExecutionContext
    from conductor.models.ticket import Ticket


class ValidationResult(BaseModel):
    """Result of deliverable validation."""

    passed: bool = True
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DeliverableValidator:
    """Validates deliverables produced by an agent against expectations.

    Runs after agent execution, before status transitions to AWAITING_REVIEW.
    If validation fails, ticket goes to FAILED.
    """

    def __init__(
        self,
        custom_validators: Optional[dict[str, Callable]] = None,
    ):
        self.custom_validators = custom_validators or {}

    def validate(
        self, ticket: "Ticket", context: "ExecutionContext"
    ) -> ValidationResult:
        """Run all validations for a ticket's expected deliverables."""
        errors: list[str] = []
        warnings: list[str] = []

        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str

            # Directory check
            if path_str.endswith("/"):
                if not full_path.is_dir():
                    errors.append(f"Expected directory not found: {path_str}")
                elif not any(full_path.iterdir()):
                    errors.append(f"Expected directory is empty: {path_str}")
                continue

            # File existence
            if not full_path.is_file():
                errors.append(f"Expected file not found: {path_str}")
                continue

            # Size check (minimum 100 bytes)
            size = full_path.stat().st_size
            if size < 100:
                errors.append(
                    f"File too small: {path_str} ({size} bytes, minimum 100)"
                )
                continue

            # Type-specific validation
            type_errors = self._validate_file_type(full_path, path_str)
            errors.extend(type_errors)

        # Custom validators
        for validator_name in ticket.metadata.custom_validators:
            if validator_name in self.custom_validators:
                custom_result = self.custom_validators[validator_name](
                    ticket, context
                )
                if isinstance(custom_result, ValidationResult):
                    errors.extend(custom_result.errors)
                    warnings.extend(custom_result.warnings)

        return ValidationResult(
            passed=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _validate_file_type(self, path: Path, rel_path: str) -> list[str]:
        """Type-specific validation based on file extension."""
        errors: list[str] = []
        suffix = path.suffix.lower()

        if suffix == ".json":
            try:
                content = path.read_text(encoding="utf-8")
                json.loads(content)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                errors.append(f"Invalid JSON in {rel_path}: {e}")

        elif suffix == ".md":
            try:
                content = path.read_text(encoding="utf-8")
                if not content.strip():
                    errors.append(f"Empty markdown file: {rel_path}")
                elif not any(
                    line.startswith("#") for line in content.splitlines()
                ):
                    errors.append(
                        f"Markdown file has no headings: {rel_path}"
                    )
            except UnicodeDecodeError:
                errors.append(f"Cannot read markdown file: {rel_path}")

        elif suffix == ".sql":
            try:
                content = path.read_text(encoding="utf-8")
                if not content.strip():
                    errors.append(f"Empty SQL file: {rel_path}")
            except UnicodeDecodeError:
                errors.append(f"Cannot read SQL file: {rel_path}")

        return errors
