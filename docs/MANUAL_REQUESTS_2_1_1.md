# AI HUB 2.1.1 — reliable manual requests

This patch follows the owner's request to increase the application's limits and make deliberate Refresh, Generate and chat requests work smoothly. Deployment and live acceptance evidence belongs in the corresponding GitHub pull request; this source document alone does not establish that a deployment succeeded.

## Intended behavior

- Opening, reloading or switching agent workspaces reads saved content. It never initiates provider retrieval or generates a report.
- An intentional Refresh bypasses the application's fifteen-minute successful-cache interval and joins an already running refresh for the same agent. During a real NewsData cooldown it can use web search without sending another NewsData request before the provider's retry time.
- An explicit request for another agent waits in a bounded queue when the shared NewsData worker is occupied. Only accepted user requests may enter that queue.
- A new report requests an evidence check for its frozen research cutoff; it cannot silently treat a previously completed cached search as a new check. Active duplicate requests reconnect to the existing job. A new request after completion gets a new generation.
- A refresh retrieves at most 50 raw NewsData articles before deduplication, relevance checks and the strict twelve-hour publication filter. Background evidence in reports remains clearly dated.
- NewsData failure, insufficient coverage or an empty result can use bounded OpenAI web-search fallback. A completed search can legitimately find no qualifying fresh news.
- Queued and running work has distinct progress text in English and Arabic. Queue metadata does not disclose another visitor's identity, task or report.

## Application settings

| Variable | Deployment value | Meaning |
| --- | --- | --- |
| `NEWSDATA_DAILY_CREDIT_LIMIT` | `500` | Shared application allowance across all agents and visitors, separate from the provider quota. |
| `MAX_REPORTS_PER_DAY` | `500` | Shared research-attempt allowance, including report research, explicit feed web discovery and failed reserved attempts. |
| `REPORT_MAX_ACTIVE_PER_OWNER` | `10` | Maximum accepted queued/running jobs belonging to one visitor or owner. |
| `REPORT_MAX_ACTIVE_GLOBAL` | `50` | Maximum accepted queued/running jobs across the service. |
| `REPORT_JOB_WORKERS` | `2` | Existing worker concurrency; do not increase Uvicorn processes or service instances with this SQLite deployment. |
| `SCHEDULER_ENABLED` | `false` | No scheduled research. Completing an already accepted queued request is permitted. |

These are ceilings, not targets. The existing USD spending ceilings still apply. Prior usage, reservations, idempotency keys and saved records are retained when an allowance increases. Report configuration accepts 1–500 daily attempts; its unconfigured development default remains 50. Schedule capacity remains independently capped at 50 daily slots even though scheduling is disabled.

The repository Blueprint records these non-secret settings. Apply the same values to the existing Render service environment when it is managed directly; commit changes do not prove environment changes are active. Merge individual keys, preserve unrelated values, and verify the deployment using the updated settings.

## NewsData entitlement and accounting

The NewsData API key, plan and page-size configuration are not changed by this patch. An unknown/free configured plan remains limited to ten articles per page. A verified paid configuration supports fifty per page; an application setting never grants a paid subscription.

The NewsData pricing FAQ states that invalid requests and requests returning no results do not consume provider credits. The application reserves a page credit before HTTP, then reconciles only positively identified non-chargeable outcomes. Ambiguous transport errors, timeouts and server failures retain their conservative reservation. Adjustment records are additive and tied to the original charge; old usage is not erased.

Provider references checked for this change:

- [NewsData pricing and credit FAQ](https://newsdata.io/pricing)
- [Official NewsData OpenAPI specification](https://newsdata.io/openapi.json)
- [Render environment-variable configuration](https://render.com/docs/configure-environment-variables)

## Recovery and practical limits

Local persistence recovery must reuse an already paid result and its existing reservation. Transient local save failures receive a bounded retry; permanent result-validation errors terminate with a specific error. A PDF failure must not conceal the absence of a readable report snapshot.

This improves normal manual use; it cannot make a provider quota unlimited, remove a free provider's news delay, guarantee publisher availability, or create fresh developments where none can be verified. Provider errors remain visible and separate from application limits. Public visitors keep separate histories and reports with same-visitor shared reviewed memory.
