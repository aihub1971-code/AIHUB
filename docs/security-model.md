# AI HUB 1.4.0 security model

## Scope and identity

AI HUB is a private, single-owner application. HTTP Basic authentication uses `APP_USERNAME` and `APP_PASSWORD`, compared in constant time. Every `/v1/*` route requires owner authentication, including chat history, report downloads, usage, configuration flags and schedules. The public page contains no saved report or conversation data before authentication.

Use HTTPS for remote access. `/`, static assets and `/health` are intentionally public. `/health` checks liveness without calling OpenAI; it does not prove that the API key, model or web-search access works. Interactive API documentation is disabled by default. `ENABLE_API_DOCS=true` makes `/docs` and the schema public and is intended for local development.

The browser keeps authentication only in memory. Its sole application preference in `localStorage` is `aihub.language`. The authenticated request callback accepts same-origin `/v1/` paths and does not expose the authorization header to the chat module. Logout clears private page state, aborts active requests and prevents late responses from repopulating the page. This design does not provide multi-user accounts, roles, MFA or individual session revocation.

## Research permissions and source boundaries

`config/permissions.json` defines per-agent action and tool allowlists; missing permission entries deny access. Skill text, tasks and retrieved material cannot authorize additional tools or external actions. The only research provider is OpenAI, using the same server-side `OPENAI_API_KEY` as generation and chat.

The four news agents use six independent publisher requests defined by `NEWS_RESEARCH_GROUPS`: Financial Times, The Atlantic, Politico, The Wall Street Journal, The Daily Telegraph and Al Jazeera. At most three research requests run concurrently **within each report**. Each request permits at most one native `web_search` action. A final structured synthesis uses `tools=[]`.

| Operation | Maximum Responses requests | Native search limit |
|---|---:|---:|
| News report | Six research requests plus one synthesis: seven | One action per research request; six total |
| Other agent task requiring research | One research request plus one answer: two | One action |
| Task without research | One answer | None |
| Chat message | One answer with `tools=[]` and `tool_choice="none"` | None |

These are planned stages, not retries. No provider request is automatically repeated. News synthesis is skipped if the merged research yields no usable sources. If some publisher requests fail, useful results may still produce a report with `research_incomplete` and `failed_request_count`; the full cost reservation is retained. If all research requests fail, the run fails. Each stage has its own bounded input and output allowance; aggregating accounting across stages does not create one larger model context. News research payloads are limited to 50,000 serialized bytes, and each reserved input bound stays below the 272,000-token pricing threshold. Final model payloads are limited to 200,000 bytes. Sources are interleaved, deduplicated and assigned stable IDs, with at most 20 combined news and background items. No minimum count is padded with invented content.

All native-search agents are restricted to `ft.com`, `theatlantic.com`, `politico.com`, `politico.eu`, `wsj.com`, `telegraph.co.uk`, `aljazeera.com` and `aljazeera.net`, including valid subdomains. Hosted `allowed_domains` filters and a separate server hostname-boundary check enforce the policy. Source shortages do not expand it.

Structured news URLs must match authorized native source metadata. URL matching permits equivalent Unicode/UTF-8 percent-encoded paths, normalizes percent-escape case and removes tracking parameters. It does not invent article paths or treat a different protocol, `www` host, AMP path or reserved encoded separator as authorization for another URL. Broader duplicate detection does not grant source authorization.

Attributed notes and publication/event dates remain **model-derived research evidence**. They are not independently fetched publisher bodies or verified verbatim quotations. A source citation does not prove full access to a paywalled article. Publisher coverage and semantic accuracy are not guaranteed. See the [OpenAI web-search metadata documentation](https://developers.openai.com/api/docs/guides/tools-web-search).

The browser inserts structured headlines and summaries as text. Official-article buttons have a separate approved-host and HTTP(S) check; credentials, nonstandard ports, deceptive host suffixes and homepages are rejected. Links open with `noopener noreferrer`. Copy and download retain the saved report. Other generated content uses a limited Markdown renderer without inserting raw HTML. This release does not send WhatsApp messages, emails or invitations, or publish generated reports to third parties.

## Chat context and language

Conversations, messages and turn states are stored in owner-scoped SQLite records. The client submits a message, language and optional selected report; it cannot supply system messages or forged assistant history. The server retrieves recent completed user/assistant pairs itself. A conversation permits one pending turn, and a history-version check prevents sending a request prepared against changed history.

Chat context is bounded to 20 completed turns within 100,000 serialized bytes, an explicitly selected completed report within 30,000 bytes, and a total payload within 200,000 bytes. A message may contain up to 8,000 characters; a conversation title is limited to 120. Older context may be omitted without deleting stored history, and truncation is reported. The selected report is labeled untrusted archived data and is not the full database. Chat has no callable tools, live web search, filesystem access or permission to change settings, schedules or reports. These boundaries limit actions; they do not guarantee the factual correctness of generated prose.

Arabic and English are stored with new reports and schedules. Changing interface language updates page direction and controls without making an OpenAI call or translating saved content. A report can be explained or translated in chat while its saved original remains unchanged. The additive migration creates chat tables and adds `schedules.language` with `ar` for existing schedules; existing Arabic report idempotency hashes remain stable. See [Chat and language guide](CHAT_AND_LANGUAGES.md).

## Secrets and stored data

The required private values are `APP_USERNAME`, `APP_PASSWORD` and `OPENAI_API_KEY`. Keep them in a private environment file or the Render service environment. The browser never receives the provider key. Do not place credentials in code, repository URLs, screenshots or support messages. `.env.example` contains empty credential fields; CI uses synthetic credentials.

Tasks, selected skills and research material are sent to OpenAI for report generation. Chat sends the current message, bounded completed history, selected report context and whitelisted platform facts. Requests set `store:false`, while AI HUB keeps its own history in SQLite. This flag is **not a promise of zero provider retention**: separate abuse-monitoring and feature-specific retention rules may apply. Consult [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data) and the account's applicable settings.

SQLite contains sensitive tasks, reports, schedules, conversations and accounting records at `AI_HUB_DATABASE_PATH`. On Render, the database and WAL sidecars must be under `/var/data/ai-hub` on the persistent disk. The source packager excludes runtime data; it cannot detect every secret pasted into ordinary source files. Use the SQLite-native backup script, keep backups private and copy them off the service disk. Backups contain the same sensitive content as the live database.

## Usage controls and billing

Before any paid call, an atomic SQLite transaction reserves a conservative estimate for every planned model stage, its bounded input/output usage and native search fees. Default shared estimated budgets are **$5 per day and $100 per month**. Manual reports, scheduled reports and chat turns all consume these USD budgets. The separate maximum of **50 accepted report attempts per day** applies only to manual and scheduled reports. Chat has a separate ledger and conversation history; it does not consume that report-attempt quota or appear as a report in the report archive. Usage exposes both the combined cost and separate report/chat totals.

Chat messages require an `Idempotency-Key`. A matching replay returns or identifies the existing turn without another provider call; changing its message, language or report selection rejects the key. Report replays likewise preserve task/language identity. Pending, interrupted and ambiguous failed attempts retain their reservation. Missing usage, unexpected billing metadata and partial research failures prevent downward reconciliation. Restarts record stale pending work as interrupted instead of resending it. An explicit new attempt with a new key is a new potentially billable operation.

Accounting is deliberately conservative and may exceed the eventual invoice. Prices are tied to the configured model, with native search fees configured separately. These controls cover only this application, not other uses of the same key, hosting, taxes or account-specific billing terms. OpenAI billing records determine invoiced API charges. Persistent limits require persistent SQLite storage.

## Scheduler and deployment boundary

Use one Uvicorn process with `--workers 1`, one Render instance and one attached disk. `SCHEDULER_ENABLED=false` is the default; both the global scheduler and each schedule must be enabled for automatic execution. Atomic occurrence claims and stable idempotency keys prevent a restart from resending an already claimed occurrence. Old missed occurrences are skipped instead of triggering an uncontrolled catch-up burst.

This architecture does not provide horizontal scaling or guaranteed uninterrupted scheduled delivery. Disk-backed Render services have a deployment interruption; their disk is not shared with other services or mounted during builds or pre-deploy commands. See the [official Render disk documentation](https://render.com/docs/disks).
