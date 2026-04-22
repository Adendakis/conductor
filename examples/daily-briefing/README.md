# Daily Briefing

Demonstrates **dynamic fan-out/fan-in** — a pattern where the number of
parallel agents is determined at runtime by human input.

## What This Shows

- **Human input form** — user specifies city and selects topics via checkboxes
- **Dynamic ticket creation** — dispatcher agent creates 1-5 topic tickets at runtime
- **Dynamic dependencies** — reconciliation ticket is blocked by all topic tickets
- **Parallel execution** — topic agents run concurrently
- **Fan-in reconciliation** — merge agent combines all results when all topics complete

## Run the Demo

### Prerequisites

```bash
# Install extra dependencies for this example
pip install -r examples/daily-briefing/requirements.txt

# Configure AWS credentials (for Bedrock LLM calls)
export AWS_REGION=us-east-1
# Optionally override the model:
# export BRIEFING_MODEL_ID=anthropic.claude-haiku-3-20250310
```

### Terminal 1: Initialize and start dashboard
```bash
cd examples/daily-briefing
conductor init --reset
conductor serve --port 8080
```

### Terminal 2: Start watcher
```bash
cd examples/daily-briefing
conductor watch-async
```

### On the dashboard (http://localhost:8080):

1. **Click the "Configure your daily briefing" ticket** (READY status)

2. **Add a comment** with your preferences:
   ```
   city: Berlin, Germany
   [x] weather
   [x] local_news
   [ ] global_news
   [x] sports
   [ ] finance
   ```

3. **Click "✓ Approve"** — this completes the setup phase

4. **Watch the dispatcher** create topic tickets (weather, local_news, sports)
   and a reconciliation ticket

5. **Topic agents run in parallel** — you'll see 3 tickets IN_PROGRESS

6. **When all topics complete**, the reconciliation ticket unblocks automatically

7. **Approve the final briefing** — check `output/daily_briefing.md`

## Architecture

```
Human fills form → Approve
                      ↓
              Dispatcher agent
              reads preferences
                      ↓
         ┌────────┬────────┬────────┐
         ↓        ↓        ↓        ↓
      Weather  Local    Sports   Finance    (0-5 agents, dynamic)
         ↓        ↓        ↓        ↓
         └────────┴────────┴────────┘
                      ↓
              Reconciler agent          (blocked_by all topics)
              merges results
                      ↓
              Daily Briefing ✅
```

## Key Design Pattern

The dispatcher agent uses `context.tracker.create_ticket()` to create
tickets at runtime. This enables:

- **Variable fan-out** — number of parallel agents depends on user input
- **Dynamic dependencies** — reconciliation ticket's `blocked_by` list
  is built at runtime, not defined in pipeline.yaml
- **Self-organizing workflow** — the pipeline YAML only defines the
  setup and dispatch phases; everything else is created dynamically

## AI Integration

Each topic agent calls AWS Bedrock (Claude) via a shared `llm_helper.py`.
The LLM generates realistic content based on the city and topic.
The reconciler uses the LLM to merge all topic outputs into a polished briefing.

Dependencies are in `requirements.txt` — only this example needs them,
not the conductor framework itself.

To use a cheaper/faster model: `export BRIEFING_MODEL_ID=anthropic.claude-haiku-3-20250310`
