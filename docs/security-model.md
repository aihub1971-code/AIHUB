# AI HUB 2.0.4 security model

This describes the current source implementation. **Release validation, deployment and post-deployment live verification are pending** until actual results are appended to [the 2.0.4 release note](FIXES_2_0_4.md) and [validation record](VALIDATION_AR.md). Earlier releases' results do not establish that this version is deployed or that live news coverage is fixed.

## Identity and public boundaries

The public workspace at `/` offers nine active assistants, owner-scoped saved reports, PDFs and discussion without owner credentials. `/admin` and administrative `/v1/*` routes remain protected. Explicit `/v1/public/*` routes use anonymous session ownership. Health and static shells are public; `/health` neither calls a provider nor proves that provider access or live coverage works. Interactive API documentation remains disabled by default.

Bootstrap creates a host-only, HttpOnly, SameSite=Strict cookie with a 30-day lifetime. It is Secure on HTTPS, including the deployment proxy's HTTPS configuration. Session identity comes from the cookie rather than a client-supplied owner ID. Reports, jobs, PDFs and conversations are read under that owner. Knowing a record ID does not authorize access to another visitor's or the administrator's records. Cross-origin public mutations are rejected.

Public serializers use explicit field projections. They omit keys, accounting, raw provider bodies and research traces, database paths and private PDF paths. Safe error codes, job references, stages, retryability and retry delays can be shown. News provider-plan metadata is labelled configured/unverified and is not a credential or account-entitlement assertion. The cookie is an anonymous session, not a registered account or cross-device recovery service. Owner credentials remain separate; this does not add MFA or multiple owner roles.

## Active and retired assistants

Nine profiles remain active. `mom-agent`, `whatsapp-briefing`, `literary-translation` and `pre-meeting-briefing` are removed from active registration and permissions. New inspection, execution, scheduling and fresh-research paths must not reactivate them. Existing owner-scoped reports, PDFs and conversations remain readable; original skill files and provenance hashes remain archived. An archived skill manifest or report identifier is not a grant of execution permission.

`app/agent_profiles.py` governs active report scopes. Existing capability checks continue to apply; a missing permission denies execution. Client payloads do not choose provider URLs, credentials, pagination cursors or runtime configuration. There are no email, WhatsApp, invitation or public-posting actions in this workflow.

## NewsData feeds and shared public cache

Seven current-news profiles use `app/news_feed.py`: Iran, geopolitics, basic income, UAE–EU trade talks, education, economic/cultural briefing and decision briefing. Their queries are server-owned and bilingual. The shared news cache stores public source records for those fixed profile queries; it does not store visitors' private tasks, reports or conversations as shared feed content. A custom report topic is handled within the private report workflow.

GET returns cached news promptly and may claim a background refresh when due. Manual POST refresh uses the same cache, interval, provider lease and credit gate. Refresh is limited to 100 accepted articles, 20 pages, 1,000 raw records and 90 seconds, with early stopping at the target, exhausted time window, provider end or invalid/repeated cursor. The 15-minute topic refresh interval is shared across sessions. A single provider refresh runs at a time, and provider failures or reported exhaustion can set a cooldown shared across topics. Manual refresh does not bypass these controls.

Unknown/free configuration limits page size to 10; configured paid access permits up to 50. `NEWSDATA_PLAN` is operator configuration, not an entitlement check. The existing `NEWSDATA_DAILY_CREDIT_LIMIT=200` remains in place. Each attempted provider page is charged conservatively before the request; the shared ledger includes prior adapter use and survives reload/redeployment. Refresh consumes provider credits, but it does not run a model, create a report or reset any allowance.

Fresh, cached, loading, empty and provider-error states are distinct. A failed update preserves useful prior cache contents and the timestamp of the last successful refresh; it cannot claim a new successful refresh. A successful empty response can replace an older snapshot. Stale content is labelled, and the browser excludes articles outside the current 12-hour display window. Provider cooldowns govern both automatic and manual refresh; repeated errors do not create unbounded rapid requests.

## Publication and content evidence

For NewsData, the backend checks structured publication metadata, including `pubDate` and `pubDateTZ`, against a server-created 12-hour window. It rejects missing/ambiguous, future or out-of-window timestamps, unsafe article links, duplicates and irrelevant records. The retained evidence level is `provider_metadata_only`, with `publisher_text_verified=false`. This does **not** mean the publisher's full text was fetched or independently verified, and it does not establish the truth of reported claims.

The 100-item ceiling applies after acceptance and deduplication; it is not a promise to find 100 articles or a reason to include older ones. The saved report distinguishes `actual_article_count`, `analyzed_article_count` and `requested_article_target`. Article inventory and the subset used for detailed model analysis are not interchangeable. Server-assigned identifiers, URLs, timestamps and source mappings remain authoritative; model prose cannot introduce substitute sources or dates.

A real diagnostic observation at 2026-10-20 18:11:09 UTC returned 10 articles whose newest publication was 06:10:59 UTC, outside the strict 12-hour window. The provider rejected page size 50 and reported an allowed range of 1–10. This supports an observed availability delay, not a verified free-plan classification. The interface explains that current access may be delayed and displays the newest returned timestamp; older articles are not relabelled as fresh. It does not claim that the release has already fixed live access.

## Bounded model work and native references

News analysis and optional Arabic/English translation are bounded, tool-free requests covered by the explicit report reservation. Input/output sizes and call counts are planned before paid work. Refreshing a feed, changing language or opening a cached page cannot trigger unmetered model work. Translations keep the original article identity and source fields; missing translations remain explicitly marked original-language text, and missing summaries cannot be invented. Failed paid model requests are not automatically retried.

Historical reading and Story Moodboard retain bounded native OpenAI research and labelled older/background references. Native search/open/find actions and publisher-page inspection have separate bounds; a model change requires matching explicit pricing and conservative compatibility handling. There is no per-publisher job fan-out.

For this native path, only URLs from native source metadata, completed native page actions or native URL-citation annotations authorize publisher inspection. Free-form prose cannot authorize another URL channel. HTTPS fetches reject private/reserved/mixed DNS answers, pin validated public addresses and check TLS hostnames. Redirects are revalidated; a cross-host target also needs native authorization. Page count, bytes, redirects and socket operations remain bounded. Publisher blocking is a limitation, not authorization to bypass access controls.

Native publisher timestamps and inspected passages remain distinct from NewsData metadata evidence. Old/undated references may be used only with their background/reference label. Original story material and palette drawings are creative interpretations, not documentary evidence. Retrieved content, prior reports and user text are untrusted data: they cannot add permissions, change policies or supply executable instructions.

## Durable jobs, failures and immutable snapshots

Additive SQLite tables preserve existing reports, conversations and accounting. Owner-scoped idempotency keys and durable claims with renewed leases prevent concurrent execution of the same report. A new generation does not overwrite an old snapshot; the latest-successful pointer advances separately.

Persisted progress includes searching, checking sources, analyzing, saving, PDF preparation and completion. Native evidence gathering may return from source checking to searching within its existing bound. Safe diagnostics retain the actual failed stage and cause while excluding provider prose, keys and money amounts from public errors. The UI restores the last job after reload, keeps any prior successful report, and offers an appropriate explicit recovery action. Retry-after timing is respected.

A restart does not resend ambiguous paid work automatically. Unstarted queued work and local PDF preparation can recover; interrupted paid attempts keep their reservations and explicit state. If PDF creation fails after saving a report, the report remains readable and PDF retry renders the same data locally without another research request.

PDFs use fpdf2/HarfBuzz and bundled Noto fonts under SIL OFL 1.1, with source and license files retained. The renderer does not fetch publisher images. Downloads enforce ownership and use private, no-store responses. Files remain beside the database on the existing persistent disk. See the bundled font provenance and [fpdf2 text shaping](https://py-pdf.github.io/fpdf2/TextShaping.html).

## Report-aware chat and language

The server loads completed conversation history; the client cannot submit system messages or forged assistant history. One pending turn, history-version checks, idempotency and bounded context/payload sizes protect each conversation. Omitted context remains in saved history.

Inline chat uses the displayed saved report, including a deliberately selected archived version. Main chat selects relevant reports from the same session. Neither can access another owner's or administrative records. Discussion can use retained reports of retired assistants without granting fresh execution to them.

Trusted orchestration may create an explicit research job for a request that needs fresh evidence, through the normal allowance and reservation gate. The chat model itself has no arbitrary file, SQL, configuration or messaging tools. Discussion cannot mutate the selected report. UI language switching changes controls and the requested feed display language; it does not regenerate or automatically translate saved reports. Available stored feed translations can be read without a new model call.

## Secrets, storage and limits

`NEWSDATA_API_KEY` supports the seven feeds. `OPENAI_API_KEY` supports bounded analysis, translation, native historical/creative research and chat. `APP_USERNAME` and `APP_PASSWORD` protect administration. Keep all credentials in the private server environment; examples and CI use empty or synthetic values. NewsData receives server-owned queries and its access credential; private reports and conversations are not part of shared feed retrieval.

OpenAI receives the bounded task, permitted source context, requested language and relevant saved report/chat context. Requests use `store:false`; that setting does not establish zero provider retention. Account-specific controls remain separate; see [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data).

The current settings remain `MAX_REPORTS_PER_DAY=100`, `DAILY_BUDGET_USD=900`, `MONTHLY_BUDGET_USD=28000` and `NEWSDATA_DAILY_CREDIT_LIMIT=200`. They are application ceilings, not expected invoices, and 2.0.4 does not raise or reset them. Chat shares model budgets but not report-attempt slots unless it initiates a report. The absent-variable report fallback stays 50; active schedule slots stay capped at 50, with scheduling disabled.

Paid report work reserves a conservative estimate before requests and reconciles only complete, bounded usage metadata. Unknown, over-bound or interrupted results retain the reservation. There is no silent zero-cost assumption. Native research and tool-free news work use their own bounded plans; older native-only estimates must not be applied to every current report. Hosting, taxes and unrelated uses of provider accounts fall outside the ledger.

Use one Uvicorn process, two configured report-job workers inside it, one Render instance and the existing disk. Keep database/WAL files and PDFs under `/var/data/ai-hub`; private backups carry the same sensitivity. The packager excludes runtime data but cannot detect a secret manually pasted into source. This architecture does not provide horizontal scaling or uninterrupted service during deployments. See [Render disks](https://render.com/docs/disks).

## Legacy compatibility

Protected administration and prior adapters remain for archived clients and results. Their 48-hour NewsData window, 20-raw-result ceiling, manual attachment and old publisher restrictions are historical contracts. The new feed is a separate bounded service; old smoke checks and old successful releases do not validate 2.0.4. Preserve historical release notes and append new evidence instead of rewriting earlier outcomes.
