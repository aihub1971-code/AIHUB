"""Check a running AI HUB service. Provider calls require the explicit --live option."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from urllib.parse import urlparse
import uuid

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--live", action="store_true", help="Request one potentially billable report.")
    parser.add_argument("--agent", default="iran-news")
    parser.add_argument("--request-id", help="Reuse this same ID only when repeating the same live task.")
    parser.add_argument("--task", default="أعد تقريرًا عربيًا بأهم 6 أخبار موثقة في آخر 24 ساعة.")
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
        with httpx.Client(base_url=base, timeout=180, follow_redirects=False) as client:
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
                print("Live request: completed. Review the saved report, sources and usage in AI HUB.")
                if result.get("report_id"):
                    print(f"Report ID: {result['report_id']}")
            else:
                print("No news search or model-generation call was requested.")
    except httpx.HTTPStatusError as exc:
        print(f"Service check failed with HTTP {exc.response.status_code}.", file=sys.stderr)
        try:
            detail = exc.response.json().get("detail")
            if isinstance(detail, dict) and isinstance(detail.get("code"), str):
                print(f"Application code: {detail['code']}", file=sys.stderr)
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
