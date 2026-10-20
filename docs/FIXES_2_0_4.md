# AI HUB 2.0.4 — live news, report recovery and active assistants

Updated on **20 October 2026**. **The initial 2.0.4 release and its first follow-up are deployed.** Current NewsData access still returns delayed articles outside the required 12-hour window, so real-time news coverage remains unresolved. The Arabic PDF timestamp correction is now verified in a newly generated live PDF. Further live checks identified an expired-snapshot race during report retrieval and localized the Story Moodboard schema rejection to the publisher-text evidence-quote field. The second correction described below requires its own deployment and live acceptance after this source record is committed.

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

`APP_VERSION` identifies 2.0.4. The first follow-up uses workspace JavaScript cache query `2.0.4.1`; historical legacy-page asset references remain tied to their existing assets.

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
| First follow-up CI and deployment | [PR #16](https://github.com/aihub1971-code/AIHUB/pull/16), merged as `079af325fe5583dbf13c5d2dbf3ea79de53ab729`: **1,069 Python tests and 96 JavaScript checks** passed, with Blueprint and source-package gates. Render `dep-dbbrvlc9v7es73auhbb0` became **live at 19:25:06.755576 UTC** |
| New live Arabic PDF | `rpt_50da224235124de78700f4fa636a874f`, created **19:25:57.218747 UTC**, PDF ready **19:25:59.087799 UTC**. The actual downloaded **45,524-byte, one-page PDF** opens and displays the inline ISO timestamp in correct order, readable Arabic and the correct 0 / 100 count |

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

## Remaining provider action

To supply articles inside the strict 12-hour window, verify the NewsData account's **real-time entitlement** and provide a key authorized for that access through the existing service's private `NEWSDATA_API_KEY` setting; update that secret if a different key is required. Set `NEWSDATA_PLAN=paid` **only when paid access is actually confirmed**, and set `NEWSDATA_PAGE_SIZE=50` to use the larger supported page size. An explicit size of 10 otherwise remains effective. The plan flag does not grant entitlement or remove a provider-side delay. **The user intends to subscribe to Basic, but activation has not been confirmed in this record. No plan upgrade or key change has been performed by the application repair.** Do not weaken the freshness window or relabel delayed articles as current.

NewsData's [official pricing guide](https://newsdata.io/blog/pricing-plan-in-newsdata-io/) lists real-time access and 50 articles per request for Basic. All seven current queries fit its 100-character query limit. The application's existing 200-credit daily ledger, 15-minute refresh interval and provider cooldowns remain applicable after an upgrade; they are not reset by a key or configuration change. A qualifying-article count of 100 is never guaranteed.

The newly generated Arabic PDF's SHA-256 is `5985fcdc52cdf6ea1cfec4dcad90e0725091dbc102fab6005ddaf6f40eb7cb4d`. Its payload retains `previous_report_id=rpt_54a8eaf1388c46aa965cf167e88a05b6`. The live correction is separate from the 13 unchanged archive PDFs verified earlier.

## Additional live workflow checks and follow-up findings

The dashboard's automatic Iran refresh completed without another refresh click at **18:56:59.211079004 UTC**. It made one provider request, retrieved 10 raw records, and accepted zero. Returned publication times ranged from **06:51:23 to 06:55:33 UTC**; all were outside the strict window. The Arabic UI updated its last-successful and next-refresh times. Switching to an older English in-memory snapshot subsequently exposed an activation delay: the client scheduled an already-due cache check behind its normal 60-second timer floor. The follow-up checks an expired client snapshot immediately on activation through the existing server cache/provider gates. It does not force an extra NewsData refresh.

At **18:55:04–18:55:07 UTC**, one isolated session submitted each of the other five news profiles with its actual English default topic. All five completed and produced valid one-page PDFs, with zero qualifying articles and the exact existing cache snapshots reused. The successful report IDs were `rpt_e2ea263502a44748967c83cefae173ce` (UBI), `rpt_5aa2bfd7eeb34be68f7b4ebaadbc7e5b` (UAE–EU trade), `rpt_bee7dc438a4b4919999c336825be0a1f` (education), `rpt_f0cea6951f734afe8ff558b46144b09e` (economic/cultural), and `rpt_74a29819cee74fac82c067bd46f0474c` (decision briefing). These remain empty-result workflow checks, not evidence of fresh live coverage.

The sixth submission, Story Moodboard job `job_3abc5948a66742709ccadcc443ee66c1`, failed at **18:55:47.959619 UTC** during `analyzing`, after discovery and source checking. Render diagnostics identified HTTP 400, `invalid_json_schema`, parameter `text.format.schema`, and provider request reference `req_bfb32d0ba61b41be885a831855028070`. The original provider validation prose and source-dependent schema were not retained, so its precise rejected condition is not established. Three bounded, tool-free schema probes (zero, one, and 40 synthetic references, each capped at 16 output tokens) were accepted; none reproduced the live rejection. This does not justify claiming a schema-limit cause or weakening source validation. The follow-up retains allowlisted schema-path/reason/keyword diagnostics to identify a recurrence without logging provider prose, source text, enum values, or credentials. Story still requires a fresh normal-workflow verification after deployment.

The original PDF-only follow-up head `31eeaee60f2d1dead7ed724cb2b0f8119458f69a` passed hosted CI run **39478903271**: **1,043 Python tests** passed in 52.39 seconds, and the JavaScript, Blueprint and source-package gates passed. Subsequent diagnostic and activation changes require their own exact-head CI gate; these earlier results must not be relabelled as validation of those additions.

The additional diagnostics passed **84 focused Python tests**, including privacy and provider-error classification checks. The language-activation change passed **44 workspace JavaScript tests** (three new regressions); the unchanged other suites contain 29, 10 and 13 tests, for an expected total of 96. Syntax and scoped whitespace checks passed. The workspace script cache query is `2.0.4.1`; the application version remains `2.0.4`.

## First follow-up deployment and newly reproduced failures

The first main-branch CI run for `079af325` passed at **19:10:58 UTC**, but Render recorded no deployment or ignored-commit event. Repository, branch, credentials, root directory and build filters were checked; no applicable exclusion or failed check was found. Re-running the existing CI job once produced a second successful completion at **19:24:02 UTC**. Render then created an automatic `new_commit` deployment at **19:24:05.341501 UTC**, which became live at the time recorded above. This establishes recovery of the trigger path, not the precise upstream cause of the missed initial notification. No deployment gate or service setting was weakened.

At **19:25:40.939196 UTC**, the live root HTML and `/static/workspace.js?v=2.0.4.1` were both HTTP 200 and byte-identical to the checked source. The script's SHA-256 was `97e5b39a4a3cca4c6723640c816b687395e63732c6a19a974837b7ed2f3d39d0`.

Two additional live observations motivated the second correction:

- **Expired snapshot during an active refresh.** Global report `rpt_6a73d087aae845bda43f32d8b73a7a35`, job `job_c144193d18f44af68368869d2a9d785b`, was created at **19:26:42.619125 UTC** while a dashboard refresh was active. Its saved feed had `status=loading`, `stale=true`, a cache age of 2,609 seconds and last success at **18:43:12.866147 UTC**. The new global refresh completed at **19:26:43.252969882 UTC**. The report loop had accepted any existing snapshot before the active refresh finished. The correction waits within the existing 90-second budget for an expired snapshot's eligible refresh, including contention for the shared provider lease. A recent valid cache remains reusable; a wait timeout cannot become a successful empty report from an expired snapshot.
- **Story request schema rejection.** A single normal-workflow attempt on the first follow-up, job `job_f19175d9c825426aab1b7784a49b1fdc`, failed at **19:29:02.917539 UTC** during `analyzing`, with HTTP 400 `invalid_json_schema`. Its request reference was `req_df22d70800d04b8e8ad595c8924f81a0`. New safe diagnostics localized the rejected schema to `properties.items.items.properties.evidence_quote`. The precise provider validation prose remains unavailable, so no particular enum-size or character-limit explanation is claimed. The correction removes the dynamic publisher-text enum, retains bounded string structure and the existing exact per-source quote membership check on the server, and removes the unused complex news-item schema when no fresh item can be produced. Source-ID, attribution, section and visual-reference validation remain in place.

The most recent provider checks in this record still accepted **zero articles**. Iran's automatic refresh at **19:26:59.637505759 UTC** returned 10 records published **07:21:38–07:24:39 UTC**. Global's refresh at **19:26:43.252969882 UTC** returned 10 records published **07:24:15–07:26:00 UTC**. Each completed successfully with `window_exhausted`; all returned records were outside the required 12-hour window.

The combined second-correction source passed **1,083 Python tests in 40.46 seconds**, all **96 JavaScript checks**, scoped whitespace checks and validation against the official Render Blueprint schema. Focused evidence includes 53 feed tests, 19 news-report tests, 12 offline integration tests, 120 native-research tests and 84 diagnostic tests. The full suite reports one upstream TestClient deprecation warning. These local results precede the exact-head hosted CI gate and deployed verification; they do not establish live Story success or post-upgrade fresh-news coverage.
