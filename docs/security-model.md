# AI HUB 2.1 security model

This describes the current source implementation. It is not a deployment attestation or a successful live-provider test. Actual release evidence belongs in [VALIDATION_AR.md](VALIDATION_AR.md). See [the migration contract](UPGRADE_2_1.md) for user-visible behavior and API routes.

## Identity and access

The public workspace at `/` uses an anonymous browser session. Bootstrap sets a host-only HttpOnly, SameSite=Strict cookie with a 30-day lifetime, Secure when served over HTTPS. The server derives ownership from the session, not from a user-supplied owner ID. Reports, PDFs, jobs, conversations and memory notes are scoped to that owner. Knowing an identifier does not grant access to another visitor's or the administrator's records. Public mutations enforce same-origin and JSON request checks.

`/admin/agents` serves the same agent UI for the authenticated owner. Its endpoints use `/v1/workspace/*` and enforce owner authentication. The server-selected body mode chooses the API prefix; it is not an authorization boundary. The new JavaScript does not create, persist or bridge Basic credentials from the legacy `/admin` maintenance console. Browser-managed authentication and server checks remain required. Public sessions cannot authorize owner endpoints.

Public serializers project allowed fields. They omit credentials, costs, private file paths, raw provider response bodies and research traces. Safe error codes, references, stages, scopes and retry information can be displayed. Public and owner memory stores remain distinct. Anonymous cookies are not registered accounts, cross-device recovery, MFA or multiple owner roles.

## Active agents and retained archives

Seven agents remain active. Story Moodboard and Decision Briefing Partner are retired from new execution, alongside meeting minutes, WhatsApp briefing, literary translation and meeting preparation. Registry and capability checks govern new work; archived skill files and reports do not grant permissions. Existing records remain subject to their original ownership.

All seven active agents have NewsData and web retrieval capabilities. Their server-owned profiles define scope and evidence requirements. Clients cannot select arbitrary provider endpoints, credentials, pagination cursors or runtime settings. This workflow has no email, WhatsApp, invitation or public-posting action.

## Intentional retrieval and shared news

Opening home, opening or switching an agent, changing language, reading history or checking saved job/feed state does not initiate a provider call. News GET is cache-only even when empty or expired. New work follows an explicit refresh, report request, or submitted question needing fresh evidence. Keep the scheduler disabled for the intended manual operation.

The news cache contains public source material for fixed server-owned profile queries. Private tasks, conversations, reports and memory notes do not become shared feed content. A deliberate refresh bypasses the former 900-second successful-cache wait; provider concurrency/lease controls, credit accounting and actual provider cooldowns remain enforced. Browser timers can age the visible snapshot or poll an accepted explicit request; they cannot claim a new provider refresh.

NewsData is capped at 50 raw records across pages before eligibility checks or deduplication, with a 90-second stage deadline. The display cap is also 50; it does not promise 50 accepted articles. Rejected records still count toward the raw bound. Current news must have an eligible publication timestamp inside the 12-hour window. Unsafe URLs, duplicate, irrelevant, undated, ambiguous, future and out-of-window records are rejected.

An intentional refresh can reserve one supplemental OpenAI discovery when provider coverage is insufficient or unavailable. It has at most two native actions and 12 inspected publisher pages. It can incur model/tool usage and an application attempt; this is no longer a blanket model-free operation. The combined refresh lease is 420 seconds. Provider cooldowns remain mandatory. Application-scoped blocks can be explicitly rechecked after an operator changes configuration or allowance, but the ledger still prevents unresolved over-limit work.

Each attempted provider page is accounted conservatively. Existing ledgers survive restarts and deployment. A failed update preserves useful prior cache data and its last-success timestamp. An empty successful result is distinct from provider failure. Expired articles are removed from the current display without being replaced through automatic retrieval.

## Evidence and untrusted material

NewsData structured metadata establishes the provider's supplied publication fields, not independent inspection of publisher text or the truth of reported claims. Publisher-verified web material uses the existing authorized-source and page-validation path. The UI and reports distinguish these evidence levels and source categories such as opinion, analysis, official statements, research and reporting.

Native source metadata, completed native page actions and native URL-citation annotations authorize publisher inspection. Free-form generated prose does not create another URL authorization channel. HTTPS fetching checks public DNS/IP ranges, validates TLS hostnames, revalidates redirects and bounds pages, bytes and time. Publisher blocking is an evidence limitation, not authority to bypass controls.

Older references can support a dated background-only report when the profile allows it. Such a report must state that no fresh development was verified and must not relabel its background as current news. Date-only sources remain date-only. Server source IDs, URLs and publication metadata remain authoritative; generated prose cannot substitute invented links or dates.

Retrieved pages, user text, saved conversations, reports and memory are untrusted context. They cannot grant permissions or override system rules. Approving a memory note records the user's decision; it does not independently verify the claim or transform it into a published source.

## Durable conversations and shared memory

The server restores one durable conversation for each owner/visitor and active agent. Its agent binding is immutable. It loads saved history itself; clients cannot submit forged system messages or assistant history. One pending turn, sequence checks and idempotency protect concurrent conversation updates. Earlier messages remain stored even when bounded context retrieval omits them from a model request.

Cross-agent retrieval uses relevant completed conversations, cited reports and approved notes for the same owner. Proposed and rejected notes are excluded from approved persistent memory. Preferences, corrections and knowledge notes have explicit proposal/approval/rejection operations. These are retrieval records, not training updates to model weights.

Streaming forwards real provider deltas. The UI keeps partial text unlinked until the complete answer has passed source-link validation and been saved. A partial stream is not stored as a completed answer. A transport interruption can be investigated with the same idempotency key; a known failed turn is not automatically resubmitted as new paid work. Ordinary researched questions use a bounded web discovery and publisher-checking stage before the streamed answer. Only explicit report-generation requests create report jobs.

Chat has bounded context and no arbitrary filesystem, SQL, configuration or outbound-messaging tools. A submitted factual question can research public web sources beyond the report bibliography and the agent's usual subject. General chat references are not subject to the news feed's twelve-hour eligibility filter, but dates are preserved and older material cannot be presented as a fresh development. Publisher inspection uses the same public-address, TLS, redirect, byte, page and URL-attestation safeguards. Unsupported model-created links are rejected.

The complete discovery-plus-answer allowance is reserved before provider I/O. A new web-research turn consumes one shared daily research attempt; an idempotent replay does not reserve or search again. Ordinary local conversation retains its existing budget treatment. Checked turn evidence is stored with the owning conversation and cannot be attached to another visitor's context or approved memory by supplying a foreign identifier. Search queries use only task-relevant context, not unrelated private memories or preferences. Reading or switching a conversation starts no paid work.

## Reports, PDFs and accounting

Schema additions preserve existing records and accounting. Reports are immutable snapshots with language, cutoff, source IDs and generation sequence; the latest-successful pointer is separate. Persisted stages reflect actual searching, checking, analysis, saving and PDF preparation. A failed job retains safe diagnostics and does not overwrite a previous successful report.

Ambiguous paid work is not automatically resent after restart. Unstarted queued work and local PDF rendering can recover within their established rules. PDF retry reads the saved report rather than researching again. Downloads enforce ownership and private/no-store responses. Bundled Noto fonts, their license and provenance remain with the renderer; it does not fetch publisher images for PDFs.

Conservative reservations precede paid work. Complete bounded usage can reconcile them; missing or interrupted usage retains the reservation. Application report/model/NewsData ceilings and provider credit/rate limits are different controls and must produce distinguishable errors. The upgrade does not reset ledgers or raise the configured budgets. Current configuration is documented in [OPERATIONS_AR.md](OPERATIONS_AR.md).

## Secrets and operations

Keep `NEWSDATA_API_KEY`, `OPENAI_API_KEY`, `APP_USERNAME` and `APP_PASSWORD` in the private server environment. Client assets, examples and tests must contain no real secret. Model requests use the bounded task and permitted same-owner context. `store:false` is not a claim of zero provider retention or a substitute for the account's data controls.

Use one Uvicorn process, the configured in-process report workers, one service instance and the existing persistent disk. SQLite, WAL files, private PDFs and backups carry the same sensitivity. The source packager excludes runtime data but cannot identify every secret manually pasted into source. This deployment arrangement is not a horizontal-scaling or zero-downtime guarantee.

Legacy routes exist for maintenance and saved clients. New legacy-adapter news requests follow the 12-hour/50-raw contract; stale continuation windows require a new explicit refresh. Earlier 48-hour/20-result snapshots remain historical records, not instructions for new retrieval. Preserve historical release notes and append evidence for the new version rather than rewriting prior outcomes.

Implementation references: [public session checks](../app/public.py), [authentication](../app/auth.py), [workspace projections](../app/workspace.py), [conversation/memory orchestration](../app/workspace_chat.py), [chat streaming](../app/chat.py), [news feed](../app/news_feed.py), [retrieval bounds](../app/retrieval.py), [research verification](../app/research.py).
