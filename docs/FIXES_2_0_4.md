# AI HUB 2.0.4 — live news, report recovery and active assistants

Updated on **20 October 2026**. **The initial 2.0.4 release is deployed and its recorded live API, report and archive checks are complete.** Current NewsData access still returns delayed articles outside the required 12-hour window, so real-time news coverage remains unresolved. A subsequent Arabic PDF inline-ISO correction has passed 20 focused local PDF tests; its follow-up deployment and verification of a newly generated live Arabic PDF remain pending.

Historical [2.0.1 validation](VALIDATION_AR.md), [2.0.2](FIXES_2_0_2.md), [2.0.3](FIXES_2_0_3.md) and [validation](VALIDATION_AR.md) records retain their original evidence. Their successful checks must not be relabelled as validation of 2.0.4.

## Confirmed problems before this release

The report workflow could return a generic failure in both languages while withholding the useful cause. Live diagnosis identified `invalid_report_stage` when a permitted bounded follow-up moved from `checking_sources` back to `searching`. Native-search bounds also produced failed attempts, and provider details were not sufficiently preserved for diagnosis. A healthy service process did not mean a report had completed successfully.

The current NewsData access was also tested independently of the report UI. At **2026-10-20 18:11:09 UTC**, the latest publication in the tested Iran page was **2026-10-20 06:10:59 UTC**: 12 hours and 10 seconds earlier. All **10 returned articles** were outside the strict 12-hour window, so that page had **zero qualifying articles**. A request with page size 50 received a provider rejection stating that the allowed range was 1–10.

These observations establish the returned page's timing and size restriction. They do **not** verify the account's subscription tier, prove a provider-wide outage, or establish that 2.0.4 now obtains real-time articles. `NEWSDATA_PLAN` remains operator configuration, with unknown/unverified access represented honestly. Older material must not be labelled fresh to conceal the delay.

## Live-news dashboard and contract

The dashboard has a compact live-news section, defaulting to Iran, with a selector populated from the configured news-enabled profiles. Supported assistant workspaces have a separate **Live news / الأخبار المباشرة** tab. The home view shows six articles and opens the full list without creating a report. Existing assistant-card report generation remains explicit through the card action.

Seven profiles use the new NewsData feed service: `ubi-research`, `iran-news`, `uae-eu-trade-talks`, `geopolitical-morning`, `education-observatory`, `economic-cultural-briefing` and `decision-briefing`.

| API | Behavior |
|---|---|
| `GET /v1/public/workspace/agents/{id}/news?language=ar` | Returns the shared cached feed and starts bounded background refresh when due |
| `POST /v1/public/workspace/agents/{id}/news/refresh` | Accepts `{language}` and requests refresh through the same interval, credit and cooldown gates |
| Feed states | `fresh`, `cached`, `loading`, `empty`, `provider_error`, with explicit `stale` and `partial` information |
| Visible metadata | Last successful refresh, next refresh, fetched/displayed counts, publisher, original link and publication time |

The feed displays up to **100 accepted, relevant, deduplicated articles from the last 12 hours**. Future, ambiguous, missing or expired timestamps and unsafe links are excluded. The limit is a ceiling, not a promised number. The current live list and a saved report have separate timestamps and purposes.

The evidence level is **`provider_metadata_only`** and `publisher_text_verified=false`. NewsData's structured publication fields are checked; this does not claim independent inspection of the publisher's full text or verification of every reported assertion. Historical/creative native research retains its own separately labelled source evidence.

The service uses a shared cache, a **15-minute topic refresh interval**, a single active provider refresh and shared provider-error cooldowns. Retrieval stops at the first applicable bound: 100 accepted articles, **20 pages**, **90 seconds**, provider end, exhausted window or invalid/repeated pagination. The maximum raw-record bound is 1,000. Unknown/free configuration caps pages at **10**; configured paid access permits up to **50**, subject to the actual provider response and configured size. Manual refresh does not bypass these constraints.

The existing **200 NewsData credits/day** limit remains shared with previous adapter usage. A provider page is accounted for before its request; reload, new sessions and deployment do not reset that ledger. Cache refresh performs **no model call, report generation or paid translation**. Arabic/English feed display uses stored translations when available and explicitly identifies source-language content when unavailable.

A failed refresh preserves useful earlier cache content and the last successful timestamp while displaying the provider failure. A successful empty result is distinct. The observed-delay notice shows the latest returned publication and explains that current access may be delayed; it does not assert a free plan without verified metadata.

## Report pipeline and recovery

The implementation permits the legitimate source-checking-to-searching transition within the existing native bound and adds the **saving** stage. Public jobs preserve a safe error code, reference, failed stage, retryability and retry-after value. Raw provider response bodies, credentials and monetary details are not public diagnostics.

Both languages map known causes, including provider authentication/access/rejection, quota, rate limit, timeout, unavailability, evidence validation and report-stage failure. Reload restores `last_job` and the prior successful snapshot. A first failed attempt does not claim that a previous report exists.

Recovery actions distinguish reconnecting to an existing job, reloading status, reopening the previous report, rerendering a PDF and explicitly retrying a report. Cooldowns disable repeated attempts until they expire. Unconfirmed submissions reuse their idempotency key. No failed paid model request is automatically rerun.

News analysis and optional translation are **bounded, tool-free model work reserved as part of an explicit report**. They cannot turn an automatic feed refresh into unmetered model research. The report preserves the full accepted inventory/source mapping and distinguishes collected count (`actual_article_count`), analyzed count (`analyzed_article_count`) and ceiling (`requested_article_target`). A 100-article inventory does not imply detailed analysis of every article. When qualifying articles exist, unavailable required OpenAI analysis produces a clear report failure rather than a successful unanalyzed report. A genuinely empty qualifying window can still produce an honest zero-article report with its provider limitation. Historical Reading Companion and Story Moodboard retain bounded native research.

The saved-report and PDF-input JSON bounds are both 5,000,000 bytes, enough for the tested 100-item maximum-length multilingual inventory. Redundant language-field copies are removed while original source text and all accepted links remain. Normalized source URLs longer than 2,000 characters are rejected rather than truncated. PDF output limits remain unchanged. Arabic count ratios use directional isolation, and both PDF languages include the report ID and generation timestamp.

## Nine active assistants, retained archives

Nine assistants remain active. The following four are retired from active registration, permissions, new task execution and scheduling:

- `mom-agent` — meeting minutes.
- `whatsapp-briefing` — pasted WhatsApp summaries.
- `literary-translation` — literary translation.
- `pre-meeting-briefing` — pre-meeting preparation.

Saved owner-scoped reports, PDFs and conversations remain readable. Original skill files and their existing provenance hashes remain retained. `docs/source-manifest.json` distinguishes nine active assistants from the original 13-assistant snapshot, while retaining all 14 original skill-file records; five of those files belong to the four retired assistants. An archived file or report does not authorize fresh execution.

## Existing limits and deployment settings

This release does **not** change `MAX_REPORTS_PER_DAY=100`, `DAILY_BUDGET_USD=900`, `MONTHLY_BUDGET_USD=28000`, `NEWSDATA_DAILY_CREDIT_LIMIT=200`, the retained attempt/credit ledgers, or owner/session boundaries. The absent-variable report fallback remains 50. The separate 50 active-schedule-slot cap and `SCHEDULER_ENABLED=false` remain in place.

The existing service, disk and database stay in use, with one Uvicorn process and two configured report-job workers. `render.yaml` changes are explanatory comments only. The initial deployment is recorded below; no budget increase, quota reset, provider-plan upgrade or credential change was performed.

`APP_VERSION` and the workspace CSS/JavaScript cache references identify 2.0.4. Historical legacy-page asset references remain tied to their existing assets.

## Validation and release status

| Gate | Current record |
|---|---|
| Consolidated implementation checks | Integration review complete; Python/JavaScript syntax and `git diff --check` pass; official Render Blueprint schema validation passes |
| Initial release Python and JavaScript tests | `python -m pytest -o addopts='' -q`: **1,041 passed** in 37.83 seconds; one upstream TestClient deprecation warning. All four `node tests/test_ui_*.js` suites: **93 passed** (news 29, public 10, workspace 41, chat 13) |
| Merge and CI | [PR #15](https://github.com/aihub1971-code/AIHUB/pull/15) merged as `cf9494d82fdc1da4bc7b264d242057420c3a9a62`. [PR CI 39476452804](https://github.com/aihub1971-code/AIHUB/actions/runs/39476452804) and [main CI 39476671212](https://github.com/aihub1971-code/AIHUB/actions/runs/39476671212) passed |
| Initial deployment | Render `dep-dbbrbclg1s2s73a2gc80`, merged commit above, **live at 18:41:42.832743 UTC** on 20 October 2026 |
| Served version and public API | `/health` served **2.0.4**, `ok`, `ready`. **47/47 live API checks passed at 18:43:20–18:43:43 UTC**, using an isolated anonymous HTTP session |
| Active and retired routes | Nine active profiles matched the shipped defaults and numbering **01–09**; all **12** retired-assistant workspace/news/report-submission checks returned **404**. Historical and Story Moodboard news endpoints also returned **404** |
| Live reports | Two completed zero-article news reports and one real AI historical report are recorded below; these do not establish successful analysis of fresh news |
| Preserved reports and PDFs | At **18:45:42.706076 UTC**, all **13 existing reports** retained their original content digests and all **13 PDF SHA-256 hashes** matched their pre-deployment values |
| Subsequent Arabic PDF correction | Inline ISO timestamp direction correction: **20 focused PDF tests passed in 22.13 seconds**. Two added tests bring the expected hosted full-suite count to **1,043**; that follow-up CI result, deployment and a new live Arabic PDF remain **pending** |

The automated checks cover feed bounds/cooldowns and credits, empty-versus-error handling, Arabic/English notices, report stages and durable failures, explicit retry behavior, registry enforcement and archive retention. Synthetic tests and the explicitly identified live observations are separate evidence; the initial full-suite result does not substitute for follow-up release validation.

Local PDF verification retained all 100 inventory records and 100 distinct original source links in both languages (45 English pages, 46 Arabic pages for the synthetic fixture). Links remained within page bounds; rendered covers, inventory pages and source pages were inspected. Empty provider-delay reports rendered as one-page Arabic and English PDFs. These are synthetic checks, not evidence that the live provider supplies current articles.

## Completed live observations

| Report | Report ID | Observed outcome on 20 October 2026 |
|---|---|---|
| Iran War News | `rpt_54a8eaf1388c46aa965cf167e88a05b6` | Ready at **18:42:28.995614 UTC**; **0 qualifying articles from 10 raw provider records**, with the delay disclosed |
| Global Geopolitical Briefing | `rpt_2a3956a211e5438c9bbc44030b775aef` | Ready at **18:43:13.603875 UTC**; **0 qualifying articles from 10 raw provider records**, with the delay disclosed |
| Historical Reading Companion | `rpt_ca0d28f315574687b46ef9085a18bc1e` | Verified at **18:46 UTC**: real AI research, **17 sources**, and a **7-page PDF** |

The Iran and global results verify successful completion of honest empty reports. They do **not** verify the nonempty fresh-news analysis path. The historical result verifies one real native-research and PDF path; its historical references are not fresh news.

The other five news feeds—basic income, UAE–EU trade talks, education, economic/cultural briefing and decision briefing—each completed exactly one provider-page refresh during the API checks: **10 raw records, 0 accepted articles**, `status=empty`, `stop_reason=window_exhausted`, and `provider_delay`. No provider error was returned. Each immediate manual refresh reused the same snapshot, last-successful timestamp and pagination with `refresh_started=false`; the five checks consumed five provider pages in total. The isolated API session generated no reports. No private global quota read was attempted; the retired requests returned 404 before report submission.

The public API confirmed seven feed IDs, a maximum of 100 accepted articles, 12-hour coverage and a 900-second refresh interval. Profile checks compared published kinds, retrieval flags and bilingual default topics; the public schema does not expose literal `domain` or `required_inputs` fields. Positive live fresh-article, maximum-inventory and translation behavior remain covered by synthetic tests rather than a successful nonempty live feed in this record.

## Remaining provider action and PDF follow-up

To supply articles inside the strict 12-hour window, verify the NewsData account's **real-time entitlement** and provide a key authorized for that access through the existing service's private `NEWSDATA_API_KEY` setting; update that secret if a different key is required. Set `NEWSDATA_PLAN=paid` **only when paid access is actually confirmed**. That configuration flag does not grant entitlement or remove a provider-side delay. **No plan upgrade or key change has been performed.** Do not weaken the freshness window or relabel delayed articles as current.

The Arabic inline-ISO PDF correction is a subsequent change: its 20 passing focused tests establish local validation only. Record the follow-up commit/deployment and inspect a newly generated live Arabic PDF before marking that remaining step complete. The 13 preserved archive PDFs above were verified unchanged and are not evidence of the new rendering correction.
