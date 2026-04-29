"""FastAPI dashboard for the built-in SQLite tracker."""

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from conductor.context.hitl_fields import (
    has_hitl_fields,
    parse_hitl_field_meta,
    parse_hitl_fields,
    update_hitl_fields,
)
from conductor.models.enums import TicketStatus
from conductor.tracker.sqlite_backend import SqliteTracker

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def _coerce_form_value(raw: str, original: object) -> object:
    """Coerce a form string value to match the type of *original*."""
    if isinstance(original, bool):
        return raw.lower() in ("true", "on", "1", "yes")
    if isinstance(original, int):
        try:
            return int(raw)
        except (ValueError, TypeError):
            return original
    if isinstance(original, float):
        try:
            return float(raw)
        except (ValueError, TypeError):
            return original
    return str(raw)


def create_app(db_path: str = ".conductor/tracker.db") -> FastAPI:
    """Create the FastAPI dashboard app."""
    from fastapi.responses import FileResponse

    app = FastAPI(title="Conductor Dashboard", version="0.1.0")

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    tracker = SqliteTracker(db_path=db_path)
    tracker.connect({})

    # Resolve project root from db_path (db is at .conductor/tracker.db)
    _project_root = Path(db_path).parent.parent

    @app.get("/logo")
    async def logo():
        """Serve project logo if exists, else default conductor logo."""
        # Check project-level logo
        for name in ("logo.png", "logo.svg", "logo.jpg"):
            project_logo = _project_root / ".conductor" / name
            if project_logo.exists():
                media = "image/png" if name.endswith(".png") else "image/svg+xml" if name.endswith(".svg") else "image/jpeg"
                return FileResponse(str(project_logo), media_type=media)

        # Default conductor logo
        default_logo = _STATIC_DIR / "conductor_logo.png"
        if default_logo.exists():
            return FileResponse(str(default_logo), media_type="image/png")

        # No logo at all
        return HTMLResponse("", status_code=204)

    # --- HTML Pages ---

    @app.get("/", response_class=HTMLResponse)
    async def board_page(request: Request):
        """Kanban board view."""
        return templates.TemplateResponse("board.html", {"request": request})

    @app.get("/ticket/{ticket_id}", response_class=HTMLResponse)
    async def ticket_page(request: Request, ticket_id: str):
        """Ticket detail page."""
        try:
            ticket = tracker.get_ticket(ticket_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return templates.TemplateResponse(
            "ticket_detail.html", {"request": request, "ticket": ticket}
        )

    # --- API Endpoints ---

    @app.get("/api/tickets")
    async def api_list_tickets(
        status: Optional[str] = None,
        phase: Optional[str] = None,
        workpackage: Optional[str] = None,
    ):
        """List tickets with optional filters."""
        if status:
            tickets = tracker.get_tickets_by_status(TicketStatus(status))
        elif phase or workpackage:
            kwargs = {}
            if phase:
                kwargs["phase"] = phase
            if workpackage:
                kwargs["workpackage"] = workpackage
            tickets = tracker.get_tickets_by_metadata(**kwargs)
        else:
            tickets = []
            for s in TicketStatus:
                tickets.extend(tracker.get_tickets_by_status(s))
        return [t.model_dump() for t in tickets]

    @app.get("/api/tickets/{ticket_id}")
    async def api_get_ticket(ticket_id: str):
        """Get ticket detail."""
        try:
            ticket = tracker.get_ticket(ticket_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return ticket.model_dump()

    @app.patch("/api/tickets/{ticket_id}/status")
    async def api_update_status(ticket_id: str, request: Request):
        """Change ticket status."""
        body = await request.json()
        new_status = body.get("status")
        if not new_status:
            raise HTTPException(status_code=400, detail="status field required")
        try:
            tracker.update_status(
                ticket_id, TicketStatus(new_status), changed_by="human"
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return {"ok": True, "ticket_id": ticket_id, "status": new_status}

    @app.post("/api/tickets/{ticket_id}/comments")
    async def api_add_comment(ticket_id: str, request: Request):
        """Add a comment to a ticket."""
        body = await request.json()
        comment = body.get("comment", "")
        if not comment:
            raise HTTPException(status_code=400, detail="comment field required")
        tracker.add_comment(ticket_id, comment, author="human")
        return {"ok": True}

    @app.get("/api/board")
    async def api_board():
        """Board view data — tickets grouped by status."""
        columns = {}
        for status in TicketStatus:
            tickets = tracker.get_tickets_by_status(status)
            columns[status.value] = [t.model_dump() for t in tickets]
        return columns

    @app.get("/api/stats")
    async def api_stats():
        """Dashboard stats."""
        stats = {"by_status": {}, "by_phase": {}, "total": 0}
        all_tickets = []
        for status in TicketStatus:
            tickets = tracker.get_tickets_by_status(status)
            stats["by_status"][status.value] = len(tickets)
            all_tickets.extend(tickets)
        stats["total"] = len(all_tickets)

        # Group by phase
        phases: dict[str, int] = {}
        for t in all_tickets:
            phase = t.metadata.phase or "unknown"
            phases[phase] = phases.get(phase, 0) + 1
        stats["by_phase"] = phases

        # Aggregate metrics (runtime + cost)
        if hasattr(tracker, 'get_aggregate_metrics'):
            stats["metrics"] = tracker.get_aggregate_metrics()

        return stats

    # --- HTMX Partials ---

    @app.get("/partials/board", response_class=HTMLResponse)
    async def partial_board(
        request: Request,
        phase: Optional[str] = None,
        workpackage: Optional[str] = None,
        status: Optional[str] = None,
    ):
        """HTMX partial: board columns with optional filters."""
        columns: dict[str, list] = {}
        for s in TicketStatus:
            columns[s.value] = []

        if status:
            # Show only one status column
            tickets = tracker.get_tickets_by_status(TicketStatus(status))
            if phase:
                tickets = [t for t in tickets if t.metadata.phase == phase]
            if workpackage:
                tickets = [t for t in tickets if t.metadata.workpackage == workpackage]
            columns[status] = tickets
        else:
            for s in TicketStatus:
                tickets = tracker.get_tickets_by_status(s)
                if phase:
                    tickets = [t for t in tickets if t.metadata.phase == phase]
                if workpackage:
                    tickets = [t for t in tickets if t.metadata.workpackage == workpackage]
                columns[s.value] = tickets

        return templates.TemplateResponse(
            "partials/board_columns.html",
            {"request": request, "columns": columns},
        )

    @app.get("/partials/ticket/{ticket_id}", response_class=HTMLResponse)
    async def partial_ticket(request: Request, ticket_id: str):
        """HTMX partial: ticket detail panel."""
        try:
            ticket = tracker.get_ticket(ticket_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Ticket not found")

        # Parse HITL fields for the template
        hitl_values = {}
        hitl_meta = {}
        has_hitl = False
        if ticket.description and has_hitl_fields(ticket.description):
            hitl_values = parse_hitl_fields(ticket.description)
            hitl_meta = parse_hitl_field_meta(ticket.description)
            has_hitl = True

        return templates.TemplateResponse(
            "partials/ticket_panel.html",
            {
                "request": request,
                "ticket": ticket,
                "has_hitl": has_hitl,
                "hitl_values": hitl_values,
                "hitl_meta": hitl_meta,
            },
        )

    @app.get("/partials/table", response_class=HTMLResponse)
    async def partial_table(
        request: Request,
        phase: Optional[str] = None,
        workpackage: Optional[str] = None,
        status: Optional[str] = None,
    ):
        """HTMX partial: table view of all tickets."""
        if status:
            tickets = tracker.get_tickets_by_status(TicketStatus(status))
            if phase:
                tickets = [t for t in tickets if t.metadata.phase == phase]
            if workpackage:
                tickets = [t for t in tickets if t.metadata.workpackage == workpackage]
        elif phase or workpackage:
            kwargs = {}
            if phase:
                kwargs["phase"] = phase
            if workpackage:
                kwargs["workpackage"] = workpackage
            tickets = tracker.get_tickets_by_metadata(**kwargs)
        else:
            tickets = []
            for s in TicketStatus:
                tickets.extend(tracker.get_tickets_by_status(s))

        return templates.TemplateResponse(
            "partials/table_view.html",
            {"request": request, "tickets": tickets},
        )

    @app.post("/partials/ticket/{ticket_id}/approve", response_class=HTMLResponse)
    async def partial_approve(request: Request, ticket_id: str):
        """HTMX: approve ticket.

        If the ticket description contains HITL fields and the request
        includes form data with ``hitl_fields`` values, the description
        is updated before transitioning.
        """
        ticket = tracker.get_ticket(ticket_id)

        # Check for HITL field updates submitted via form
        if has_hitl_fields(ticket.description):
            form = await request.form()
            if form:
                current_values = parse_hitl_fields(ticket.description)
                updated = dict(current_values)
                for key in current_values:
                    form_key = f"hitl_{key}"
                    if form_key in form:
                        raw = form[form_key]
                        updated[key] = _coerce_form_value(raw, current_values.get(key))
                    # Checkboxes: absent means false
                    elif isinstance(current_values.get(key), bool):
                        updated[key] = False

                new_desc = update_hitl_fields(ticket.description, updated)
                tracker.update_description(ticket_id, new_desc)

        tracker.update_status(ticket_id, TicketStatus.APPROVED, changed_by="human")
        ticket = tracker.get_ticket(ticket_id)
        return templates.TemplateResponse(
            "partials/ticket_panel.html",
            {"request": request, "ticket": ticket},
        )

    @app.post("/partials/ticket/{ticket_id}/reject", response_class=HTMLResponse)
    async def partial_reject(request: Request, ticket_id: str):
        """HTMX: reject ticket with comment."""
        form = await request.form()
        comment = form.get("comment", "Rejected")
        tracker.add_comment(ticket_id, str(comment), author="human")
        tracker.update_status(ticket_id, TicketStatus.REJECTED, changed_by="human")
        ticket = tracker.get_ticket(ticket_id)
        return templates.TemplateResponse(
            "partials/ticket_panel.html",
            {"request": request, "ticket": ticket},
        )

    @app.post("/partials/ticket/{ticket_id}/pause", response_class=HTMLResponse)
    async def partial_pause(request: Request, ticket_id: str):
        """HTMX: pause ticket."""
        tracker.update_status(ticket_id, TicketStatus.PAUSED, changed_by="human")
        ticket = tracker.get_ticket(ticket_id)
        return templates.TemplateResponse(
            "partials/ticket_panel.html",
            {"request": request, "ticket": ticket},
        )

    @app.post("/partials/ticket/{ticket_id}/resume", response_class=HTMLResponse)
    async def partial_resume(request: Request, ticket_id: str):
        """HTMX: resume ticket."""
        tracker.update_status(ticket_id, TicketStatus.READY, changed_by="human")
        ticket = tracker.get_ticket(ticket_id)
        return templates.TemplateResponse(
            "partials/ticket_panel.html",
            {"request": request, "ticket": ticket},
        )

    @app.post("/partials/ticket/{ticket_id}/retry", response_class=HTMLResponse)
    async def partial_retry(request: Request, ticket_id: str):
        """HTMX: retry failed ticket."""
        tracker.update_status(ticket_id, TicketStatus.READY, changed_by="human")
        ticket = tracker.get_ticket(ticket_id)
        return templates.TemplateResponse(
            "partials/ticket_panel.html",
            {"request": request, "ticket": ticket},
        )

    @app.post("/partials/ticket/{ticket_id}/comment", response_class=HTMLResponse)
    async def partial_add_comment(request: Request, ticket_id: str):
        """HTMX: add comment."""
        form = await request.form()
        comment = form.get("comment", "")
        if comment:
            tracker.add_comment(ticket_id, str(comment), author="human")
        ticket = tracker.get_ticket(ticket_id)
        return templates.TemplateResponse(
            "partials/ticket_panel.html",
            {"request": request, "ticket": ticket},
        )

    return app
