# Sherrif backend

Sherrif diagnoses problems field officers (FOs) report from the FieldSmart Android app. The officer picks an issue from the
report drop-down (or types one), the app sends it with the tablet's state, and Sherrif works out where things went
wrong: on the tablet, in the officer's account, in Sync Gateway, in Couchbase, or upstream in Fineract. It answers
the officer in plain words with what to do next (restart, log in again, raise a service desk request), and files a
Jira bug when developers need to look.

## What it diagnoses

| Tool | Officer reports | What it checks |
|---|---|---|
| `feature_is_disabled` | "I can't see the add client button" | The feature's switch in the tablet's copy of Firebase Remote Config against the live one. Off in both: service desk request to enable it. On live but off on the tablet: restart. On in both: escalate. |
| `record_is_missing` | "I can't find client Daniel" | Searches wider until the record turns up (Couchbase in the officer's office → Couchbase in the whole country → Fineract office → Fineract country). The officer picks the record, and it's traced back: Sync Gateway import → office → channel → device revision → app. |
| `data_no_syncing` | "My data is not syncing" | Tablet storage → Sync Gateway public URL → Sync Gateway database → the officer's Sync Gateway user (exists, enabled, password, office channel) → channel has documents → Couchbase indexes → cluster health → how fresh the office's data is. |

The app checks internet and the tablet clock itself before it reports anything, since without them it can't reach
Sherrif at all.

## Architecture

```
FieldSmart app
   │  POST /start-session  (report + tablet state + configs)        ◄── SSE: progress lines, then the answer
   ▼
FastAPI (main.py)
   │  builds AgentContext: the request + live Firebase Remote Config + formulas
   ▼
OllamaLLM (src/llm)  ── qwen3:8b on a local Ollama ──  picks a tool and its arguments
   │  call_tool                                     (the request and configs are injected; the model never sees them)
   ▼
MCP server (src/mcp_interface/agent_server.py, started once as a subprocess over stdio)
   │  feature_is_disabled · record_is_missing · data_no_syncing
   ▼
Diagnosis code (src/utils)  ──►  integrations (src/integrations)
                                   cb.py          Couchbase SDK: documents, _sync xattrs, N1QL
                                   cb_admin.py    Couchbase management REST: nodes, rebalance, quotas
                                   sync_gateway.py  Sync Gateway admin API + public URL
                                   fineract.py    Fineract REST (Keycloak token when configured)
                                   firebase.py    Remote Config
                                   (src/utils/jira.py: Jira bugs and service desk requests)
```

- **Only the first step uses the model.** `/start-session` lets the model choose a tool. Every later step is a
  plain endpoint keyed by `chat_id`: the officer picking a record, "none of these", requesting a service, feedback.
- **Answers stream.** `/start-session` and `/record-search/next` return Server-Sent Events (`src/utils/sse.py`):
  `progress` {"message"} while each thing is checked ("Checking Fineract for your office"), then one `result`
  (`OutgoingResponsePayload`) or `error` {"detail"}. Input is validated before streaming starts, so bad requests
  still get a normal 4xx.
- **Every diagnosis has the same shape** (`MCPToolDiagnosisResponse`): an officer-facing `message`,
  `recommended_actions` with their notes, a `jira_ticket_title`, the `stage` where the problem is (`DataStage`),
  and for record searches the `records` found and the `search_stage`.
- **Escalation is automatic.** Any answer with `ESCALALTE_TO_DEVELOPER` files a Jira bug in the background, with the
  request and evidence attached.
- **Chats are stored** in Postgres (`DATABASE_URL`, SQLModel `Issue` table, created on startup): the incoming report, then `updates`
  (search stage and records, pick, diagnosis, feedback) and `issue_status`
  (processing → completed / unsolved / failed).

### The record search

`DOCUMENT_TYPES` in `src/enums/actions.py` defines each document the officer can search for (Client, Group, Loan,
Repayment, ClientOrder): its Couchbase `type`, searchable fields (exact or partial match), the fields shown, and
its source system. `JOINS` links documents searched through the client (a repayment by its client's name, say).
Couchbase ids are `{type}_{officeId}_{fineractId}`.

A stage that finds nothing is skipped without asking the officer. The officer only sees a stage that found records,
or the final "couldn't find it anywhere". Orders come from Odoo, which isn't searched yet: the search stops there and
says so.

## Endpoints

| Endpoint | Does |
|---|---|
| `POST /start-session` | New report. SSE. The model picks a tool; an empty office search keeps widening. |
| `POST /record-search/next` | "None of these": continue the search from the next stage. SSE. |
| `POST /record-search/select` | The officer picked a record (with the device's copy of it): trace where it stops. |
| `POST /enable-feature-service-request` | Raise the service desk request an answer suggested; returns the ticket key and URL. |
| `POST /chat-feedback` | "That solved it" (completed) or "I still need help" (unsolved). 204. |

## Running locally

Needs [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com) with the model pulled (`ollama pull qwen3:8b`), and:

- Couchbase Server and Sync Gateway: the docker compose in `reg-couchbase-sync-gateway/local-docker`.
- Postgres with an empty database for the chats (`createdb sherrif`); tables are created on startup.
- Fineract: `fs/mock_apis` serves the same API locally (leave `FINERACT_TOKEN_URL` empty, no login).
- `src/integrations/firebase-service-account.json`: a Firebase service account for Remote Config.

```bash
uv run fastapi dev        # http://localhost:8000
```

The MCP server is started by FastAPI on startup; there's nothing to run separately.

### Environment (`.env`)

Copy `.sample.env` to `.env` and fill it in (`.env` is git-ignored).

| Variables | For |
|---|---|
| `DATABASE_URL` | Postgres for the chats, e.g. `postgresql+psycopg://user:password@localhost:5432/sherrif` |
| `CB_URL`, `CB_USERNAME`, `CB_PASSWORD`, `CB_READS_BUCKET_NAME` | Couchbase SDK |
| `CB_MANAGEMENT_URL` | Couchbase management REST (port 8091) |
| `SG_PUBLIC_URL` | The Sync Gateway URL devices sync through (port 4984) |
| `SG_ADMIN_URL`, `SG_READS_DB`, `SG_ADMIN_USERNAME`, `SG_ADMIN_PASSWORD` | Sync Gateway admin API (port 4985). Credentials are a Couchbase Server user with a Sync Gateway role; empty where admin auth is off |
| `FINERACT_BASE_URL`, `FINERACT_TENANT_ID`, `FINERACT_TOKEN_URL`, `FINERACT_CLIENT_ID`, `FINERACT_USERNAME`, `FINERACT_PASSWORD` | Fineract API root (`…/fineract-provider/api/v1`) and its Keycloak login |
| `JIRA_URL`, `JIRA_EMAIL`, `JIRA_TOKEN`, `JIRA_PROJECT` | Jira bugs |
| `JIRA_SERVICE_DESK_URL`, `JIRA_SERVICE_DESK_ID`, `JIRA_SERVICE_REQUEST_TYPE_ID` | Service desk requests |

### Testing

- `test-runs/record-search-tests.html`: the record search test cases, with pass/fail and comments saved in the
  browser.
- `test-runs/simulate-cluster.sh`: adds, rebalances, pauses and removes a second Couchbase node so the cluster check
  in `data_no_syncing` can be seen firing. Read the warning at its top first: adding a node renames the local node
  for good.

## Adding a tool

1. Write the diagnosis in `src/utils`, returning `MCPToolDiagnosisResponse`s.
2. Register it in `src/mcp_interface/agent_server.py` with `@app.tool()`. Its docstring is what the model reads to
   choose it, so give example reports. Take `request: IncomingRequestPayload` (and `server_config` /
   `formula_config` if needed): these are injected, never filled by the model.
3. Add a progress line for it to `TOOL_PROGRESS` in `main.py`.
4. If the officer should be able to pick it, add an option to the app's report drop-down.
