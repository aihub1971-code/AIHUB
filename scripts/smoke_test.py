"""Check a running AI HUB service. Provider calls require the explicit --live option."""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlparse
import uuid

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _safe_label(value: object) -> str:
    """Display only short metadata identifiers, never arbitrary response text."""
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", value):
        return value
    return "unknown"


def _count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 1_000_000 else None


def _usd(value: object) -> str:
    if type(value) not in (int, float, str):
        return "unknown"
    try:
        amount = Decimal(str(value))
        if amount.is_finite() and 0 <= amount <= Decimal("1000000000000"):
            return f"{amount:.6f}"
    except InvalidOperation:
        pass
    return "unknown"


def _print_live_summary(result: dict) -> None:
    print("Live request: HTTP succeeded.")
    print(f"Report ID: {_safe_label(result.get('report_id'))}")
    print(f"Provider/model: {_safe_label(result.get('provider'))} / {_safe_label(result.get('model'))}")
    model_calls = _count(result.get("model_call_count"))
    print(f"Model calls: {model_calls if model_calls is not None else 'unknown'}")
    search = result.get("web_search")
    search = search if isinstance(search, dict) else {}
    counts = {
        "requests": _count(search.get("request_count")),
        "tool_calls": _count(search.get("tool_call_count")),
        "search_calls": _count(search.get("search_call_count")),
    }
    count_text = "; ".join(f"{name}={value if value is not None else 'unknown'}" for name, value in counts.items())
    print(f"OpenAI web search: status={_safe_label(search.get('status'))}; {count_text}.")
    count, target = _count(result.get("item_count")), _count(result.get("target_count"))
    if count is None:
        print("News item count: not reported for this response. Review the saved output.")
    else:
        if target is None:
            print(f"News items: {count}; coverage=open; status={_safe_label(result.get('status'))}.")
            limit = _count(result.get("bounded_item_limit"))
            if limit is not None:
                print(f"Report item limit: {limit} (a processing ceiling, not a target).")
        else:
            print(f"News items: {count}/{target} explicitly requested; status={_safe_label(result.get('status'))}.")
        fresh = _count(result.get("fresh_event_count"))
        if fresh is not None:
            print(f"Fresh-event evidence count: {fresh} (requires source/date review).")
        if count == 0:
            print("Report quality: NO NEWS ACCEPTED. HTTP success does not establish a usable news report.")
        elif target is None:
            print("Report quality: open coverage with no fixed count target; review source evidence, dates and relevance.")
        elif count < target:
            print("Report quality: PARTIAL against the explicitly requested count. Review the shortfall and sources.")
        else:
            print("Report quality: target count reached; evidence, dates and relevance still require review.")
    accounting = result.get("accounting")
    accounting = accounting if isinstance(accounting, dict) else {}
    print(
        f"Accounted estimate: USD {_usd(accounting.get('estimated_cost_usd'))}; "
        f"basis={_safe_label(accounting.get('cost_basis'))}. This is not an invoice."
    )
    if result.get("idempotency_replayed") is True:
        print("Existing report reused with the same request ID; no new run was created.")
    print("Review the saved report, source links, dates and usage in AI HUB before enabling schedules.")


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--live", action="store_true", help="Request one potentially billable report.")
    parser.add_argument("--agent", default="iran-news")
    parser.add_argument("--request-id", help="Reuse this same ID only when repeating the same live task.")
    parser.add_argument("--task", default="أعد تقريرًا عربيًا بالمستجدات الموثقة في آخر 24 ساعة من المصادر المعتمدة، بعدد الأخبار الذي تدعمه الأدلة المتاحة.")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        parser.error("Use a plain http(s) service URL without embedded credentials.")
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("HTTPS is required when sending owner credentials to a remote service.")
    username = os.environ.get("APP_USERNAME", "")
    password = os.environ.get("APP_PASSWORD", "")
    if not username or not password:
        parser.error("Set APP_USERNAME and APP_PASSWORD in your private environment or .env.")
    try:
        # A live run can include two sequential 90-second OpenAI reads and connections.
        with httpx.Client(base_url=base, timeout=360 if args.live else 180, follow_redirects=False) as client:
            response = client.get("/health")
            response.raise_for_status()
            print("Health check: passed.")
            response = client.get("/v1/agents", auth=(username, password))
            response.raise_for_status()
            agents = response.json().get("agents", [])
            print(f"Owner authentication: passed. Registered assistants: {len(agents)}.")
            if len(agents) != 13:
                print("Expected 13 registered assistants.", file=sys.stderr)
                return 1
            if args.live:
                request_id = args.request_id or f"smoke-{uuid.uuid4()}"
                print("Submitting one live report; provider usage may be billed.")
                print(f"Request ID: {request_id}")
                response = client.post(
                    f"/v1/agents/{args.agent}/run",
                    auth=(username, password),
                    headers={"Idempotency-Key": request_id},
                    json={"task": args.task},
                )
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError("Unexpected report response shape.")
                _print_live_summary(result)
            else:
                print("No news search or model-generation call was requested.")
    except httpx.HTTPStatusError as exc:
        print(f"Service check failed with HTTP {exc.response.status_code}.", file=sys.stderr)
        try:
            detail = exc.response.json().get("detail")
            if isinstance(detail, dict) and isinstance(detail.get("code"), str):
                print(f"Application code: {_safe_label(detail['code'])}", file=sys.stderr)
            if isinstance(detail, dict):
                provider_status = detail.get("provider_status")
                if type(provider_status) is int and 400 <= provider_status <= 599:
                    print(f"OpenAI HTTP status: {provider_status}", file=sys.stderr)
        except (ValueError, AttributeError):
            pass
        return 1
    except (httpx.HTTPError, ValueError):
        print("Service connection or JSON response failed. Check the URL and server logs.", file=sys.stderr)
        print("Before retrying a live request, check stored reports for a completed run.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
