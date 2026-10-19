# AI HUB security model

## Scope and identity

This release is a private, single-owner application. HTTP Basic authentication uses `APP_USERNAME` and `APP_PASSWORD`, compared in constant time. The public UI contains no report data before authentication. Every `/v1/*` API route requires owner authentication, including report downloads, usage, system configuration flags, and schedule management.

Use HTTPS for remote access. `/health` is a public liveness endpoint; it does not call OpenAI or Tavily or prove that credentials are valid. `/`, static browser assets, and `/health` are intentionally public. Interactive API documentation is disabled by default. When `ENABLE_API_DOCS=true`, `/docs` and the schema are public; enable this for local development only.

The browser keeps owner authentication in memory for the active session. Provider API keys remain on the server. This owner-password design does not provide multi-user accounts, per-user roles, MFA, or individual session revocation. Do not expose it as a multi-tenant service without redesigning identity and authorization.

## Permissions and outside content

`config/permissions.json` defines the per-agent action and tool allowlists. Missing permission entries deny access. Model and search calls must pass the relevant runtime capability checks. Skill text and retrieved articles are untrusted instructions with respect to authorization: neither can grant access to another tool or expand these allowlists.

The runtime sends requests only to the configured provider integration endpoints. News citations are restricted to retrieved source identifiers and checked URLs. Source text is bounded and treated as evidence. Reports can still contain interpretation errors and require editorial review. The WhatsApp assistant prepares drafts; this release does not send messages, emails, meeting invitations, or publish generated reports to third parties.

## Secrets and stored data

Real credentials belong only in a private `.env` file or the Render service environment. `.env.example` deliberately contains empty credential fields. Never put credentials into repository URLs, code, workflow YAML, screenshots, report templates, or support messages. GitHub Actions uses synthetic test credentials and needs no production API keys.

Tasks, report content, schedules, and accounting records are sensitive operational data. SQLite stores them at `AI_HUB_DATABASE_PATH`. On Render, the database and its WAL sidecar files must all be under `/var/data/ai-hub` on the attached persistent disk. The archive builder excludes runtime data and only collects known source locations and file types. Review commits and archives before sharing them; a source packager cannot identify every possible secret pasted into ordinary source text.

Use the SQLite-native backup script and move backups to a private destination off the service disk. Backups contain the same sensitive content as the database. Keep owner-only access to both live data and backups.

## Usage controls and billing limits

Accepted runs reserve their estimated provider exposure before search or generation. Atomic SQLite transactions enforce a global daily attempt cap of 50 and daily/monthly estimated budgets. Manual and scheduled requests share the same controls. A successfully replayed idempotency key does not create a second provider call. A key cannot be reused for different task content.

Pending and ambiguous failed runs retain their full reservation. Stale pending runs are recorded as interrupted instead of being silently retried. This deliberately conservative accounting may overstate the invoice. Prices are model-bound; changing a model requires matching rate configuration. Quotas and costs are persistent only when the database is persistent.

These controls govern requests made through this application. They do not cap charges incurred by other uses of the same API keys, hosting fees, taxes, account-specific service pricing, or manual provider actions. Provider billing records remain the source for invoiced cost.

## Scheduler and deployment boundary

Run one Uvicorn process with `--workers 1`, one Render instance, and one attached disk. The in-process scheduler starts only when `SCHEDULER_ENABLED` is enabled, and each schedule also needs its own enabled flag. Atomic occurrence claims and stable idempotency keys prevent a restart from automatically resending an already claimed occurrence. The scheduler skips old missed occurrences rather than generating an uncontrolled catch-up burst.

This architecture does not provide horizontal scaling or guaranteed uninterrupted scheduled delivery. Render's disk-backed services have a brief interruption on deploy; the disk is not shared with other services and is not mounted in builds or pre-deploy commands. See the [official disk documentation](https://render.com/docs/disks).
