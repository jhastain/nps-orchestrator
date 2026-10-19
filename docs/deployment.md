# Deployment

Target: **Vertex AI Agent Engine** (`reasoningEngines`) in `us-central1`, fronted by a chat app that
end users talk to. The two observability MCP servers run as separate Cloud Run services.

Project IDs, warehouse IDs and hostnames in this repo are placeholders — substitute your own.

## Prerequisites

```bash
gcloud services enable aiplatform.googleapis.com secretmanager.googleapis.com run.googleapis.com
```

Secrets the agent reads at runtime (values never enter the repo or Terraform state):

```bash
gcloud secrets describe databricks-client-id     --project=$GOOGLE_CLOUD_PROJECT
gcloud secrets describe databricks-client-secret --project=$GOOGLE_CLOUD_PROJECT
gcloud secrets describe sentry-auth-token        --project=$GOOGLE_CLOUD_PROJECT
```

The agent's service account needs **Secret Manager Secret Accessor** and **Vertex AI User**.

Both MCP servers must be up:

```bash
gcloud run services describe datadog-mcp-server --region us-central1
gcloud run services describe sentry-mcp-server  --region us-central1
```

A staging bucket `gs://$GOOGLE_CLOUD_PROJECT-agent-staging` must exist — Agent Engine packages the
source through it.

## Deploy

```bash
export SENTRY_AUTH_TOKEN=...     # from the env; never hardcode it in deploy.py
python deploy.py
```

`deploy.py` wraps `root_agent` in `vertexai.agent_engines.AdkApp`, then calls
`client.agent_engines.create()` with an inline `requirements` list, `env_vars`, the staging bucket,
and `extra_packages: ["./app"]` so the local package ships with the deployment. It prints the new
reasoning-engine resource name — capture it, you need it to attach the engine.

Attaching the engine to the front-end chat app is a **manual console step**. There is no API for it.

## Updating and rolling back

Agent Engine does not update in place. Every deploy creates a **new** reasoning engine with a new ID,
which has two consequences:

- **Rollback = redeploy an older commit**, then repoint the chat app at the older resource name.
- Engines accumulate. `cleanup_agents.py` lists them, keeps the newest, confirms interactively, and
  deletes the rest.

```bash
gcloud ai reasoning-engines list --region=us-central1 --project=$GOOGLE_CLOUD_PROJECT
python cleanup_agents.py
```

## Troubleshooting

| Symptom | Cause / check |
| --- | --- |
| `Module not found` during deploy | A dependency is missing from the `requirements` list in `deploy.py`. Agent Engine builds its remote venv from that list, not from the local environment. |
| Deploy succeeds, every tool fails | `env_vars` did not reach the engine. Check the Agent Engine logs in Cloud Logging. |
| MCP calls fail | `gcloud run services list --region us-central1`, then curl each server's `/health`. Confirm the URLs compiled into `app/agent_direct.py`. |
| Databricks auth fails | `gcloud secrets versions access latest --secret=databricks-client-id` — then confirm the agent's SA holds Secret Manager Secret Accessor. |
| `cloudpickle` error on deploy | You are deploying the `McpToolset` agent (`app/agent.py`). Deploy `app/agent_direct.py`; see the README. |
| Query hangs then times out | The warehouse was asleep. First query pays the cold-start; the client polls for up to 300s. |

## Monitoring

Agent Engine request and error logs land in Cloud Logging under the reasoning-engine resource. The
two MCP servers log independently as Cloud Run services. `app/agent_engine_app.py` adds Cloud Logging
setup and a `register_feedback` operation if you wire it in as the deployed app class.
