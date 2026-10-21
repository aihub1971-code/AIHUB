# AI HUB 2.1 — Agent workspaces and intentional research

This document describes the current source contract. It does not claim a deployment, a successful live provider request, or completed release validation. Record actual commit, CI, deployment and verification evidence in [VALIDATION_AR.md](VALIDATION_AR.md). Earlier release notes remain historical records.

## User experience

The homepage contains seven active agent cards. The interface calls them **Agents / الوكلاء**. Story Moodboard and Decision Briefing Partner are retired from new work, alongside the four previously retired agents. Existing reports, PDFs, conversations and original source materials are retained under their existing access rules.

Opening or switching an agent restores saved state and its own durable conversation. It never starts research, generates a report or claims a provider refresh. The primary surface is the agent conversation. News and report controls are in the **News & reports** disclosure; there is no homepage feed or separate AI HUB Chat destination. Choosing a saved report opens its Briefing Pack directly.

Every active agent has NewsData and web retrieval capabilities. This includes historical reading, where books, scholarship and older primary material can be useful without being presented as current news. Retrieval choices are determined by the server and the intentional task; this is not a promise to invoke every provider for every message.

## What starts work

| Action | Effect |
|---|---|
| Open home, an agent, saved report, history, or change language | Saved reads only; no provider or model work |
| Open News & reports | Read the saved feed and report |
| Refresh news | Explicit provider refresh, subject to the shared gates |
| Generate fresh report | Explicit durable report job with its own frozen cutoff |
| Send a chat message | Model response; a request needing fresh evidence can start a research job |
| Read an in-progress job/feed | Status polling only; does not initiate another job or refresh |
| Retry a saved report's PDF | Local rendering from the saved report; no fresh research |

No idle timer starts news retrieval. `next_refresh_at` is an eligibility/cooldown timestamp. Keep `SCHEDULER_ENABLED=false` for the intended manual operation; legacy schedule management remains available for maintenance.

## News bounds and evidence

NewsData collection stops at **50 raw provider records across pages, before filtering and deduplication**. A rejected or duplicate record still consumes that raw allowance. The accepted display is also capped at 50. Pages and time are bounded; the NewsData stage has a 90-second deadline. A sparse response may produce fewer than 50 usable articles, including zero.

Current news must have an eligible publication timestamp inside the 12-hour window. Missing, ambiguous, future, old, irrelevant, duplicated or unsafe records are rejected. Live lists age locally and remove expired articles without asking the provider for replacements.

An explicit refresh can invoke one bounded supplemental web discovery when NewsData is insufficient or unavailable. It reserves existing application allowance, can incur OpenAI usage, permits at most two native actions and checks at most 12 publisher pages. The feed lease covers 420 seconds; status reads can continue while this work finishes. This is bounded retrieval, not automatic analysis or unrestricted browsing. Provider rate/credit cooldowns remain enforced. An application-scoped block can be explicitly rechecked after an operator resolves its configuration or allowance; the ledger still prevents provider I/O when the block remains.

NewsData metadata and inspected publisher text have separate evidence labels. Web results are accepted only through the publisher verification path. Source categories distinguish reporting, analysis, opinion, official statements and research where the source supplies that classification. Inspection is not a guarantee that a source's claims are true.

A `background_only` report retains substantive analysis and its dated references. It explicitly states that no fresh development was verified; it is readable and eligible for PDF output. Older background must not enter the live news list. Dates without a known time are displayed as dates, without an invented clock time.

## Conversation and reviewed shared knowledge

The server restores one durable thread per owner/visitor and active agent. Its agent binding is immutable. Browser-side identifiers do not choose another person's history, and switching agents does not reuse the previous thread or its draft. Earlier messages remain available through scoped pagination.

Relevant completed conversations, reviewed memory and cited reports can inform other agents for the **same owner or visitor**. Proposed and rejected notes are excluded from persistent shared memory. A preference, correction or knowledge note becomes shared only after approval; it remains a user note rather than independently verified evidence. This is retrieval context, not model training.

The message endpoint streams actual provider text. `status` and `delta` events update the interface. Partials remain unlinked plain text until `complete` supplies the validated saved response. `error` preserves a safe code and reference. An ambiguous connection loss can replay the same body and idempotency key without automatically creating another paid turn. A server-declared failed attempt remains a failed attempt and can be reviewed through saved history.

Fresh-research chat requests first return HTTP202 with a job. The interface polls saved job state, then resumes the same logical request with `fresh_job_id` and the same key. No fabricated token animation is used.

## Public and owner API

The public bootstrap is `GET /v1/public/bootstrap`. The owner bootstrap is `GET /v1/workspace/bootstrap`. In the table, `{prefix}` is `/v1/public/workspace` for a visitor or `/v1/workspace` for the authenticated owner.

| Route | Contract |
|---|---|
| `GET {prefix}/config` | Active agents, languages and retrieval configuration |
| `GET {prefix}/agents/{agent_id}` | Saved report and job state |
| `GET {prefix}/agents/{agent_id}/conversation?language=en` | Restore/create the agent's empty durable thread without provider work |
| `GET {prefix}/conversations/{conversation_id}?before=...` | Scoped saved history |
| `POST {prefix}/conversations/{conversation_id}/messages/stream` | Message body plus `Idempotency-Key`; SSE or HTTP202 research job |
| `GET {prefix}/agents/{agent_id}/news?language=en` | Cache-only feed read |
| `POST {prefix}/agents/{agent_id}/news/refresh` | Explicit `{language}` refresh |
| `POST {prefix}/agents/{agent_id}/reports` | `{topic,language}` and `Idempotency-Key` |
| `GET {prefix}/jobs/{job_id}` | Persisted progress and safe errors |
| `GET {prefix}/reports` and `/reports/{report_id}` | Owner/visitor-scoped saved snapshots |
| `GET {prefix}/reports/{report_id}/pdf` | Authorized PDF download |
| `POST {prefix}/reports/{report_id}/pdf/retry` | Local retry for the saved PDF |
| `GET/POST {prefix}/memory` | Read notes or propose `{kind,content,source_urls?}` |
| `PATCH {prefix}/memory/{memory_id}` | Explicit `{action:"approve"}` or `{action:"reject"}` |

The owner interface at `/admin/agents` uses the same assets with server-selected owner mode. The backend still requires owner authentication on every owner endpoint. The new JavaScript neither constructs Basic credentials nor stores or bridges them from `/admin`. The legacy maintenance console links to the new owner workspace and no longer exposes a separate chat tab. Old conversation APIs remain compatibility interfaces, not the new UI's source of thread selection.

## Migration and release checks

Use the existing persistent database and PDF directory. Schema changes are additive. Do not reset usage ledgers or rewrite saved reports. Keep original source manifests and retired skills as provenance, rather than reactivating them to make an old count match.

Before release, verify seven active profiles, both retrieval permissions, protected owner routes, visitor isolation, restored per-agent history, reviewed-memory filtering, manual-only navigation, the 50-raw ceiling, distinct provider/application errors, real streaming, Dubai timestamps and background-only PDFs. Exercise English, Arabic and narrow screens. Passive checks do not require provider work; any live provider test is a distinct intentional action and its actual outcome must be recorded.

Implementation references: [workspace UI](../app/static/workspace.js), [feed](../app/news_feed.py), [bounded web discovery](../app/retrieval.py), [workspace API](../app/workspace.py), [conversation and memory API](../app/workspace_chat.py), [research](../app/research.py), [security model](security-model.md).
