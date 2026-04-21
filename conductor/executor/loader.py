"""Agent module loader — imports user's agent module and registers agents."""

import importlib
import logging
import sys
from pathlib import Path
from typing import Optional

from .registry import AgentRegistry

logger = logging.getLogger(__name__)


def load_agents_module(
    module_path: str,
    registry: AgentRegistry,
    project_dir: Optional[Path] = None,
) -> None:
    """Import a user's agent module and call its register() function.

    The module must expose: def register(registry: AgentRegistry) -> None

    Args:
        module_path: Python module path (e.g., "agents" or "my_project.agents")
        registry: The AgentRegistry to register agents into
        project_dir: Optional project directory to add to sys.path
    """
    # Add project directory to sys.path so relative imports work
    if project_dir:
        project_str = str(project_dir.resolve())
        if project_str not in sys.path:
            sys.path.insert(0, project_str)

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        logger.warning(
            f"Could not import agents module '{module_path}': {e}. "
            f"Using built-in generic executors only."
        )
        return

    # Look for register() function
    register_fn = getattr(module, "register", None)
    if register_fn is None:
        logger.warning(
            f"Agents module '{module_path}' has no register() function. "
            f"Expected: def register(registry: AgentRegistry) -> None"
        )
        return

    if not callable(register_fn):
        logger.warning(
            f"Agents module '{module_path}' has 'register' but it's not callable."
        )
        return

    try:
        register_fn(registry)
        logger.info(
            f"Loaded agents from '{module_path}': {registry.list_agents()}"
        )
    except Exception as e:
        logger.error(f"Error calling register() in '{module_path}': {e}")
