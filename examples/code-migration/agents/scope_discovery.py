"""ACM-specific scope discovery implementation.

This is a reference implementation showing how a project implements
the ScopeDiscovery interface to teach conductor about its data formats.

The ACM migration project uses:
- Workpackage_Planning.json with a `migrationSequence` array
- Pod_Assignment.json with a `pod_assignments` dict (project-specific format)
- Workpackage types derived from flowId prefixes (FLOW_* vs JOB_*)

Other projects would implement ScopeDiscovery differently based on
their own data formats.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from conductor.watcher.scope_discovery import ScopeDiscovery

logger = logging.getLogger(__name__)


class AcmScopeDiscovery(ScopeDiscovery):
    """Scope discovery for the ACM legacy migration pipeline.

    Reads ACM-specific JSON formats and maps them to conductor's
    generic scope discovery interface.

    Usage in agents/__init__.py::

        from .scope_discovery import AcmScopeDiscovery

        def register(registry):
            registry.register(MyAgent())
            registry.set_scope_discovery(AcmScopeDiscovery())
    """

    def __init__(
        self,
        workpackage_planning_path: str = "output/analysis/workpackages/Workpackage_Planning.json",
        pod_assignment_path: str = "output/analysis/workpackages/Pod_Assignment.json",
    ):
        self._wp_planning_path = workpackage_planning_path
        self._pod_assignment_path = pod_assignment_path

    def discover_workpackages(self, working_directory: Path) -> list[str]:
        """Read Workpackage_Planning.json to get WP IDs.

        Expected format::

            {
                "migrationSequence": [
                    {"workpackageId": 1, "name": "User Management", ...},
                    {"workpackageId": 2, "name": "Post Management", ...}
                ]
            }

        Returns IDs formatted as WP-001, WP-002, etc.
        """
        planning_path = working_directory / self._wp_planning_path
        if not planning_path.exists():
            logger.warning(f"Workpackage planning not found: {planning_path}")
            return []

        try:
            data = json.loads(planning_path.read_text(encoding="utf-8"))
            return [
                f"WP-{item['workpackageId']:03d}"
                for item in data.get("migrationSequence", [])
            ]
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Error reading workpackage planning: {e}")
            return []

    def discover_pods(self, working_directory: Path) -> list[str]:
        """Read Pod_Assignment.json to get pod IDs.

        Supports two formats:

        Generic conductor format::

            {"pods": {"pod-a": {"workpackages": [...]}, ...}}

        ACM-specific format::

            {"pod_assignments": {"pod-a": {"workpackages": [...]}, ...}}

        Returns pod IDs in order.
        """
        pod_path = working_directory / self._pod_assignment_path
        if not pod_path.exists():
            logger.warning(f"Pod assignment not found: {pod_path}")
            return []

        try:
            data = json.loads(pod_path.read_text(encoding="utf-8"))
            # Try generic format first
            pods = data.get("pods", {})
            if isinstance(pods, dict) and pods:
                return list(pods.keys())
            # Fall back to ACM-specific format
            pods = data.get("pod_assignments", {})
            if isinstance(pods, dict) and pods:
                return list(pods.keys())
            return []
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Error reading pod assignment: {e}")
            return []

    def get_workpackage_type(
        self, wp_id: str, working_directory: Path
    ) -> Optional[str]:
        """Determine workpackage type from the planning JSON.

        ACM convention: workpackages with flowId starting with "FLOW_"
        are type "flow", those starting with "JOB_" are type "job".
        Steps with `workpackage_type: "flow"` only run for flow WPs, etc.
        """
        planning_path = working_directory / self._wp_planning_path
        if not planning_path.exists():
            return None

        try:
            data = json.loads(planning_path.read_text(encoding="utf-8"))
            for item in data.get("migrationSequence", []):
                item_id = f"WP-{item['workpackageId']:03d}"
                if item_id == wp_id:
                    # Check explicit type field first
                    if "type" in item:
                        return item["type"]
                    # Infer from flowId prefix
                    flow_id = item.get("flowId", "")
                    if flow_id.startswith("FLOW_"):
                        return "flow"
                    if flow_id.startswith("JOB_"):
                        return "job"
                    return item.get("type")
        except (json.JSONDecodeError, KeyError):
            pass
        return None

    def map_to_generic_pod_format(self, working_directory: Path) -> Optional[Path]:
        """Convert ACM pod assignment to conductor's generic format.

        ACM agents produce a rich pod assignment with domain_cluster,
        shared_entities, cross_pod_dependencies, etc. Conductor only
        needs the generic format: {"pods": {...}, "merge_order": [...]}.

        This method reads the ACM format, extracts the conductor-relevant
        fields, and writes a generic Pod_Assignment.json.

        Call this from a post-processing step or agent if needed.
        Returns the path to the generic file, or None on failure.
        """
        acm_path = working_directory / self._pod_assignment_path
        if not acm_path.exists():
            return None

        try:
            data = json.loads(acm_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            return None

        # Extract pods
        acm_pods = data.get("pod_assignments", {})
        if not acm_pods:
            return None

        generic_pods = {}
        for pod_id, pod_data in acm_pods.items():
            generic_pods[pod_id] = {
                "workpackages": pod_data.get("workpackages", [])
            }

        # Extract merge order from cross_pod_dependencies
        merge_order = []
        for dep in data.get("cross_pod_dependencies", []):
            from_pod = dep.get("from_pod")
            to_pod = dep.get("to_pod")
            if from_pod and to_pod:
                pair = [from_pod, to_pod]
                if pair not in merge_order:
                    merge_order.append(pair)

        generic = {
            "pods": generic_pods,
            "merge_order": merge_order,
        }

        # Write generic format
        generic_path = acm_path.parent / "Pod_Assignment_generic.json"
        generic_path.write_text(
            json.dumps(generic, indent=2), encoding="utf-8"
        )
        logger.info(f"Wrote generic pod assignment: {generic_path}")
        return generic_path
