"""Initialize the daily briefing board.

Run: python init_briefing.py
"""

import subprocess
import sys

# Init the board
subprocess.run([sys.executable, "-m", "conductor.cli", "init", "--reset"], check=True)

# Move to awaiting_review so the Approve button is visible
from conductor.tracker.sqlite_backend import SqliteTracker
from conductor.models.enums import TicketStatus

tracker = SqliteTracker(db_path=".conductor/tracker.db")
tracker.connect({})
tracker.update_status("COND-001", TicketStatus.AWAITING_REVIEW, changed_by="system")

print("✓ Briefing initialized.")
print("  1. Open http://localhost:8080")
print("  2. Click the ticket")
print("  3. Edit the form in the comment box, click 'Add Comment'")
print("  4. Click 'Approve' to start the agents")
