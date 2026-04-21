# Code Migration Demo

Demonstrates conductor's orchestration features with a simulated code migration.

## What This Shows

- **Progressive ticket creation** — Phase 2 tickets appear after Phase 1 completes
- **Parallel workpackage execution** — 3 WPs run concurrently in Phase 3
- **Human-in-the-loop gates** — approve/reject on the dashboard
- **Reviewer pattern** — automated review step before human approval
- **Auto-approve** — some steps skip human review
- **Auto-refreshing dashboard** — board updates every 5 seconds

## Run the Demo (5 minutes)

Open **three terminals**:

### Terminal 1: Initialize
```bash
cd examples/code-migration
conductor init
```

### Terminal 2: Dashboard
```bash
cd examples/code-migration
conductor serve --port 8080
```
Open http://localhost:8080

### Terminal 3: Watcher
```bash
cd examples/code-migration
conductor watch-async
```

## Demo Flow

1. **Phase 1** starts automatically (2 tickets: DB analysis + code analysis)
   - DB analysis auto-approves (no HITL)
   - Code analysis → AWAITING_REVIEW → **approve on dashboard**

2. **Phase 2** tickets appear (workpackage planning)
   - Planner creates 3 workpackages (Users, Posts, Comments)
   - → AWAITING_REVIEW → **approve on dashboard**

3. **Phase 3** tickets appear (6 tickets: 2 steps × 3 WPs)
   - Logic extraction runs in parallel for all 3 WPs
   - Reviews auto-complete → AWAITING_REVIEW
   - **Approve all 3** (or reject one to see rework)

4. **Phase 4** appears (final report)
   - **Approve** → all DONE

## To Demonstrate Rejection

When a Phase 3 review reaches AWAITING_REVIEW:
1. Click the ticket on the dashboard
2. Click "✗ Reject"
3. Type feedback: "Missing error handling for edge cases"
4. Submit — watch the watcher re-run the specialist with feedback
5. Approve the reworked version
