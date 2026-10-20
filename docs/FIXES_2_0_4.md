# AI HUB 2.0.4 — live news, report recovery and active assistants

Updated on **20 October 2026**. **PRs #15–#18 are deployed.** NewsData now returns current articles, and new Arabic Iran and English global reports complete with analysis and downloadable PDFs. Story Moodboard also completes after its schema correction. Live inspection confirms the Arabic numeric-range repair and stronger global filtering; two remaining rhetorical/incidental topic matches are addressed in the next narrowly scoped source correction below. Its own deployed verification remains separate from the successful earlier checks. Service-wide paid-plan/page-size configuration still awaits operator confirmation; a bounded, credit-accounted size-50 capability check succeeded without changing the service environment.

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

The provider checks before PR #17 still accepted **zero articles**. Iran's automatic refresh at **19:26:59.637505759 UTC** returned 10 records published **07:21:38–07:24:39 UTC**. Global's refresh at **19:26:43.252969882 UTC** returned 10 records published **07:24:15–07:26:00 UTC**. Each completed successfully with `window_exhausted`; all returned records were outside the required 12-hour window. Later positive results are recorded below.

The combined second-correction source passed **1,083 Python tests in 40.46 seconds**, all **96 JavaScript checks**, scoped whitespace checks and validation against the official Render Blueprint schema. Focused evidence includes 53 feed tests, 19 news-report tests, 12 offline integration tests, 120 native-research tests and 84 diagnostic tests. The full suite reports one upstream TestClient deprecation warning. These local results precede the exact-head hosted CI gate and deployed verification; they do not establish live Story success or post-upgrade fresh-news coverage.

## PR #17 deployment and positive live results

Main commit `1f2ed2097e406cf7783cee9f3265f5695e86eb3f` passed hosted CI run **39487285400** at **19:55:00 UTC**. Render automatically created deployment `dep-dbbse66k1f9s73d1jb8g` at **19:55:04.510829 UTC** and marked that exact commit live at **19:56:04.241141 UTC**. No trigger retry or manual deployment was needed.

NewsData delivery changed during these checks. The existing key returned current articles; the account's named subscription tier was not independently inspected.

| Feed refresh | Accepted by the then-current filter | Raw / requests | Accepted publication range, UTC |
| --- | ---: | ---: | --- |
| Iran, 19:57:04.531948 UTC | 46 | 200 / 20 | 18:22:43–19:41:58 |
| Global, 19:57:11.020607 UTC | 79 | 200 / 20 | 19:09:32–19:51:36 |

Both feeds were inside the 12-hour window, had no provider error, and contained no duplicate IDs, normalized URLs or normalized original headlines. These uniqueness checks do not mean every article describes a different event. Both stopped at the 20-request ceiling with page size 10 and disclosed partial retrieval. The 79-item global inventory subsequently exposed the relevance defects below; its count must not be represented as independently confirmed geopolitical relevance.

- **Iran Arabic:** report `rpt_cb84d950849c47dd936107d1b50f6fa2`, job `job_156769b418684ec1b8a7cf70df3db851`, was created at **19:59:25.058869 UTC** and PDF-ready at **19:59:37.486814 UTC**. Its frozen window was **07:57:11.788809–19:57:11.788809 UTC**. It analyzed all **46** articles from completed snapshot `feed_e533d112da594508b038b730da503193`, with `stale=false` and cache age 7 seconds. All 24 English-source articles were translated into Arabic; 22 were already Arabic. Its PDF contains 24 pages, 206,392 bytes and all 46 original source URLs in 228 clickable annotations. SHA-256: `f840a83b05f991f4f5c35b7149158115207cd7086d9f309318fdcf010be4ef38`. The previous report ID remains recorded.
- **Global English:** report `rpt_85a12ed8ecab4e3980c0af022dfddab1` completed with **79** analyzed articles and a downloaded 260,842-byte PDF. Its analysis distinguishes attribution and uncertainty. The inventory's observed topic false positives require the further filter correction before final relevance acceptance.
- **Story English:** job `job_d9b133d23b0e4f97a9b2a0c3b2f837ef`, report `rpt_3fe62f06cbde40a8923b5c2da852c9a9`, completed at **19:58:55.220831 UTC** after one submission. Its six-page, 54,603-byte PDF includes eight sections, five palette entries and 15 clickable annotations covering all nine reference URLs. The prior `invalid_json_schema` failure did not recur. One CJK reference title/publisher is missing glyphs in the PDF, although the original metadata and clickable URL remain available; the English report body is readable.

At **20:01:31.316136 UTC**, a bounded education-feed refresh tested size **50** through the existing provider claim, quota ledger, deduplication and timestamp checks. It retrieved **250** raw records in **five** requests and reached **100** accepted articles, stopping at `article_limit` without a provider error. Only that diagnostic process used the candidate page settings; the service environment remained `NEWSDATA_PLAN=unknown`, `NEWSDATA_PAGE_SIZE=10`, and the shared daily allowance remained 200. Persistent `paid`/`50` configuration awaits operator confirmation and a subsequent deployment; a capability test does not identify the account's billing plan.

## Follow-up from positive-result inspection

The global filter's broad `election` and `conflict` matches admitted local party-zoning news and an Arabic entertainment article about AI and Hollywood. They were fresh publications but outside the intended major geopolitical scope. The correction requires concrete security/diplomacy, strategic economic consequences, or major national institutional changes, without a country whitelist. Narrow local/entertainment exclusions distinguish literal geopolitical events from metaphorical conflict. Existing global cache entries are revalidated using original headlines and summaries; excluded items no longer contribute to displayed or analyzed counts. This read-time filtering preserves snapshot identity, refresh times, quota accounting, cooldowns and saved report archives. Relevance exclusions are reported separately from articles leaving the rolling coverage window.

The Arabic PDF correctly displayed the report identity, counts and ISO timestamps, but an actual executive-summary percentage range `80–100%` appeared in reversed order. The correction isolates complete numeric ranges and percentages without changing dates, URLs or existing directional isolates. The 23-case PDF suite covers Western and Arabic-Indic digits, signs, decimals, thousands separators and table cells. Offline regeneration of the actual saved 46-source Iran report remains 24 pages, visibly preserves the `80–100%` endpoint order, and retains all 46 original source URLs. The saved live PDF is retained as reproduction evidence; this corrected local render does not count as a new deployed PDF.

Automatic refresh was separately observed on the live Arabic dashboard at approximately **20:15 UTC**: the Iran feed updated without a manual refresh from 46 to **42** qualifying articles, showed a successful refresh at **20:12 UTC**, and displayed the next scheduled refresh at **20:26 UTC**. Fresh source-language items are explicitly marked when a matching cached translation is not yet available; report generation translates the full accepted inventory and subsequent cache reads reuse those translations.

An attempted persistent change to `NEWSDATA_PLAN=paid` and `NEWSDATA_PAGE_SIZE=50` was rejected by automatic approval review because subscription activation had not been confirmed. The rejection also noted that saving configuration can restart or redeploy the service. No alternate configuration path was used. The remaining action is for the operator to confirm the Basic subscription is active and authorize those two settings; the existing API key need not be disclosed in chat.

The final correction passed **134 focused feed/report/integration tests** (103 feed, 19 report and 12 integration) plus **23 PDF tests**. Fifty new relevance/cache cases cover the reported false positives and preserve major events at ordinary venues, national leadership changes, diplomatic actors, and the distinction between the Arabic Security Council and a board of trustees. Re-evaluating the saved 79-item global inventory locally retains 24 and excludes 55; this is an offline relevance check, not a new provider refresh or deployed report. Hosted full-suite CI and exact-commit deployment verification follow this source checkpoint.

## PR #18 deployment and final topic-boundary inspection

PR #18 passed **1,136 Python tests**, **96 JavaScript tests**, official Render Blueprint validation and clean source packaging. Its merged commit `62f185479fbf4883198a1509690401f702af7601` passed main CI run **39491161171** at **20:21:47 UTC** and became live in automatic Render deployment `dep-dbbsqnajnfac738bgd7g` at **20:22:43.854294 UTC**.

- New Arabic Iran report `rpt_1f9ac548f05348d68c8b4c949922a2b0` (job `job_6c833f337e534125a0de502b2e112f85`) was created at **20:24:41.332762 UTC** and PDF-ready at **20:24:52.965294 UTC**. It analyzed 42 articles from completed, non-stale snapshot `feed_2173ded5b587450786a16571ebaa98f3`; all 23 English items were translated and 19 were originally Arabic. The 22-page, 193,093-byte downloaded PDF retains all 42 URLs in 211 clickable annotations. Visual inspection of pages 1, 2, 6, 13, 17 and 22 confirms readable Arabic, correct counts, source ordering and `80–100%` endpoint order. Its SHA-256 is `e63659421dc471e8e3d2877169b56f1641da17945a1e15b81b5209933516c477`.
- New English global report `rpt_bd94c0f534ab463fb1e51f08e103fd39` (job `job_dd43ffb6e15b406b853c48a941927b7d`) was created at **20:27:09.624497 UTC** and PDF-ready at **20:27:15.580401 UTC**, with 18 articles analyzed and a 12-page, 126,613-byte PDF. The previous 73-item cache had excluded 55 items on read without another provider request.

Live review nevertheless exposed two narrow topic-boundary escapes: a political legal-fee story used “goes to war” figuratively while mentioning a military base, and an Iran-feed item focused on the Ukraine war with Iran appearing only in a closing oil-price aside. These saved reports remain workflow and PDF evidence, not final acceptance of every item's topic relevance. The next focused correction rejects rhetorical-conflict matches without substantive security/diplomatic context, requires Iranian focus in the headline or opening summary, and applies the same original-text revalidation to cached Iran items. It does not alter timestamps, provider limits, saved reports or service configuration.

The focused topic-boundary correction passed **154 tests** (123 feed, 19 report and 12 integration), including **20 new regression cases**. Both exact saved false positives are rejected in offline evaluation. Literal declared war, military operations, substantive diplomatic disputes and clear Iran-related oil/shipping consequences remain eligible. Iran focus and conflict context are checked against the headline and the first 400 summary characters. Cached Iran and global views use original provider text for revalidation; saved reports and quota state are unchanged. These are pre-deployment results; exact-commit CI, Render verification and final new reports follow this checkpoint.
