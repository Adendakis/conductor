"""Built-in file operation tools for LLM agents."""

import os
import re
from pathlib import Path

from .base import AgentTool, ToolContext, ToolParameter, ToolSandbox


class ReadFileTool(AgentTool):
    """Read a file from the working directory."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read a file. Returns content or error message."

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("path", "string", "Relative file path"),
            ToolParameter(
                "start_line", "integer", "First line (1-based, optional)",
                required=False,
            ),
            ToolParameter(
                "end_line", "integer", "Last line (1-based, optional)",
                required=False,
            ),
        ]

    async def execute(self, arguments: dict, context: ToolContext) -> str:
        rel_path = arguments.get("path", "")
        if not rel_path:
            return "Error: path is required"

        sandbox = ToolSandbox(working_directory=context.working_directory)
        if not sandbox.can_read(rel_path):
            return f"Error: access denied for path: {rel_path}"

        full_path = context.working_directory / rel_path

        # Directory detection
        if full_path.is_dir():
            entries = sorted(os.listdir(full_path))[:100]
            return f"Directory listing for {rel_path}:\n" + "\n".join(entries)

        if not full_path.is_file():
            return f"Error: file not found: {rel_path}"

        try:
            content = full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"Error: cannot read binary file: {rel_path}"

        # Line range support
        start_line = arguments.get("start_line")
        end_line = arguments.get("end_line")

        if start_line or end_line:
            lines = content.splitlines()
            start = (int(start_line) - 1) if start_line else 0
            end = int(end_line) if end_line else len(lines)
            start = max(0, start)
            end = min(len(lines), end)
            content = "\n".join(lines[start:end])

        # Large file handling
        if len(content) > 100_000:
            content = content[:100_000] + "\n\n[... truncated at 100KB ...]"

        return content


class WriteFileTool(AgentTool):
    """Write content to a file in the working directory."""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file. Creates directories as needed."

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("path", "string", "Relative file path"),
            ToolParameter("content", "string", "File content to write"),
        ]

    async def execute(self, arguments: dict, context: ToolContext) -> str:
        rel_path = arguments.get("path", "")
        content = arguments.get("content", "")

        if not rel_path:
            return "Error: path is required"

        sandbox = ToolSandbox(working_directory=context.working_directory)
        if not sandbox.can_write(rel_path):
            return f"Error: write access denied for path: {rel_path}"

        full_path = context.working_directory / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

        context.files_written.append(rel_path)
        return f"Written {len(content)} bytes to {rel_path}"


class ListFilesTool(AgentTool):
    """List files in a directory (non-recursive)."""

    @property
    def name(self) -> str:
        return "list_files"

    @property
    def description(self) -> str:
        return "List files and directories. Non-recursive."

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                "directory", "string",
                "Relative directory path",
                required=False, default=".",
            ),
        ]

    async def execute(self, arguments: dict, context: ToolContext) -> str:
        rel_dir = arguments.get("directory", ".")
        full_path = context.working_directory / rel_dir

        if not full_path.is_dir():
            return f"Error: not a directory: {rel_dir}"

        entries = []
        for entry in sorted(full_path.iterdir()):
            suffix = "/" if entry.is_dir() else ""
            entries.append(f"{entry.name}{suffix}")

        if not entries:
            return f"Directory {rel_dir} is empty"

        return "\n".join(entries[:200])


class SearchFileTool(AgentTool):
    """Search for a pattern in a file."""

    @property
    def name(self) -> str:
        return "search_file"

    @property
    def description(self) -> str:
        return "Search for a regex pattern in a file. Returns matches with context."

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("path", "string", "File path to search"),
            ToolParameter("pattern", "string", "Regex pattern (case-insensitive)"),
            ToolParameter(
                "context_lines", "integer",
                "Lines of context around matches",
                required=False, default="5",
            ),
        ]

    async def execute(self, arguments: dict, context: ToolContext) -> str:
        rel_path = arguments.get("path", "")
        pattern = arguments.get("pattern", "")
        ctx_lines = int(arguments.get("context_lines", 5))

        if not rel_path or not pattern:
            return "Error: path and pattern are required"

        full_path = context.working_directory / rel_path
        if not full_path.is_file():
            return f"Error: file not found: {rel_path}"

        try:
            content = full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"Error: cannot read binary file: {rel_path}"

        lines = content.splitlines()
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return f"Error: invalid regex: {e}"

        matches = []
        for i, line in enumerate(lines):
            if regex.search(line):
                start = max(0, i - ctx_lines)
                end = min(len(lines), i + ctx_lines + 1)
                snippet = "\n".join(
                    f"{'>' if j == i else ' '} {j+1}: {lines[j]}"
                    for j in range(start, end)
                )
                matches.append(snippet)

        if not matches:
            return f"No matches for '{pattern}' in {rel_path}"

        # Limit output
        if len(matches) > 20:
            matches = matches[:20]
            matches.append(f"... and more matches (showing first 20)")

        return f"Found {len(matches)} match(es) in {rel_path}:\n\n" + "\n---\n".join(matches)


class ReadFilesTool(AgentTool):
    """Batch read multiple files in a single tool call."""

    @property
    def name(self) -> str:
        return "read_files"

    @property
    def description(self) -> str:
        return "Read multiple files at once. Paths separated by newlines."

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                "paths", "string", "Newline-separated list of file paths"
            ),
        ]

    async def execute(self, arguments: dict, context: ToolContext) -> str:
        paths_str = arguments.get("paths", "")
        if not paths_str:
            return "Error: paths is required"

        paths = [p.strip() for p in paths_str.splitlines() if p.strip()]
        sandbox = ToolSandbox(working_directory=context.working_directory)

        results = []
        total_size = 0
        max_total = 200_000  # 200KB budget for batch reads

        for rel_path in paths:
            if not sandbox.can_read(rel_path):
                results.append(f"## {rel_path}\n\nError: access denied")
                continue

            full_path = context.working_directory / rel_path
            if not full_path.is_file():
                results.append(f"## {rel_path}\n\nError: file not found")
                continue

            try:
                content = full_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                results.append(f"## {rel_path}\n\nError: binary file")
                continue

            if total_size + len(content) > max_total:
                results.append(
                    f"## {rel_path}\n\n[skipped: batch size budget exceeded]"
                )
                break

            total_size += len(content)
            results.append(f"## {rel_path}\n\n{content}")

        return "\n\n---\n\n".join(results)
