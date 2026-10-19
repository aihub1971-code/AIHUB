# AI HUB security model

## Scope and identity

This release is a private, single-owner application. HTTP Basic authentication uses `APP_USERNAME` and `APP_PASSWORD`, compared in constant time. The public UI contains no report data before authentication. Every `/v1/*` API route requires owner authentication, including report downloads, usage, system configuration flags, and schedule management.

Use HTTPS for remote access. `/health` is a public liveness endpoint; it does not call OpenAI or prove that the API key or web-search access is valid. `/`, static browser assets, and `/health` are intentionally public. Interactive API documentation is disabled by default. When `ENABLE_API_DOCS=true`, `/docs` and the schema are public; enable this for local development only.

The browser keeps owner authentication in memory for the active session. The OpenAI API key remains on the server and is used for both generation and native web search. This owner-password design does not provide multi-user accounts, per-user roles, MFA, or individual session revocation. Do not expose it as a multi-tenant service without redesigning identity and authorization.

## Permissions and outside content

`config/permissions.json` defines the per-agent action and tool allowlists. Missing permission entries deny access. Model and search calls must pass the relevant runtime capability checks. Skill text and retrieved articles are untrusted instructions with respect to authorization: neither can grant access to another tool or expand these allowlists.

The application uses OpenAI Responses API for both research and generation. Native `web_search` is the permitted research tool; application requests carry a tool-call limit, and the final structured-report request has no search tools. The same `OPENAI_API_KEY` authenticates both stages.

Every agent that uses native search is restricted to `ft.com`, `theatlantic.com`, `politico.com`, `politico.eu`, `wsj.com`, `telegraph.co.uk`, `aljazeera.com`, and `aljazeera.net`, including their subdomains. The native `allowed_domains` filter and the server source-URL gate enforce this publisher policy. Source shortages do not expand the allowlist. Citation URLs must also appear in the native search source metadata. Research notes and dates extracted by the model are not independently downloaded publisher text or verified verbatim quotations. The runtime bounds this material and treats it as evidence, not authorization. Paywalled material is usable only to the extent evidence is actually available to the search tool; a citation does not prove full-article access. Not every approved publisher is guaranteed to be represented. Reports can still contain interpretation errors and require editorial review. See the [OpenAI web-search source metadata documentation](https://developers.openai.com/api/docs/guides/tools-web-search). The WhatsApp assistant prepares drafts; this release does not send messages, emails, meeting invitations, or publish generated reports to third parties.

## Secrets and stored data

The required private values are `APP_USERNAME`, `APP_PASSWORD`, and `OPENAI_API_KEY`. Tasks, selected skill context, and research material are sent to OpenAI when the owner runs a task. Real credentials belong only in a private `.env` file or the Render service environment. `.env.example` deliberately contains empty credential fields. Never put credentials into repository URLs, code, workflow YAML, screenshots, report templates, or support messages. GitHub Actions uses synthetic test credentials and needs no production API keys.

Tasks, report content, schedules, and accounting records are sensitive operational data. SQLite stores them at `AI_HUB_DATABASE_PATH`. On Render, the database and its WAL sidecar files must all be under `/var/data/ai-hub` on the attached persistent disk. The archive builder excludes runtime data and only collects known source locations and file types. Review commits and archives before sharing them; a source packager cannot identify every possible secret pasted into ordinary source text.

Use the SQLite-native backup script and move backups to a private destination off the service disk. Backups contain the same sensitive content as the database. Keep owner-only access to both live data and backups.

## Usage controls and billing limits

Accepted runs reserve their estimated OpenAI exposure before search or generation. The reservation covers both Responses stages when research is needed, the native search tool fee, and a conservative allowance for search content tokens. A search-tool fee alone does not cover model input/output usage. Atomic SQLite transactions enforce a global daily attempt cap of 50 and daily/monthly estimated budgets. Manual and scheduled requests share the same controls. A successfully replayed idempotency key does not create a second provider call. A key cannot be reused for different task content.

Pending and ambiguous failed runs retain their full reservation. Stale pending runs are recorded as interrupted instead of being silently retried. This deliberately conservative accounting may overstate the invoice. Model prices are model-bound; changing a model requires matching rate configuration and support for the required tools. Native search pricing is configured separately. Quotas and costs are persistent only when the database is persistent.

These controls govern requests made through this application. They do not cap charges incurred by other uses of the same API key, hosting fees, taxes, account-specific service pricing, or manual provider actions. OpenAI billing records remain the source for invoiced API cost.

## Scheduler and deployment boundary

Run one Uvicorn process with `--workers 1`, one Render instance, and one attached disk. The in-process scheduler starts only when `SCHEDULER_ENABLED` is enabled, and each schedule also needs its own enabled flag. Atomic occurrence claims and stable idempotency keys prevent a restart from automatically resending an already claimed occurrence. The scheduler skips old missed occurrences rather than generating an uncontrolled catch-up burst.

This architecture does not provide horizontal scaling or guaranteed uninterrupted scheduled delivery. Render's disk-backed services have a brief interruption on deploy; the disk is not shared with other services and is not mounted in builds or pre-deploy commands. See the [official disk documentation](https://render.com/docs/disks).
