"""CLI entry points for Conductor."""

import json
from pathlib import Path

import click

from conductor.models.config import ProjectConfig, WatcherConfig
from conductor.observability.log_config import setup_logging


@click.group()
@click.version_option(version="0.1.0")
def main():
    """Conductor — Event-driven tracker orchestration."""
    pass


@main.command()
@click.option("--project", type=click.Path(exists=True), help="Project config JSON file")
@click.option("--tracker", type=click.Choice(["sqlite", "vikunja"]), default="sqlite")
@click.option("--pipeline", type=click.Choice(["full", "minimal"]), default=None)
@click.option("--all-phases", is_flag=True, help="Create all tickets upfront")
@click.option("--workpackages", type=click.Path(exists=True), help="Workpackage planning JSON")
@click.option("--reset", is_flag=True, help="Wipe existing board before initializing")
def init(project, tracker, pipeline, all_phases, workpackages, reset):
    """Initialize the board with tickets."""
    from conductor.board_initializer import initialize_board
    from conductor.git.manager import GitManager
    from conductor.tracker.sqlite_backend import SqliteTracker

    # Load project config
    if project:
        data = json.loads(Path(project).read_text())
        project_config = ProjectConfig(**data)
    else:
        project_config = ProjectConfig()

    # Determine pipeline mode
    pipeline_mode = pipeline or "full"

    # Check for .conductor/config.yaml
    config_path = project_config.project_base_path / ".conductor" / "config.yaml"
    if config_path.exists() and not pipeline:
        try:
            import yaml
        except ImportError:
            click.echo("Error: PyYAML is required for pipeline.yaml loading. Install: pip install pyyaml")
            return

        try:
            cfg = yaml.safe_load(config_path.read_text())
            pipeline_file = cfg.get("pipeline", "")
            if pipeline_file:
                pipeline_path = project_config.project_base_path / pipeline_file
                if pipeline_path.exists():
                    pipeline_mode = f"yaml:{pipeline_path}"
                    click.echo(f"📄 Loading pipeline from {pipeline_file}")
                else:
                    click.echo(f"⚠ Pipeline file not found: {pipeline_file} — using built-in '{pipeline_mode}' pipeline")
        except Exception as e:
            click.echo(f"⚠ Error reading config: {e} — using built-in '{pipeline_mode}' pipeline")

    # Setup tracker
    if tracker == "sqlite":
        db_path = str(project_config.project_base_path / ".conductor" / "tracker.db")

        # Reset: delete existing DB
        if reset:
            db_file = Path(db_path)
            if db_file.exists():
                db_file.unlink()
                click.echo("♻ Board reset — previous tickets cleared")

        trk = SqliteTracker(db_path=db_path)
        trk.connect({})
    else:
        click.echo(f"Tracker '{tracker}' not yet implemented.")
        return

    # Setup git
    git = GitManager(repo_path=project_config.project_base_path)

    # Initialize
    wp_file = Path(workpackages) if workpackages else None
    ids = initialize_board(
        tracker=trk,
        git=git,
        pipeline_mode=pipeline_mode,
        working_directory=project_config.project_base_path,
        all_phases=all_phases,
        workpackages_file=wp_file,
    )
    click.echo(f"Created {len(ids)} tickets: {ids}")


@main.command()
@click.option("--poll-interval", type=int, default=30, help="Poll interval in seconds")
@click.option("--max-concurrent", type=int, default=3, help="Max concurrent agents")
@click.option("--no-hitl", is_flag=True, help="Disable all HITL gates")
@click.option("--log-level", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]), default="INFO")
@click.option("--log-file", type=str, default=".conductor/conductor.log", help="Log file path")
@click.option("--log-json", is_flag=True, help="JSON console output")
def watch(poll_interval, max_concurrent, no_hitl, log_level, log_file, log_json):
    """Start the event watcher."""
    setup_logging(level=log_level, log_file=log_file or None, json_console=log_json)
    from conductor.executor.registry import AgentRegistry
    from conductor.git.manager import GitManager
    from conductor.tracker.sqlite_backend import SqliteTracker
    from conductor.watcher.event_watcher import EventWatcher

    project_config = ProjectConfig()
    watcher_config = WatcherConfig(
        poll_interval_seconds=poll_interval,
        max_concurrent_agents=max_concurrent,
    )
    if no_hitl:
        watcher_config.hitl_default = False

    # Setup tracker
    db_path = str(project_config.project_base_path / ".conductor" / "tracker.db")
    trk = SqliteTracker(db_path=db_path)
    trk.connect({})

    # Setup components
    git = GitManager(repo_path=project_config.project_base_path)

    # Build registry with fallback
    from conductor.agents import build_default_registry
    from conductor.agents.generic import NoOpExecutor
    from conductor.executor.loader import load_agents_module

    registry = build_default_registry()
    registry.set_fallback(NoOpExecutor("__fallback__"))

    # Load user agents if configured
    if watcher_config.agents_module:
        load_agents_module(
            watcher_config.agents_module,
            registry,
            project_dir=project_config.project_base_path,
        )
    else:
        # Try loading from .conductor/config.yaml
        config_path = project_config.project_base_path / ".conductor" / "config.yaml"
        if config_path.exists():
            try:
                import yaml
                cfg = yaml.safe_load(config_path.read_text())
                agents_mod = cfg.get("agents_module", "")
                if agents_mod:
                    load_agents_module(
                        agents_mod, registry,
                        project_dir=project_config.project_base_path,
                    )
            except Exception:
                pass

    watcher = EventWatcher(
        tracker=trk,
        registry=registry,
        git=git,
        config=watcher_config,
        project_config=project_config,
    )
    watcher.run()


@main.command("watch-async")
@click.option("--poll-interval", type=int, default=30, help="Poll interval in seconds")
@click.option("--max-concurrent", type=int, default=3, help="Max concurrent agents")
@click.option("--no-hitl", is_flag=True, help="Disable all HITL gates")
@click.option("--log-level", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]), default="INFO")
@click.option("--log-file", type=str, default=".conductor/conductor.log", help="Log file path")
@click.option("--log-json", is_flag=True, help="JSON console output")
def watch_async(poll_interval, max_concurrent, no_hitl, log_level, log_file, log_json):
    """Start the async event watcher (concurrent agent dispatch)."""
    setup_logging(level=log_level, log_file=log_file or None, json_console=log_json)
    from conductor.agents import build_default_registry
    from conductor.agents.generic import NoOpExecutor
    from conductor.executor.loader import load_agents_module
    from conductor.git.manager import GitManager
    from conductor.tracker.sqlite_backend import SqliteTracker
    from conductor.watcher.async_watcher import AsyncEventWatcher

    project_config = ProjectConfig()
    watcher_config = WatcherConfig(
        poll_interval_seconds=poll_interval,
        max_concurrent_agents=max_concurrent,
    )
    if no_hitl:
        watcher_config.hitl_default = False

    db_path = str(project_config.project_base_path / ".conductor" / "tracker.db")
    trk = SqliteTracker(db_path=db_path)
    trk.connect({})

    git = GitManager(repo_path=project_config.project_base_path)

    registry = build_default_registry()
    registry.set_fallback(NoOpExecutor("__fallback__"))

    # Load user agents
    config_path = project_config.project_base_path / ".conductor" / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            cfg = yaml.safe_load(config_path.read_text())
            agents_mod = cfg.get("agents_module", "")
            if agents_mod:
                load_agents_module(
                    agents_mod, registry,
                    project_dir=project_config.project_base_path,
                )
        except Exception:
            pass

    # Load pipeline for progressive ticket creation
    pipeline_phases = []
    config_path = project_config.project_base_path / ".conductor" / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            cfg = yaml.safe_load(config_path.read_text())
            pipeline_file = cfg.get("pipeline", "")
            if pipeline_file:
                pipeline_path = project_config.project_base_path / pipeline_file
                if pipeline_path.exists():
                    from conductor.pipeline.loader import load_pipeline_yaml
                    pipeline_phases = load_pipeline_yaml(pipeline_path)
                    click.echo(f"📄 Watcher loaded pipeline from {pipeline_file}")
                else:
                    click.echo(f"⚠ Pipeline file not found: {pipeline_file}")
        except Exception as e:
            click.echo(f"⚠ Could not load pipeline: {e}")

    if not pipeline_phases:
        from conductor.pipeline.builder import build_pipeline
        pipeline_phases = build_pipeline("full")
        click.echo("⚠ Using built-in 'full' pipeline (no pipeline.yaml loaded)")

    watcher = AsyncEventWatcher(
        tracker=trk,
        registry=registry,
        git=git,
        config=watcher_config,
        project_config=project_config,
        pipeline=pipeline_phases,
    )
    watcher.run()


@main.command()
@click.option("--port", type=int, default=8080, help="Server port")
def serve(port):
    """Start the tracker web UI."""
    click.echo(f"Starting Conductor dashboard on port {port}...")
    try:
        import uvicorn
        from conductor.tracker.web.app import create_app

        app = create_app()
        uvicorn.run(app, host="0.0.0.0", port=port)
    except ImportError as e:
        click.echo(f"Error: {e}. Run: pip install uvicorn fastapi")


# --- Ticket subcommands ---


@main.group()
def ticket():
    """Ticket operations."""
    pass


@ticket.command("list")
@click.option("--status", type=str, help="Filter by status")
@click.option("--phase", type=str, help="Filter by phase")
@click.option("--workpackage", type=str, help="Filter by workpackage")
def ticket_list(status, phase, workpackage):
    """List tickets."""
    from conductor.models.enums import TicketStatus
    from conductor.tracker.sqlite_backend import SqliteTracker

    trk = SqliteTracker(db_path=".conductor/tracker.db")
    trk.connect({})

    if status:
        tickets = trk.get_tickets_by_status(TicketStatus(status))
    elif phase or workpackage:
        kwargs = {}
        if phase:
            kwargs["phase"] = phase
        if workpackage:
            kwargs["workpackage"] = workpackage
        tickets = trk.get_tickets_by_metadata(**kwargs)
    else:
        # Get all tickets by querying each status
        tickets = []
        for s in TicketStatus:
            tickets.extend(trk.get_tickets_by_status(s))

    if not tickets:
        click.echo("No tickets found.")
        return

    # Table output
    click.echo(f"{'ID':<10} {'Status':<16} {'Phase':<12} {'Title'}")
    click.echo("-" * 70)
    for t in tickets:
        click.echo(
            f"{t.id:<10} {t.status.value:<16} {t.metadata.phase:<12} {t.title}"
        )


@ticket.command("show")
@click.argument("ticket_id")
def ticket_show(ticket_id):
    """Show ticket detail."""
    from conductor.tracker.sqlite_backend import SqliteTracker

    trk = SqliteTracker(db_path=".conductor/tracker.db")
    trk.connect({})

    try:
        t = trk.get_ticket(ticket_id)
    except KeyError:
        click.echo(f"Ticket {ticket_id} not found.")
        return

    click.echo(f"ID:          {t.id}")
    click.echo(f"Title:       {t.title}")
    click.echo(f"Status:      {t.status.value}")
    click.echo(f"Type:        {t.ticket_type.value}")
    click.echo(f"Phase:       {t.metadata.phase}")
    click.echo(f"Step:        {t.metadata.step}")
    click.echo(f"Agent:       {t.metadata.agent_name}")
    click.echo(f"Workpackage: {t.metadata.workpackage or 'N/A'}")
    click.echo(f"Iteration:   {t.metadata.iteration}/{t.metadata.max_iterations}")
    click.echo(f"HITL:        {'yes' if t.metadata.hitl_required else 'no'}")
    click.echo(f"Blocked by:  {t.blocked_by or 'none'}")
    click.echo(f"Blocks:      {t.blocks or 'none'}")
    if t.comments:
        click.echo(f"\nComments ({len(t.comments)}):")
        for c in t.comments[-3:]:
            click.echo(f"  - {c[:100]}")


@ticket.command("approve")
@click.argument("ticket_id")
def ticket_approve(ticket_id):
    """Approve a ticket (move to APPROVED)."""
    from conductor.models.enums import TicketStatus
    from conductor.tracker.sqlite_backend import SqliteTracker

    trk = SqliteTracker(db_path=".conductor/tracker.db")
    trk.connect({})
    trk.update_status(ticket_id, TicketStatus.APPROVED, changed_by="human")
    click.echo(f"✓ {ticket_id} → APPROVED")


@ticket.command("reject")
@click.argument("ticket_id")
@click.option("--comment", "-c", required=True, help="Rejection feedback")
def ticket_reject(ticket_id, comment):
    """Reject a ticket with feedback."""
    from conductor.models.enums import TicketStatus
    from conductor.tracker.sqlite_backend import SqliteTracker

    trk = SqliteTracker(db_path=".conductor/tracker.db")
    trk.connect({})
    trk.add_comment(ticket_id, comment, author="human")
    trk.update_status(ticket_id, TicketStatus.REJECTED, changed_by="human")
    click.echo(f"✓ {ticket_id} → REJECTED")


@ticket.command("pause")
@click.argument("ticket_id")
def ticket_pause(ticket_id):
    """Pause a ticket."""
    from conductor.models.enums import TicketStatus
    from conductor.tracker.sqlite_backend import SqliteTracker

    trk = SqliteTracker(db_path=".conductor/tracker.db")
    trk.connect({})
    trk.update_status(ticket_id, TicketStatus.PAUSED, changed_by="human")
    click.echo(f"✓ {ticket_id} → PAUSED")


@ticket.command("resume")
@click.argument("ticket_id")
def ticket_resume(ticket_id):
    """Resume a paused ticket (move to READY)."""
    from conductor.models.enums import TicketStatus
    from conductor.tracker.sqlite_backend import SqliteTracker

    trk = SqliteTracker(db_path=".conductor/tracker.db")
    trk.connect({})
    trk.update_status(ticket_id, TicketStatus.READY, changed_by="human")
    click.echo(f"✓ {ticket_id} → READY")


@ticket.command("retry")
@click.argument("ticket_id")
def ticket_retry(ticket_id):
    """Retry a failed ticket (move FAILED → READY)."""
    from conductor.models.enums import TicketStatus
    from conductor.tracker.sqlite_backend import SqliteTracker

    trk = SqliteTracker(db_path=".conductor/tracker.db")
    trk.connect({})
    trk.update_status(ticket_id, TicketStatus.READY, changed_by="human")
    click.echo(f"✓ {ticket_id} → READY (retry)")


@main.command("new-project")
@click.argument("name")
def new_project(name):
    """Scaffold a new conductor project."""
    from pathlib import Path

    project_dir = Path(name)
    if project_dir.exists():
        click.echo(f"Error: directory '{name}' already exists.")
        return

    # Create directory structure
    (project_dir / ".conductor").mkdir(parents=True)
    (project_dir / "agents" / "example" / "prompts").mkdir(parents=True)
    (project_dir / "output").mkdir()

    # .conductor/config.yaml
    (project_dir / ".conductor" / "config.yaml").write_text(
        f"""# Conductor project configuration
agents_module: "agents"
pipeline: "pipeline.yaml"

tracker:
  backend: "sqlite"

settings:
  poll_interval_seconds: 30
  hitl_default: true
  stale_ticket_threshold_seconds: 1800
""",
        encoding="utf-8",
    )

    # pipeline.yaml
    (project_dir / "pipeline.yaml").write_text(
        f"""# Pipeline definition for {name}
pipeline:
  name: "{name}"
  phases:
    - id: "phase_1"
      name: "Phase 1"
      scope: "global"
      steps:
        - id: "step_1"
          name: "Example Step"
          agent: "example_agent"
          prompt: "agents/example/prompts/task.md"
          deliverables:
            - path: "output/example_output.md"
              type: "markdown"
          hitl_after: true
""",
        encoding="utf-8",
    )

    # agents/__init__.py
    (project_dir / "agents" / "__init__.py").write_text(
        '''"""Agent registration for this project."""

from conductor.executor.registry import AgentRegistry
from .example import ExampleAgent


def register(registry: AgentRegistry):
    """Register all project agents with conductor."""
    registry.register(ExampleAgent())
''',
        encoding="utf-8",
    )

    # agents/example/__init__.py
    (project_dir / "agents" / "example" / "__init__.py").write_text(
        'from .agent import ExampleAgent\n\n__all__ = ["ExampleAgent"]\n',
        encoding="utf-8",
    )

    # agents/example/agent.py
    (project_dir / "agents" / "example" / "agent.py").write_text(
        '''"""Example agent — replace with your own implementation."""

from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class ExampleAgent(AgentExecutor):
    """Example agent that creates a placeholder deliverable.

    Replace this with your actual agent logic. Each agent lives in its
    own directory with its prompts:

        agents/
        ├── my_agent/
        │   ├── __init__.py
        │   ├── agent.py        ← your executor class
        │   └── prompts/
        │       └── task.md     ← prompt template

    Executor types:
    - AgentExecutor   — direct, do anything
    - ToolExecutor    — run a subprocess
    - HybridExecutor  — context assembly + single LLM call
    - LLMExecutor     — autonomous agent with tool loop
    - ReviewerExecutor — evaluate deliverables, return verdict
    """

    @property
    def agent_name(self) -> str:
        return "example_agent"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(
                f"# {ticket.title}\\n\\n"
                f"Output from example_agent.\\n\\n"
                f"Ticket: {ticket.id}\\n"
                f"Step: {ticket.metadata.step}\\n",
                encoding="utf-8",
            )
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary="Example agent completed",
            deliverables_produced=created,
        )
''',
        encoding="utf-8",
    )

    # agents/example/prompts/task.md
    (project_dir / "agents" / "example" / "prompts" / "task.md").write_text(
        """# Example Prompt

This is a template prompt for the example agent.

## Task

Analyze the input and produce the expected deliverables.

## Context

- Project: {project_name}
- Phase: {phase}
- Step: {step}
""",
        encoding="utf-8",
    )

    click.echo(f"✓ Project '{name}' created.")
    click.echo(f"")
    click.echo(f"  cd {name}")
    click.echo(f"  conductor init")
    click.echo(f"  conductor serve --port 8080")
    click.echo(f"  conductor watch")


# --- Pipeline subcommands ---


@main.group()
def pipeline():
    """Pipeline information."""
    pass


@pipeline.command("show")
@click.option("--mode", type=str, default="full", help="Pipeline mode")
def pipeline_show(mode):
    """Display phase/step tree."""
    from conductor.pipeline.builder import build_pipeline

    phases = build_pipeline(mode)
    for phase in phases:
        click.echo(f"\n{'='*60}")
        click.echo(f"Phase: {phase.phase_id} — {phase.display_name}")
        click.echo(f"  Scope: {phase.execution_scope}")
        if phase.depends_on:
            click.echo(f"  Depends on: {phase.depends_on}")
        if phase.creates_next_phases:
            click.echo(f"  Creates: {phase.creates_next_phases}")
        for step in phase.steps:
            deps = f" (depends: {step.depends_on})" if step.depends_on else ""
            reviewer = " [REVIEWER]" if step.is_reviewer else ""
            hitl = " [HITL]" if step.hitl_after else ""
            click.echo(f"    → {step.step_id}: {step.display_name}{reviewer}{hitl}{deps}")


@pipeline.command("agents")
@click.option("--mode", type=str, default="full", help="Pipeline mode")
def pipeline_agents(mode):
    """List all agents in the pipeline."""
    from conductor.pipeline.builder import build_pipeline

    phases = build_pipeline(mode)
    agents = set()
    for phase in phases:
        for step in phase.steps:
            if step.agent_name:
                agents.add(step.agent_name)

    click.echo("Registered agents in pipeline:")
    for agent in sorted(agents):
        click.echo(f"  - {agent}")


@pipeline.command("validate")
@click.option("--mode", type=str, default="full", help="Pipeline mode")
def pipeline_validate(mode):
    """Validate pipeline definition for errors."""
    from conductor.pipeline.builder import build_pipeline
    from conductor.pipeline.validator import validate_pipeline

    phases = build_pipeline(mode)
    errors = validate_pipeline(phases)

    if errors:
        click.echo(f"✗ Pipeline validation failed ({len(errors)} errors):")
        for err in errors:
            click.echo(f"  - {err}")
        raise SystemExit(1)
    else:
        step_count = sum(len(p.steps) for p in phases)
        click.echo(
            f"✓ Pipeline valid: {len(phases)} phases, {step_count} steps, no errors"
        )


if __name__ == "__main__":
    main()
