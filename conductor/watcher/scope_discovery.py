"""Scope discovery interface — how projects define their scope units.

Conductor calls these methods during ticket creation to discover
workpackages, pods, and other scope units. Projects implement this
interface to read their own data formats.

The default implementation reads the generic pod assignment format
and returns empty lists for workpackages (projects must override
or provide their data in the generic format).
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class ScopeDiscovery(ABC):
    """Interface for discovering scope units (workpackages, domains, pods).

    Projects implement this to read their own data formats.
    Conductor calls these methods during dynamic ticket creation.

    Example implementation for a migration project::

        class MigrationScopeDiscovery(ScopeDiscovery):
            def discover_workpackages(self, working_directory):
                planning = working_directory / "output/planning.json"
                data = json.loads(planning.read_text())
                return [wp["id"] for wp in data["workpackages"]]

            def discover_pods(self, working_directory):
                pods = working_directory / "output/pods.json"
                data = json.loads(pods.read_text())
                return list(data["pods"].keys())
    """

    @abstractmethod
    def discover_workpackages(self, working_directory: Path) -> list[str]:
        """Return ordered list of workpackage IDs.

        Called when a phase with scope 'per_workpackage' needs tickets created.
        """
        ...

    @abstractmethod
    def discover_pods(self, working_directory: Path) -> list[str]:
        """Return ordered list of pod IDs.

        Called when a phase with scope 'per_pod' needs tickets created.
        """
        ...

    def discover_domains(self, working_directory: Path) -> list[str]:
        """Return ordered list of domain names.

        Called when a phase with scope 'per_domain' needs tickets created.
        Override if your project uses domain-scoped phases.
        Default: empty list.
        """
        return []

    def get_workpackage_type(
        self, wp_id: str, working_directory: Path
    ) -> Optional[str]:
        """Return the type of a workpackage for conditional step filtering.

        Steps with a `workpackage_type` field are only created for workpackages
        whose type matches. Return None to skip type filtering.
        Default: None (all steps created for all workpackages).
        """
        return None


class DefaultScopeDiscovery(ScopeDiscovery):
    """Default scope discovery — returns empty lists.

    Projects that don't need dynamic scope discovery can use this.
    Projects that do should implement ScopeDiscovery and register it
    via their agents module register() function.
    """

    def discover_workpackages(self, working_directory: Path) -> list[str]:
        return []

    def discover_pods(self, working_directory: Path) -> list[str]:
        return []
