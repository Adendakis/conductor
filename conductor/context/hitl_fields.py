"""HITL field parser — extract and update editable field values in ticket descriptions."""

import re
from typing import Any

import yaml

# Markers that fence the HITL fields YAML block in ticket descriptions.
_START_MARKER = "<!-- HITL_FIELDS_START -->"
_END_MARKER = "<!-- HITL_FIELDS_END -->"

# Regex to extract the YAML content between the markers.
# The YAML block is wrapped in a ```yaml fenced code block.
_BLOCK_RE = re.compile(
    re.escape(_START_MARKER)
    + r"\s*```yaml\s*\n(.*?)\n\s*```\s*"
    + re.escape(_END_MARKER),
    re.DOTALL,
)


def parse_hitl_fields(description: str) -> dict[str, Any]:
    """Extract HITL field values from a ticket description.

    Returns a dict of ``{field_name: value}`` parsed from the YAML block
    between the ``HITL_FIELDS_START`` / ``HITL_FIELDS_END`` markers.
    Comments (``# …``) in the YAML are ignored during parsing.

    Returns an empty dict if no HITL block is found or parsing fails.
    """
    match = _BLOCK_RE.search(description)
    if not match:
        return {}

    yaml_text = match.group(1)
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return {}

    if not isinstance(parsed, dict):
        return {}

    return parsed


def update_hitl_fields(description: str, values: dict[str, Any]) -> str:
    """Replace the HITL field values in a ticket description.

    Rebuilds the YAML block between the markers with the supplied *values*
    dict while preserving any inline comments from the original block.

    If the description does not contain a HITL block, it is returned unchanged.
    """
    match = _BLOCK_RE.search(description)
    if not match:
        return description

    # Preserve inline comments from the original block so the human-readable
    # labels survive round-trips.
    original_yaml = match.group(1)
    comments: dict[str, str] = {}
    for line in original_yaml.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "#" in stripped:
            key_part, comment_part = stripped.split("#", 1)
            key = key_part.split(":")[0].strip()
            comments[key] = comment_part.strip()

    # Build new YAML lines
    lines: list[str] = []
    for name, value in values.items():
        yaml_value = _format_yaml_value(value)
        if name in comments:
            lines.append(f"{name}: {yaml_value}  # {comments[name]}")
        else:
            lines.append(f"{name}: {yaml_value}")

    new_yaml = "\n".join(lines)
    new_block = f"{_START_MARKER}\n```yaml\n{new_yaml}\n```\n{_END_MARKER}"

    return description[: match.start()] + new_block + description[match.end() :]


def build_hitl_fields_block(
    fields: list[Any],
) -> str:
    """Build the HITL fields YAML block for embedding in a ticket description.

    *fields* is a list of ``HitlFieldDefinition`` model instances (or any
    object with ``name``, ``label``, ``type``, ``default``, and ``options``
    attributes).

    Returns a string like::

        <!-- HITL_FIELDS_START -->
        ```yaml
        calibration_run: false  # Run calibration pass (boolean)
        ```
        <!-- HITL_FIELDS_END -->
    """
    lines: list[str] = []
    for f in fields:
        yaml_value = _format_yaml_value(f.default)
        comment_parts: list[str] = []
        if f.label:
            comment_parts.append(f.label)
        if f.type == "select" and f.options:
            comment_parts.append(f"options: {', '.join(f.options)}")
        elif f.type != "text":
            comment_parts.append(f"({f.type})")

        comment = " — ".join(comment_parts) if comment_parts else ""
        if comment:
            lines.append(f"{f.name}: {yaml_value}  # {comment}")
        else:
            lines.append(f"{f.name}: {yaml_value}")

    yaml_body = "\n".join(lines)
    return (
        f"\n\n<!-- HITL_FIELDS_START -->\n"
        f"```yaml\n{yaml_body}\n```\n"
        f"<!-- HITL_FIELDS_END -->"
    )


def parse_hitl_field_meta(description: str) -> dict[str, dict[str, Any]]:
    """Extract field metadata (type, label, options) from YAML comments.

    Returns a dict keyed by field name::

        {
            "target_language": {
                "label": "Target language for code generation",
                "type": "select",
                "options": ["Java", "C#", "Python", "Go"],
            },
            "calibration_run": {
                "label": "Run calibration pass",
                "type": "boolean",
                "options": [],
            },
        }

    The metadata is reconstructed from the inline ``# comments`` that
    ``build_hitl_fields_block`` embeds.  This keeps the ticket description
    fully self-contained — no need to load the pipeline YAML at render time.
    """
    match = _BLOCK_RE.search(description)
    if not match:
        return {}

    meta: dict[str, dict[str, Any]] = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        name = stripped.split(":")[0].strip()
        field_type = "text"
        label = ""
        options: list[str] = []

        if "#" in stripped:
            comment = stripped.split("#", 1)[1].strip()

            # Check for "options: A, B, C" → select type
            opt_match = re.search(r"options:\s*(.+)", comment)
            if opt_match:
                field_type = "select"
                options = [o.strip() for o in opt_match.group(1).split(",")]
                # Label is everything before " — options:"
                label = re.split(r"\s*—\s*options:", comment)[0].strip()
            elif "(boolean)" in comment:
                field_type = "boolean"
                label = comment.replace("— (boolean)", "").replace("(boolean)", "").strip()
            elif "(number)" in comment:
                field_type = "number"
                label = comment.replace("— (number)", "").replace("(number)", "").strip()
            else:
                label = comment

        meta[name] = {"label": label, "type": field_type, "options": options}

    return meta


def has_hitl_fields(description: str) -> bool:
    """Return True if the description contains a HITL fields block."""
    return _START_MARKER in description and _END_MARKER in description


def _format_yaml_value(value: Any) -> str:
    """Format a Python value for inline YAML output."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        # Quote strings that could be misinterpreted by YAML
        if not value:
            return '""'
        if value.lower() in ("true", "false", "null", "yes", "no"):
            return f'"{value}"'
        # If it contains special chars, quote it
        if any(c in value for c in ":#{}[]|>&*!%@`"):
            return f'"{value}"'
        return value
    return str(value)
