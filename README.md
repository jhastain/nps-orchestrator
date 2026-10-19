# NPS Orchestrator

A single-agent LLM service, built on [Google ADK](https://google.github.io/adk-docs/) and deployed to
Vertex AI Agent Engine, that answers "why is customer satisfaction dropping?" by correlating three
systems a human would otherwise have to join by hand:

| Source | What it holds | How the agent reaches it |
| --- | --- | --- |
| Databricks SQL Warehouse | NPS survey responses (score, comment, account, timestamp) | Hand-written REST client over the SQL Statement Execution API |
| Sentry | Application errors, releases, traces | Model Context Protocol server + direct REST for search |
| Datadog | Monitors, incidents, metrics, logs, hosts | Model Context Protocol server |

Ask it *"which accounts scored us 0-6 last month, and what errors did those same accounts hit?"* and it
pulls the survey rows, extracts the account IDs, and finds the matching errors in one pass.

> This is a portfolio copy of a system I built at work. All employer identifiers, project IDs,
> hostnames, table names and credentials have been replaced with neutral placeholders, so it will not
> run as-is — the architecture, the integration code and the engineering decisions are intact.

## Architecture

```
                   ┌──────────────────────────────┐
  natural language │   Vertex AI Agent Engine     │
  question ───────▶│   root_agent (Gemini)        │
                   │   22 FunctionTools, flat     │
                   │   ReAct loop, no sub-agents  │
                   └───┬──────────┬───────────┬───┘
                       │          │           │
          ┌────────────▼──┐  ┌────▼───────┐  ┌▼──────────────┐
          │ Databricks    │  │ Sentry     │  │ Datadog       │
          │ SQL Warehouse │  │ MCP + REST │  │ MCP           │
          │ (OAuth, creds │  │ (Cloud Run)│  │ (Cloud Run)   │
          │  from Secret  │  └────────────┘  └───────────────┘
          │  Manager)     │
          └───────────────┘
```

Full diagram: [`docs/nps-orchestrator.drawio`](docs/nps-orchestrator.drawio).

All coordination lives in the system prompt — there is no graph, no router, no sub-agent tree. The
model picks tools from a flat list. That is a deliberate choice and it has a cost, documented in
[`docs/sentry-search.md`](docs/sentry-search.md).

## Layout

| Path | Role |
| --- | --- |
| `app/agent_direct.py` | **The deployed agent.** Hand-rolled MCP client, 22 tools, the system prompt. |
| `app/agent.py` | Local-dev agent. Uses ADK's `McpToolset` for tool auto-discovery. Not deployable — see below. |
| `app/databricks_client.py` | Databricks SQL REST client: OAuth, token caching, submit/poll, parameter binding. |
| `app/databricks_tools.py` | The three ADK-facing NPS tools over that client. |
| `app/agent_engine_app.py` | `AdkApp` subclass adding Cloud Logging, GCS artifacts and a feedback operation. |
| `deploy.py` | Deploys `root_agent` to Agent Engine. |
| `cleanup_agents.py` | Deletes superseded reasoning engines (Agent Engine has no in-place update). |
| `bin/e2e.py` | One-command check; see [Verifying](#verifying). |

## Three engineering decisions worth the read

**1. The deployed agent re-implements the MCP client by hand.** ADK's `McpToolset` holds an open
`TextIOWrapper`, and Agent Engine deploys by `cloudpickle`-ing the agent object — an open file handle
is not picklable, so a `McpToolset`-based agent cannot deploy. `app/agent_direct.py:85-146` does the
JSON-RPC 2.0 `initialize` handshake, the `notifications/initialized` notification and `tools/call`
over plain `requests`, caching the session in a module-level dict. Each remote MCP tool then becomes
a thin typed Python wrapper registered as a `FunctionTool`. `app/agent.py` keeps the `McpToolset`
version because tool auto-discovery is genuinely better for local iteration under `adk web`.

**2. Sentry's MCP search is bypassed.** The MCP server's `search_issues` / `search_events` route
natural-language queries through a third-party model and returned 401 in this environment. Both are
commented out of the tool list and the prompt explicitly forbids them. The replacement,
`search_sentry_errors`, composes a Sentry issue-search query string from up to 24 inputs — account and
user identity, Datadog trace/span correlation tags, Sentry distributed-trace IDs, time window,
environment, release, level, status, exception type, URL, server name, arbitrary custom tags — and
calls the REST API directly.

**3. The model is not trusted with dates or SQL.**
- A `get_current_date` tool exists because the model does not know what "today" is; the prompt
  mandates calling it first for any relative-time question and doing the arithmetic itself.
- Every value that reaches SQL is bound as a named parameter (`:account_id`), never interpolated.
  Tool arguments come from an LLM, which makes them untrusted input. `LIMIT` cannot be bound, so it
  goes through `clamp_row_limit()` instead.
- Every tool returns a JSON string and never raises. Failures serialize as
  `{"success": false, "error": ..., "error_type": ...}` so the model can read the failure and react,
  rather than the turn dying.

## Running locally

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in your own project, warehouse and tokens
adk web app/                  # then open http://localhost:8000
```

`adk web` loads `app/agent.py`. You need application-default credentials for a GCP project with
Vertex AI and Secret Manager enabled, plus a reachable Databricks warehouse — see
[`docs/deployment.md`](docs/deployment.md).

## Verifying

```bash
python bin/e2e.py
```

Runs everything that can be exercised without cloud credentials — module imports, the ADK agent
contract (`root_agent` exists, is named correctly, has tools), SQL construction and parameter
binding, `clamp_row_limit` bounds, the MCP JSON-RPC envelope shape, and the batch-query composer —
and prints an explicit `SKIP` line for each path that needs real credentials rather than silently
omitting it. Exit code is non-zero on any failure.

## Deploying

```bash
export SENTRY_AUTH_TOKEN=...      # read from the env, never committed
python deploy.py
```

Agent Engine has no in-place update: each deploy mints a new reasoning-engine ID, so rollback means
redeploying an older commit and `cleanup_agents.py` exists to remove the superseded ones. Attaching a
deployed engine to a front-end chat app is a manual console step. Details and the troubleshooting
notes are in [`docs/deployment.md`](docs/deployment.md).

## Documentation

- [`docs/deployment.md`](docs/deployment.md) — prerequisites, deploy, rollback, troubleshooting.
- [`docs/sentry-search.md`](docs/sentry-search.md) — why the custom search exists, and the batch fix
  for the N-sequential-tool-calls problem.
- [`docs/nps-orchestrator.drawio`](docs/nps-orchestrator.drawio) — component and data-flow diagram.

## License

MIT — see [LICENSE](LICENSE) — **except** three files generated from Google's ADK
project starter, which keep their original Apache-2.0 header. [NOTICE](NOTICE) lists them.
