# WhatsApp + GitLab bug tracker

Local bug reporting and tracking for a small team. Customers and testers report bugs through WhatsApp or the HTTP API. The backend validates the reporter, checks the customer account, maps the selected product to a configured GitLab project, and opens one GitLab issue. GitLab remains the source of truth for issue status. Status changes come back through a webhook and are delivered from a notification outbox.

This repository also contains unrelated algorithm notebooks (`linear.ipynb`, `binary.ipynb`, `insert_vs_heap.ipynb`) from earlier work. They are not part of the bug tracker.

## Architecture

The backend is one FastAPI application with module boundaries, not a set of microservices.

```text
WhatsApp or admin UI
        |
        v
backend (FastAPI)
  |-- identity and role checks
  |-- conversation state in PostgreSQL
  |-- product to GitLab project mapping
  |-- GitLab provider (mock or official REST)
  |-- WhatsApp provider (mock or Cloud API)
  |-- notification outbox worker
        |
        +--> postgres
        +--> redis (rate limits)
        +--> local attachment volume
```

Containers on the Compose network:

| Service | Role |
| --- | --- |
| `backend` | API, webhooks, outbox worker, migrations |
| `postgres` | System of record, persistent volume `postgres_data` |
| `redis` | Shared rate-limit window |
| `frontend` | Local admin dashboard on port 8080 |

Services use Compose DNS names (`postgres`, `redis`, `backend`). No container IP addresses are hard-coded.

Roles are enforced in the backend:

| Role | Access |
| --- | --- |
| `CUSTOMER` | Submit bugs and read reports for linked customer accounts |
| `TESTER` | Submit bugs, read assigned customer accounts, record QA results |
| `DEVELOPER` | Read issues for products owned by their teams and add notes |
| `MANAGEMENT` | Read all customer issues, summaries, and management notifications |
| `ADMIN` | Configure customers, products, teams, mappings, and notification rules |

A request cannot choose a GitLab project. The product catalog is the only mapping. Customer identity comes from the authenticated user or the WhatsApp phone mapping, and a supplied customer code is accepted only when that user is already linked to it. Customers never receive another customer's reports, internal notes, or management/developer notification text.

Ordinary WhatsApp groups are not available through the Cloud API. Group destinations are rejected. Notifications are individual chats to authorized phone numbers.

## Local commands

Prerequisites: Docker with Compose v2. Copy the example environment if you want to override the Compose defaults:

```bash
cp .env.example .env
```

`.env` is gitignored. The placeholders are empty on purpose. Compose substitutes local mock secrets when those values are empty, so the mock stack starts without real credentials.

Build and start:

```bash
docker compose up --build -d
```

Check running containers:

```bash
docker compose ps
```

View backend logs:

```bash
docker compose logs -f backend
```

Run database migrations (also runs automatically when the backend container starts):

```bash
docker compose exec backend alembic upgrade head
```

Run automated tests:

```bash
docker compose exec backend pytest -q
```

Stop containers:

```bash
docker compose down
```

Reset the local database and attachment volume. This deletes every local report, user, and uploaded screenshot:

```bash
docker compose down -v
```

The API is at `http://localhost:8000` and the dashboard at `http://localhost:8080`. OpenAPI is at `http://localhost:8000/docs`.

Demo tokens are created only when `SEED_DEMO_DATA=true` (the Compose default). They are local fixtures, not production credentials:

| Token | Role |
| --- | --- |
| `local-admin-token` | ADMIN |
| `local-customer-token` | CUSTOMER for `northwind` |
| `local-customer-2-token` | CUSTOMER for `contoso` |
| `local-tester-token` | TESTER for `northwind` |
| `local-developer-token` | DEVELOPER on the demo product teams |
| `local-management-token` | MANAGEMENT |

Seeded products are Try-On (`try-on`, GitLab project `1001`), Inventory (`1002`), Recommendation Engine (`1003`), and Design Studio (`1004`). Labels are stored on each product. The defaults are `bug`, `whatsapp`, the product slug, and `customer-reported`.

Mock GitLab issue ids are stored in the `mock_state` volume (`GITLAB_MOCK_STATE_PATH=/data/mock/gitlab.json`). Before each create, the backend also skips any numeric issue id already stored on a bug report. Restarting the backend does not open a second report against an existing GitLab issue id. `docker compose down -v` deletes that volume together with the database.

## Sample API calls

Health:

```bash
curl -s http://localhost:8000/health
```

```json
{"status": "ok"}
```

Submit a bug as the Northwind customer:

```bash
curl -s -X POST http://localhost:8000/api/v1/bugs \
  -H 'Authorization: Bearer local-customer-token' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-report-1' \
  -d '{"product_slug":"try-on","description":"Bracelet appears black","severity":"high"}'
```

Expected shape:

```json
{
  "product_slug": "try-on",
  "customer_code": "northwind",
  "severity": "high",
  "status": "opened",
  "source": "api",
  "gitlab_issue_iid": "1",
  "gitlab_issue_url": "https://gitlab.mock/projects/1001/-/issues/1"
}
```

Repeating the same idempotency key returns the same report and does not open another GitLab issue. An unknown product returns HTTP 422. A customer asking for another customer's code returns HTTP 403. Reading another customer's bug returns HTTP 404. Errors use one shape:

```json
{"error": {"code": "not_found", "message": "Bug report not found", "correlation_id": "..."}}
```

## Webhooks

Mock webhook secrets, unless `.env` overrides them:

- WhatsApp app secret: `local-dev-whatsapp-app-secret`
- WhatsApp verify token: `local-dev-verify-token`
- GitLab webhook token: `local-dev-gitlab-webhook-secret`

Verification:

```bash
curl -s "http://localhost:8000/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=local-dev-verify-token&hub.challenge=12345"
```

The response body is `12345`.

Sign a WhatsApp payload with the app secret before posting it. A sample body is in `backend/tests/fixtures/whatsapp_text.json`.

```bash
SIG=$(python - <<'PY'
import hashlib, hmac, pathlib
body = pathlib.Path("backend/tests/fixtures/whatsapp_text.json").read_bytes()
print("sha256=" + hmac.new(b"local-dev-whatsapp-app-secret", body, hashlib.sha256).hexdigest())
PY
)
curl -s -X POST http://localhost:8000/webhooks/whatsapp \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: $SIG" \
  --data-binary @backend/tests/fixtures/whatsapp_text.json
```

Expected response: `{"status":"ok"}`. The same body again returns `{"status":"duplicate"}`. A missing or wrong signature returns HTTP 401.

GitLab issue hook (sample in `backend/tests/fixtures/gitlab_issue_close.json`):

```bash
curl -s -X POST http://localhost:8000/webhooks/gitlab \
  -H 'Content-Type: application/json' \
  -H 'X-Gitlab-Token: local-dev-gitlab-webhook-secret' \
  -H 'X-Gitlab-Event-UUID: demo-close-1' \
  --data-binary @backend/tests/fixtures/gitlab_issue_close.json
```

A wrong token returns HTTP 401. Invalid JSON returns HTTP 422. The hook updates the linked report; it never creates a new GitLab issue. Notification delivery failure is retried from the outbox and also does not create another issue.

WhatsApp conversation states are `IDLE`, `AWAITING_PRODUCT`, `AWAITING_IMAGE`, `AWAITING_DESCRIPTION`, `AWAITING_SEVERITY`, `AWAITING_CONFIRMATION`, `SUBMITTING`, `COMPLETED`, and `FAILED`. The GitLab issue is created only after validation and an explicit yes. `cancel` stops the report. `retry` resumes a failed submission with the same idempotency key. Inactive conversations expire.

## Tests

`docker compose exec backend pytest -q` uses PostgreSQL and the mock GitLab and WhatsApp providers. It does not need production credentials. Coverage includes submission, invalid products, tenant isolation, attachment checks, duplicate webhooks, status sync, notification routing, retry, cancellation, resumption, and concurrent conversations.

`tests/integration/test_real_integrations.py` is marked `real_integration` and is skipped unless you opt in:

```bash
docker compose exec \
  -e RUN_REAL_INTEGRATIONS=1 \
  -e GITLAB_PROVIDER=rest \
  -e GITLAB_BASE_URL=https://gitlab.example \
  -e GITLAB_ACCESS_TOKEN=your-dev-token \
  -e GITLAB_PROJECT_ID=123 \
  backend pytest -q tests/integration/test_real_integrations.py
```

That test creates one issue in the development project you name and then calls create again with the same marker to confirm the client reuses it. Do not point it at a production project.

## Real GitLab and WhatsApp development integrations

Keep `APP_ENV=local`. Set these in `.env` and recreate the backend container. Compose passes them into the container; the database URL stays on the `postgres` service.

```text
WHATSAPP_PROVIDER=cloud_api
WHATSAPP_ACCESS_TOKEN=...
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_BUSINESS_ACCOUNT_ID=...
WHATSAPP_APP_SECRET=...
WHATSAPP_VERIFY_TOKEN=...
WHATSAPP_API_VERSION=v21.0

GITLAB_PROVIDER=rest
GITLAB_BASE_URL=https://gitlab.example
GITLAB_ACCESS_TOKEN=...
GITLAB_WEBHOOK_SECRET=...
```

Startup fails closed when the selected provider is missing a required value. `gitlab_project_id` on a product must be the numeric project id that GitLab webhooks send. `gitlab_project_path` is the human-readable path. Point the GitLab project webhook at `http://<your-tunnel-or-local-forward>/webhooks/gitlab` with the same secret token, and the WhatsApp callback at `/webhooks/whatsapp`. Those forwards are outside this repository; the app itself only listens on the Compose network and the published local ports.

Screenshots are stored on the `attachments` volume, not in a database bytea column. Temporary files that were never attached to a report are deleted after `ATTACHMENT_RETENTION_HOURS` (default 24).

Mock mode remains the default and is what the test suite exercises. A successful mock run does not mean a real GitLab or WhatsApp tenant was called.
