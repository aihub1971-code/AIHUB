# AI HUB 2.0.2 — report failures, conversation history and news coverage

Prepared on 20 October 2026 from the deployed 2.0.1 source. This note records source changes and their verification limits; a version number or a successful health check is not proof of complete live news coverage.

## Production findings

The existing Render service was running 2.0.1 successfully. Recent failed report jobs all reported `daily_report_limit`: the configured daily allowance of 50 attempts had been exhausted. The interface discarded the job error code and invited another attempt. No deployment or quota reset could correct that message.

A separate saved-conversation selector lacked an explicit empty value on its translated placeholder. Production consequently received a request with the Arabic placeholder text as the conversation ID and returned 404.

The latest saved Iran research run discovered and inspected 40 candidates but accepted none. Its diagnostics contained 12 unavailable publishers, 7 pages with insufficient text, 6 outside the frozen window, 6 rejected as irrelevant, 3 oversized pages, 3 unsupported content types, 2 unverified publication timestamps and 1 ambiguous article metadata result. The discovery used three native actions and contained many background/older pages. A direct publisher recheck confirmed that its current Le Monde candidate was rejected as irrelevant. This does not establish that the candidate meets every other verification check.

## Changes

- Failed report and fresh-chat research jobs preserve their error codes. The public interface explains the daily report limit and the next midnight reset in Dubai, in Arabic and English. Daily/monthly service allowances have distinct messages without cost data. Saved reports and PDFs remain accessible.
- Conversation placeholders have an explicit empty value. Only valid conversation IDs present in the returned history can be selected. Invalid persisted placeholder values are cleared before a request.
- Current-news research reserves two discovery stages within the existing six-action ceiling. The first uses up to three actions and initially verifies up to 24 publisher pages. If it completes without verified evidence, a final focused stage can use up to three actions, informed by rejection counts and previous queries. Provider errors stop execution; they do not trigger retries.
- The combined research attempt remains bounded to 80 discovered URLs, 40 unique publisher inspections, the exact frozen 12-hour window and at most 20 verified/deduplicated news items. Inspections are cached within the attempt. Both discovery stages are reserved and accounted for before execution.
- Cited and opened articles are considered ahead of background search overflow. Iran relevance recognizes fuel, crude, Brent, shipping and attack terminology while retaining the requirement for an explicit Iran/conflict connection in the publisher headline or description.
- PDFs keep six palette colours in one equal-width row. Colour codes embedded in Arabic paragraphs and tables preserve their leading hash and left-to-right order. Saved production PDFs are immutable; the renderer correction applies to newly prepared PDFs.

## Verification and remaining limits

Focused interface, research and rendered-PDF regressions exercise the confirmed failures. The saved Arabic moodboard was rendered locally without a new research request; the affected pages were visually inspected, and its source links were preserved. The combined source passed all **834 Python tests and 76 JavaScript interface tests**, Python/JavaScript syntax checks and the official Render Blueprint schema. Hosted CI results are recorded in the release pull request. The local API suite required the normal execution environment because the restricted sandbox blocked its local async test client; provider HTTP stayed disabled in the fixtures.

The daily report allowance and its accounting ledger are unchanged. After this patch, new reports still require available allowance. The 20 October allowance resets at **00:00 Dubai on 21 October 2026**, equivalent to **20:00 UTC on 20 October**. No further paid live report is included in this patch's acceptance checks.

The research changes are verified against controlled fixtures. A successful new live Iran report remains necessary to establish improved coverage; do not describe Iran news coverage, every assistant, or every publisher as fully verified. Historical/creative source-title descriptions remain a separate small polish item. Existing saved report contents and PDF hashes must not be silently rewritten.
