# AI HUB 2.0.3 — 100 daily report attempts

Prepared on 20 October 2026 following the owner's request to raise the daily report allowance from 50 to **100 attempts**. This is an allowance change; it does not establish improved Iran news coverage or a successful new live research run.

## Changes

- `MAX_REPORTS_PER_DAY=100` is the configured value in `render.yaml` and `.env.example`. The application accepts the higher allowance; if the variable is absent, the fallback remains **50**.
- The existing attempt ledger is retained. Attempts already made during the current Dubai calendar day still count, including unsuccessful attempts. Increasing the allowance does not clear records, reset usage, or regenerate stored reports.
- The version and asset cache references identify 2.0.3. The capability text says “Up to 100 report attempts a day” in English and Arabic, without promising 100 completed reports.

## Unchanged limits

`DAILY_BUDGET_USD=900` and `MONTHLY_BUDGET_USD=28000` remain unchanged. Research and chat continue to share those budgets, so an available report slot does not override a budget check. Reservation, reconciliation, idempotency and ownership checks remain in place.

The separate cap of **50 active daily schedule times** remains unchanged, and `SCHEDULER_ENABLED=false` remains the configured setting. Scheduled report attempts, if scheduling is enabled later, share the report allowance with manual attempts.

The research bounds remain **20 news items after verification and deduplication**, an exact frozen **12-hour publication window**, and at most **six native research actions**. The staged discovery and interface fixes from [2.0.2](FIXES_2_0_2.md) remain in place. No provider error is automatically retried.

## Verification and release status

The focused storage and scheduler suite passed **33 tests**. The new checks preserve the first 50 accepted attempts in the same ledger, admit exactly 50 more under concurrent requests, reject the next attempt at 100, retain budget accounting and idempotency, and keep schedule slots capped at 50. These checks use isolated synthetic records and make no paid provider calls.

This note records the requested source/configuration change. Release checks must verify the configured allowance and the preservation of previous attempts, then confirm the deployed version through `/health`. Updating the source files alone does not change an existing service's environment value.

Earlier 2.0.1 and 2.0.2 test and live-report records remain historical evidence for those versions. Deployment results and any separately authorized new live research results belong in [the validation record](VALIDATION_AR.md); they must not be inferred from the allowance increase.
