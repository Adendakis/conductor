"""Agent tool base classes and sandboxing."""

import fnmatch
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ToolParameter:
    """Schema for a single tool parameter."""

    name: str
    type: str  # "string", "integer", "boolean"
    description: str
    required: bool = True
    default: Optional[str] = None


@dataclass
class ToolContext:
    """Runtime context for tool execution."""

    working_directory: Path = field(default_factory=lambda: Path("."))
    workpackage_id: Optional[str] = None
    domain_name: Optional[str] = None
    turns_since_write: int = 0
    total_tool_calls: int = 0
    files_written: list[str] = field(default_factory=list)


@dataclass
class ToolSandbox:
    """Defines access boundaries for agent tools."""

    working_directory: Path = field(default_factory=lambda: Path("."))
    read_blocked_patterns: list[str] = field(
        default_factory=lambda: [
            "*.dat",
            "*.ps",
            "*/ebcdic/*",
            ".migration_state.json",
        ]
    )
    write_blocked_patterns: list[str] = field(
        default_factory=lambda: [
            "output/analysis/*",
            "input/*",
            ".migration_state.json",
        ]
    )
    write_allowed_exceptions: list[str] = field(
        default_factory=lambda: [
            "*/Pod_Assignment.json",
        ]
    )

    def can_read(self, path: str) -> bool:
        """Check if a path is readable by the agent."""
        for pattern in self.read_blocked_patterns:
            if fnmatch.fnmatch(path, pattern):
                return False
        return True

    def can_write(self, path: str) -> bool:
        """Check if a path is writable by the agent."""
        # Check exceptions first
        for pattern in self.write_allowed_exceptions:
            if fnmatch.fnmatch(path, pattern):
                return True
        # Check blocks
        for pattern in self.write_blocked_patterns:
            if fnmatch.fnmatch(path, pattern):
                return False
        return True


class AgentTool(ABC):
    """Base class for tools available to LLM agents."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name (used in LLM tool_use blocks)."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description."""
        ...

    @property
    @abstractmethod
    def parameters(self) -> list[ToolParameter]:
        """Parameter schema for this tool."""
        ...

    @abstractmethod
    async def execute(self, arguments: dict, context: ToolContext) -> str:
        """Execute the tool with given arguments. Returns result as string."""
        ...

    def to_bedrock_schema(self) -> dict:
        """Convert to Bedrock Converse tool definition format."""
        properties = {}
        required = []
        for param in self.parameters:
            properties[param.name] = {
                "type": param.type,
                "description": param.description,
            }
            if param.required:
                required.append(param.name)
        schema: dict = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required
        return {
            "toolSpec": {
                "name": self.name,
                "description": self.description,
                "inputSchema": {"json": schema},
            }
        }
