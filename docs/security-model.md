# AI HUB 2.0 security model

This describes the 2.0 source before deployment verification. Actual test results, deployed commits and live checks belong in [validation](VALIDATION_AR.md); earlier release results are not evidence that this version is live.

## Identity and public boundaries

The public workspace at `/` serves the 13 existing assistants, saved research reports, PDFs and discussion without owner credentials. `/admin` and administrative `/v1/*` routes remain protected by owner authentication. Explicit `/v1/public/*` routes use anonymous session ownership instead. Health and static/sign-in shells are intentionally public; `/health` does not call the provider or establish that paid model access works. Interactive API documentation remains disabled by default.

Bootstrap creates a host-only, HttpOnly, SameSite=Strict session cookie with a 30-day lifetime. It is Secure on HTTPS, including the deployment proxy's HTTPS configuration. Session identity comes from the cookie, never a client-supplied owner ID. Reports, jobs, PDFs and conversations are looked up under that owner. Knowing another visitor's or an administrative record ID does not authorize access. Cross-origin public mutations are rejected.

Public serializers use explicit field projections. They omit keys, accounting, raw research traces, provider configuration, database paths and private PDF paths. Safe source links, publication evidence, report sections and aggregate verification diagnostics can be displayed. The cookie represents an anonymous browser session, not a registered account, role system or cross-device recovery service. Administration credentials stay in browser memory and are cleared on logout; this does not provide MFA or multiple owner roles.

## New research and permissions

The workspace uses `app/agent_profiles.py` and `app/research.py`; profile configuration governs its editorial scopes. It does not read old Iran skill instructions or use NewsData. Retrieval profiles must have `submit_task` and `web_search` permission. Missing capabilities deny the action. The three text utilities—meeting minutes, pasted WhatsApp messages and literary translation—do not gain web search or external account access. Their empty default prompts produce honest local starter packs without a provider call.

Default research uses one live OpenAI Responses request with at most six native actions, followed by one tool-free structured synthesis when needed. Search/open/find are bounded separately from the final 20-news ceiling. There is no publisher allowlist and no per-publisher job fan-out. A configured alternative model requires explicit pricing and uses the conservative one-action compatibility path where long-context pricing is not verified. No paid provider request is automatically retried.

Only URLs supplied by native web-search source metadata, completed native page actions or native URL-citation annotations authorize publisher inspection. Free-form model prose and synthesis cannot create a second URL channel. The publisher fetcher permits HTTPS, resolves public IP addresses, rejects private/reserved/mixed DNS answers, and pins the TLS connection to a validated address while verifying the original hostname. Redirect destinations are checked again; a cross-host redirect must itself have native authorization. Page count, redirects, response bytes and socket operations have explicit bounds. Publisher blocks and unsupported formats become exclusion reasons, not permission to bypass access controls.

## Publication and content evidence

A frozen, server-created window covers exactly 12 hours. Freshness requires a complete timezone-aware publication timestamp from an article-bound publisher `datePublished`, `article:published_time`, or clearly labelled publication element. Date-only values, naive clocks, search crawl time and generic `dateModified` are not sufficient. Unbound sidebar metadata, conflicting dates, future publications and publications outside the window are rejected. Live coverage requires a particular new entry with verified publication timing; the overall page's latest update is not a new article.

Relevance and duplicate/wire-copy grouping occur before the 20-item limit. Source IDs, timestamps, publisher URLs and verification metadata are assigned by the server. Structured synthesis can choose only supplied source IDs and exact short evidence choices; free-form links or invented metadata cannot replace the backend fields. Unsupported items fail validation. A briefing with no accepted fresh items becomes a deterministic no-evidence report, rather than an invitation to fill it with older model knowledge.

Historical references can be old or undated when explicitly labelled background. Creative references are labelled separately; an original moodboard with no verified references must disclose that fact and avoid invented real-world evidence. A publisher timestamp proves publication, not the truth of every claim. Semantic interpretation, translation and cross-source event grouping retain model limitations; sections must distinguish attribution, analysis and scenarios.

Citation annotations and extracted publisher evidence are preserved with the source record. Raw discovery text and native action metadata are stored privately under `_research_provenance`; they are not public chat instructions. Retrieved pages, user text and prior reports are untrusted data and cannot grant tools or alter the system's policy.

## Jobs, immutable snapshots and PDF

Research jobs and snapshots use additive tables in the existing SQLite database. An owner-scoped idempotency key identifies a submission, and durable claims with renewed leases prevent workers from concurrently executing the same job. A completed report is a fixed snapshot; a new generation does not overwrite it. The latest-successful pointer advances safely while older reports remain readable.

A restart does not automatically resend ambiguous paid work. Unstarted queued work and local PDF preparation can be recovered; an interrupted paid attempt retains its reservation and explicit state. If PDF fails after the report is saved, the report remains available. PDF retry renders that saved data locally without another research request.

PDFs are produced with fpdf2/HarfBuzz and bundled, original Noto fonts under SIL OFL 1.1. License and provenance files are included with the fonts. No publisher images are fetched by the PDF renderer, and palette/composition drawings are labelled creative visual elements. Downloads check owner identity and use private, no-store responses. Files remain under the existing persistent disk beside the database. See [fpdf2 text shaping](https://py-pdf.github.io/fpdf2/TextShaping.html).

## Report-aware chat and language

The server loads completed conversation history; clients cannot supply system messages or fabricated assistant history. A conversation allows one pending turn, with history-version and idempotency checks. Context and payload sizes are bounded, and omitted older context remains in saved history.

Workspace chat automatically binds the currently displayed report, including an explicitly selected archived snapshot. Main chat can select relevant reports from the same session. Neither path can retrieve another owner or administrative report. The report context is a bounded projection of saved content and citations, not arbitrary database access.

A question needing fresh evidence may cause the trusted workspace orchestration to create a normal research job and then use its saved result. This is separate from the tool-free chat model response: the model does not receive general file access, arbitrary SQL, configuration tools or permission to send messages externally. Old evidence is not described as a fresh search. The selected report is not mutated by discussion.

Interface language switching changes controls and direction without automatically fetching or translating saved reports. Reports retain their own stored language. Arabic and English explanations can be requested in chat. No WhatsApp messages, emails, invitations or public report postings are sent by this workflow. See [chat and language guide](CHAT_AND_LANGUAGES.md).

## Secrets and stored data

`OPENAI_API_KEY` is the provider secret required for new research and chat. Owner credentials remain `APP_USERNAME` and `APP_PASSWORD`. Keep them in the private server environment; examples and CI use empty or synthetic values. `NEWSDATA_API_KEY` may remain for legacy compatibility, but the new workspace does not use it.

OpenAI receives the bounded task, verified source context, requested output language and, when needed, prior report or conversation context. Requests set `store:false`; this is not a guarantee of zero provider retention. Account-specific data controls and abuse-monitoring policies remain separate; consult [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data).

SQLite and PDFs contain private tasks, reports and conversation data. On Render, store the database, its WAL sidecars and PDFs under `/var/data/ai-hub`. Backups carry the same sensitivity and should be kept privately off the service disk. The source packager excludes runtime data, but cannot detect every secret manually pasted into a source file. Do not include keys, private reports or production databases in the public repository or source ZIP.

## Reservations and service limits

The intended 2.0 deployment sets `MAX_REPORTS_PER_DAY=50`, `DAILY_BUDGET_USD=900` and `MONTHLY_BUDGET_USD=28000`. These are conservative application ceilings, not expected invoices. Dollar budgets include research and chat; chat messages do not consume report attempts, but a fresh research job started from chat does. Limits apply across public and administrative execution through the shared ledger.

A transaction reserves quota and a conservative engineering cost estimate before paid work. The default research bound includes accumulated context re-reading, all possible search fees and long-context pricing headroom. Complete usage and billing metadata allow reconciliation; unknown, over-bound or interrupted results retain the reservation. There is no silent zero-cost fallback. Hosting, taxes, provider account terms and other uses of the key are outside this ledger. [Operational bounds](OPERATIONS_AR.md) explain the calculation.

Use one Uvicorn process, two configured report-job workers inside it, one Render instance and the existing attached disk. `SCHEDULER_ENABLED=false` keeps legacy schedules off by default. This local-disk architecture does not provide horizontal scaling or guaranteed uninterrupted delivery during deployments. See [Render disk documentation](https://render.com/docs/disks).

## Legacy compatibility

Protected administration and some prior API adapters remain for old clients and archived results. Earlier NewsData pagination, 48-hour windows, prepaid Arabic translations, manual report attachment and old publisher restrictions describe that legacy experience only. They do not govern `/v1/public/workspace/*`. Do not route new research through those adapters or assume old smoke commands validate the 2.0 workflow.
