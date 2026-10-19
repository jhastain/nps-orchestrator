# Sentry search: why it is custom, and the batch fix

Two problems, in order. The first forced a custom search tool; the second forced a batch version of it.

## Problem 1 — the MCP server's search was unusable

The Sentry MCP server exposes `search_issues` and `search_events`. Both route the user's
natural-language query through a third-party model to translate it into Sentry query syntax. In this
environment that path returned `401 Unauthorized`, so both tools are commented out of the registry
(`app/agent_direct.py:1054-1055`) and the system prompt explicitly forbids them.

Everything else from the MCP server is still used — `find_organizations`, `find_projects`,
`find_releases`, `get_issue_details`, `update_issue`, `get_trace_details`. Only search is replaced.

### The replacement

`search_sentry_errors` (`app/agent_direct.py:354-628`) composes a Sentry issue-search query string
from typed arguments and calls `GET /api/0/organizations/{org}/issues/` directly — or the
project-scoped path when a project is given. It accepts:

| Group | Fields |
| --- | --- |
| Identity | `user_id`, `username`, `user_email`, `user_ip`, `account_id`, `account_name` |
| Correlation | `datadog_trace_id`, `datadog_span_id`, `sentry_trace_id`, `sentry_span_id` |
| Scope | `organization_slug`, `project_slug`, `environment`, `release`, `server_name` |
| Filters | `error_level`, `status`, `error_type`, `url_path`, `custom_tags`, `query` |
| Window | `start_date`, `end_date`, `stats_period`, `limit`, `sort` |

`stats_period` is used only when no explicit `start_date`/`end_date` is supplied — Sentry rejects both at once.

Composing the query string in Python rather than asking the model to write Sentry syntax is the point:
the model supplies values, the code supplies grammar.

## Problem 2 — N sequential tool calls

Asked to correlate 10 survey responses with errors, the model called the single-ID search once per
response and waited for each before starting the next. The observed run completed **3 of 10 searches
in 12 hours**.

The Sentry API was not the bottleneck — each call took 2-5 seconds. The cost was the agent loop
around it: model reasoning time between calls, response processing, and retry behaviour, repeated ten
times.

### The fix has two halves, and the prompt half matters as much as the code

**Code.** `batch_search_sentry_errors` (`app/agent_direct.py:631-847`) takes lists of IDs and
collapses N lookups into **one** HTTP request by composing a single Sentry `OR` query:

```
account-id:123 OR account-id:456 OR user.id:789
```

Sentry returns one flat list of issues. The function then regroups them client-side by matching each
returned issue's tag dict back to the requested IDs, and returns a `summary` (IDs searched, errors
found, measured `search_time_seconds`, the query used) alongside both the grouped and flat views.

**Prompt.** Code alone does not fix it — the model has to *choose* the batch tool. The system
instruction carries an explicit block ordering it to collect all IDs first, call the batch tool once,
and specifically **not** loop (`app/agent_direct.py:962-976`). Without that, the model kept reaching
for the familiar single-ID tool.

### What the fix is actually worth

The defensible claim is a change in shape, not a benchmark:

- **Before:** N agent tool-calls, each paying full model-reasoning overhead. Observed: 3 of 10 in 12 hours.
- **After:** 1 agent tool-call, 1 HTTP request. Measured ~12-15 seconds for 10 IDs.

That is the number to trust. The original internal write-up of this fix quoted speedups ranging from
2,880x to 12,000x, which were extrapolations from the worst observed run rather than measurements,
and it also described a `ThreadPoolExecutor` and an ID cap that were never implemented. They are not
repeated here.

## Known limitations

- The batch query is one serial `requests.get`. There is no parallelism and no chunking, so a very
  large ID list will eventually produce a query string Sentry rejects. A cap and chunk-loop is the
  obvious next change.
- Grouping relies on the requested ID appearing in the returned issue's tags. An issue that matched
  on a different field lands in the flat view only.
- `search_sentry_by_account_user` remains registered as a deprecated wrapper for compatibility. New
  work should use the batch tool, or `search_sentry_errors` for a genuine single-ID lookup.
