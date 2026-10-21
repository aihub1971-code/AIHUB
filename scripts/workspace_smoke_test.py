"""Validate a deployed AI HUB 2.x public workspace without owner credentials.

Only --live can submit potentially billable research. Basic and --verify-saved
modes use GET requests only. Reuse the same output directory and cookie file to
resume an interrupted validation batch; use a new output directory to request a
new batch deliberately. Request keys are saved before the first POST.

Examples:
  python scripts/workspace_smoke_test.py --base-url https://SERVICE.onrender.com
  python scripts/workspace_smoke_test.py --base-url https://SERVICE.onrender.com --live --all-primary
  python scripts/workspace_smoke_test.py --base-url https://SERVICE.onrender.com --verify-saved

Artifacts and cookies are private (0600 files, 0700 output directory). Console
output contains only IDs, stages/status codes, counts, PDF page metadata and
elapsed time. These checks establish API, persistence and publication-metadata
invariants; they do not establish the truth or editorial quality of every claim.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
from http.cookiejar import MozillaCookieJar
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


PREFIX = "/v1/public/workspace"
COOKIE = "aihub_public_session"
EXPECTED_AGENT_IDS = {"iran-news", "geopolitical-morning", "ubi-research", "uae-eu-trade-talks",
    "education-observatory", "economic-cultural-briefing", "historical-reading-companion"}
MAX_NEWS_ITEMS = 50
MAX_JSON_BYTES, MAX_PDF_BYTES = 5_000_000, 50_000_000
ID = re.compile(r"^(?:job|rpt)_[a-f0-9]{32}$")
PRIVATE = {"api_key", "owner_id", "usage", "usage_stages", "accounting", "model", "pricing_snapshot",
           "estimated_cost_usd", "reserved_cost_usd", "cost_tracking_complete", "lease_token", "pdf_path"}
STAGES = {"queued", "searching", "checking_sources", "analyzing", "preparing_pdf", "ready"}
PRINT_LOCK = threading.Lock()


class CheckError(Exception):
    def __init__(self, code: str):
        self.code = code if re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", code) else "validation_failed"
        super().__init__(self.code)


def require(condition: object, code: str) -> None:
    if not condition:
        raise CheckError(code)


def emit(*, status: str, agent=None, job=None, report=None, count=None, pages=None, timing=None) -> None:
    values = {"agent": agent, "job": job, "report": report, "status": status, "count": count, "pages": pages, "timing": timing}
    safe = {key: value for key, value in values.items() if value is not None and
            (type(value) in (int, float) or isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", value))}
    with PRINT_LOCK:
        print(json.dumps(safe, sort_keys=True), flush=True)


def write_private(path: Path, content: bytes) -> None:
    require(not path.is_symlink(), "unsafe_output_file")
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def timestamp(value: object) -> datetime:
    require(isinstance(value, str) and len(value) <= 150, "invalid_timestamp")
    raw = value.strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"))
    try:
        try:
            result = datetime.fromisoformat(raw.replace("Z", "+00:00").replace("z", "+00:00"))
        except ValueError:
            result = parsedate_to_datetime(raw.replace(" UTC", " GMT"))
        require(result.tzinfo is not None and result.utcoffset() is not None, "timestamp_requires_timezone")
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise CheckError("invalid_timestamp") from None


def https_url(value: object) -> str:
    require(isinstance(value, str) and not re.search(r"[\x00-\x20\x7f\\]", value), "invalid_source_url")
    try:
        parsed = urlsplit(value)
        require(parsed.scheme == "https" and parsed.hostname and "." in parsed.hostname and not parsed.username
                and not parsed.password and parsed.port in (None, 443), "source_url_requires_https")
        require(not re.fullmatch(r"[\d.]+", parsed.hostname) and not parsed.hostname.endswith((".local", ".internal", ".localhost")),
                "invalid_source_host")
    except ValueError:
        raise CheckError("invalid_source_url") from None
    return value


def assert_public(value: object) -> None:
    if isinstance(value, dict):
        require(not PRIVATE.intersection(value) and not any(key.startswith("_") for key in value), "private_public_response")
        for child in value.values():
            assert_public(child)
    elif isinstance(value, list):
        for child in value:
            assert_public(child)


def validate_report(record: dict, entry: dict) -> tuple[int, int | None]:
    assert_public(record)
    require(record.get("agent_id") == entry["agent_id"] and record.get("language") == entry["language"], "report_scope_mismatch")
    require(ID.fullmatch(record.get("report_id", "")) and record["report_id"].startswith("rpt_"), "invalid_report_id")
    require(record.get("job_id") == entry["job_id"], "report_job_mismatch")
    result = record.get("result")
    require(isinstance(result, dict) and result.get("schema_version") == 2, "invalid_report_schema")
    require(result.get("agent_id") == entry["agent_id"] and result.get("language") == entry["language"], "report_result_scope_mismatch")
    window = result.get("research_window", {})
    start, end = timestamp(window.get("start")), timestamp(window.get("end"))
    require(end - start == timedelta(hours=12), "research_window_not_twelve_hours")
    require(timestamp(entry["research_window"]["start"]) == start and timestamp(entry["research_window"]["end"]) == end,
            "research_window_changed")
    require(timestamp(record.get("research_cutoff")) == end, "report_cutoff_changed")
    items, sources = result.get("items"), result.get("sources")
    require(isinstance(items, list) and len(items) <= MAX_NEWS_ITEMS and isinstance(sources, list)
            and len(sources) <= 100, "invalid_item_or_source_count")
    source_map = {}
    for source in sources:
        require(isinstance(source, dict) and isinstance(source.get("id"), str) and source["id"] not in source_map, "duplicate_source_id")
        https_url(source.get("url"))
        source_map[source["id"]] = source
        if source.get("role") == "fresh":
            published = timestamp(source.get("published_at"))
            require(start <= published <= end, "source_outside_window")
            proof = source.get("publication_verification")
            publisher_proof = (isinstance(proof, dict) and proof.get("verified") is True
                and str(proof.get("method", "")).startswith("publisher_"))
            provider_metadata = (isinstance(proof, dict) and proof.get("method") == "newsdata_pubDate_pubDateTZ"
                and proof.get("provider_metadata_validated") is True and proof.get("verified") is False
                and source.get("publisher_text_verified") is False
                and source.get("evidence_level") == "newsdata_structured_metadata")
            require(publisher_proof or provider_metadata, "missing_publication_evidence")
            require(timestamp(proof.get("value") or proof.get("timestamp") or proof.get("published_at")) == published,
                    "publication_proof_mismatch")
    urls, headlines = set(), set()
    for item in items:
        require(isinstance(item, dict), "invalid_item")
        url, published = https_url(item.get("url")), timestamp(item.get("published_at"))
        require(start <= published <= end, "item_outside_window")
        headline = " ".join(str(item.get("headline", "")).casefold().split())
        require(headline and url not in urls and headline not in headlines, "duplicate_or_empty_item")
        urls.add(url); headlines.add(headline)
        identifiers = item.get("source_ids")
        require(isinstance(identifiers, list) and identifiers and all(identifier in source_map for identifier in identifiers), "unknown_item_source")
        supporting = [source_map[identifier] for identifier in identifiers]
        require(all(source.get("role") == "fresh" for source in supporting), "nonfresh_news_source")
        require(any(source["url"] == url and timestamp(source.get("published_at")) == published for source in supporting),
                "item_publication_source_mismatch")
    mappings = result.get("citation_mappings")
    require(isinstance(mappings, dict), "missing_citation_mappings")
    for identifier, source in source_map.items():
        require(isinstance(mappings.get(identifier), dict) and mappings[identifier].get("url") == source["url"], "citation_mapping_mismatch")
    for section in result.get("sections", []):
        require(isinstance(section, dict) and all(identifier in source_map for identifier in section.get("source_ids", [])), "unknown_section_source")
    pages = record.get("pdf", {}).get("page_count")
    require(pages is None or type(pages) is int and 1 <= pages <= 500, "invalid_pdf_page_metadata")
    return len(items), pages


class Validator:
    def __init__(self, args: argparse.Namespace, *, transport=None):
        self.args, self.transport = args, transport
        self.directory = Path(args.output_dir).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.state_path = self.directory / "validation.json"
        self.session_path = Path(args.session_file).expanduser().resolve() if args.session_file else self.directory / "session.cookies"
        self.session_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {"version": 1, "base_url": args.base_url, "runs": {}}
        require(self.state.get("base_url") == args.base_url and isinstance(self.state.get("runs"), dict), "validation_state_scope_mismatch")
        self.cookies = MozillaCookieJar(str(self.session_path))
        if self.session_path.exists():
            require(not self.session_path.is_symlink(), "unsafe_session_file")
            os.chmod(self.session_path, 0o600)
            self.cookies.load(ignore_discard=True, ignore_expires=True)
        else:
            require(not self.state["runs"], "original_session_file_required")
        self.agents = []

    def client(self, *, stranger=False):
        cookies = MozillaCookieJar()
        if not stranger:
            for cookie in self.cookies:
                cookies.set_cookie(copy.copy(cookie))
        return httpx.Client(base_url=self.args.base_url, headers={"Origin": self.args.base_url},
            cookies=cookies, timeout=httpx.Timeout(30, connect=15),
            follow_redirects=False, trust_env=True, transport=self.transport)

    def save_state(self) -> None:
        write_private(self.state_path, encoded(self.state))

    def update(self, key: str, **values) -> dict:
        with self.lock:
            self.state["runs"][key].update(values)
            self.save_state()
            return copy.deepcopy(self.state["runs"][key])

    def json(self, client, path: str, *, method="GET", body=None, key=None, expected=200) -> dict:
        headers = {"Idempotency-Key": key} if key else None
        response = client.request(method, path, json=body, headers=headers)
        require(len(response.content) <= MAX_JSON_BYTES, "response_too_large")
        try:
            data = response.json()
        except ValueError:
            raise CheckError("invalid_json_response") from None
        if response.status_code != expected:
            code = data.get("detail", {}).get("code") if isinstance(data, dict) and isinstance(data.get("detail"), dict) else None
            raise CheckError(f"http_{response.status_code}/" + (code if isinstance(code, str) else "request_failed"))
        require(isinstance(data, dict), "invalid_json_object")
        assert_public(data)
        return data

    def basic(self) -> None:
        began = time.monotonic()
        with self.client() as client:
            health = self.json(client, "/health")
            require(health.get("status") == "ok" and str(health.get("version", "")).startswith("2."), "workspace_health_version_mismatch")
            self.json(client, "/v1/public/bootstrap")
            token = client.cookies.get(COOKIE)
            require(isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", token), "missing_public_session_cookie")
            session_digest = hashlib.sha256(token.encode()).hexdigest()
            require(not self.state["runs"] or self.state.get("session_digest") == session_digest, "original_public_session_unavailable")
            self.cookies = MozillaCookieJar(str(self.session_path))
            for cookie in client.cookies.jar:
                self.cookies.set_cookie(copy.copy(cookie))
            descriptor, name = tempfile.mkstemp(prefix=".session.", dir=self.session_path.parent)
            os.close(descriptor)
            try:
                self.cookies.save(name, ignore_discard=True, ignore_expires=True)
                write_private(self.session_path, Path(name).read_bytes())
            finally:
                Path(name).unlink(missing_ok=True)
            self.state["session_digest"] = session_digest
            self.save_state()
            config = self.json(client, PREFIX + "/config")
            self.agents = config.get("agents", [])
            require(isinstance(self.agents, list) and len(self.agents) == len(EXPECTED_AGENT_IDS)
                    and all(isinstance(agent, dict) and agent.get("enabled") is True
                        and agent.get("retrieval_options") == ["newsdata", "web_search"]
                        and agent.get("news_feed_enabled") is True for agent in self.agents)
                    and {agent.get("id") for agent in self.agents} == EXPECTED_AGENT_IDS
                    and config.get("agent_count") == len(EXPECTED_AGENT_IDS)
                    and config.get("coverage_hours") == 12 and config.get("maximum_news_items") == MAX_NEWS_ITEMS
                    and config.get("maximum_newsdata_articles_per_refresh") == MAX_NEWS_ITEMS
                    and config.get("refresh_mode") == "manual" and config.get("display_timezone") == "Asia/Dubai",
                    "invalid_workspace_configuration")
            require(client.get("/v1/usage").status_code == 401, "unauthenticated_admin_not_rejected")
            write_private(self.directory / "public_config.json", encoded(config))
        existing = next((entry for entry in self.state["runs"].values() if entry.get("report_id")), None)
        self.check_foreign(existing)
        emit(status="basic_verified" if existing else "basic_verified_missing_id_probe", count=len(self.agents), timing=round(time.monotonic() - began, 2))

    def check_foreign(self, entry: dict | None) -> None:
        with self.client(stranger=True) as client:
            self.json(client, "/v1/public/bootstrap")
            report_id = entry["report_id"] if entry else "rpt_" + "0" * 32
            paths = [PREFIX + "/reports/" + report_id, PREFIX + "/reports/" + report_id + "/pdf"]
            if entry and entry.get("job_id"):
                paths.append(PREFIX + "/jobs/" + entry["job_id"])
            require(all(client.get(path).status_code == 404 for path in paths), "cross_session_isolation_failed")

    def selected(self) -> list[dict]:
        if self.args.all_primary:
            selected = list(self.agents)
        elif self.args.agent:
            by_id = {agent["id"]: agent for agent in self.agents}
            require(all(identifier in by_id for identifier in self.args.agent), "unknown_agent_selection")
            selected = [by_id[identifier] for identifier in dict.fromkeys(self.args.agent)]
        else:
            selected = self.agents[:1]
        require(all(agent.get("enabled") and re.fullmatch(r"[a-z0-9-]{1,80}", agent["id"]) for agent in selected), "invalid_agent_selection")
        return selected

    def prepare(self, profile: dict) -> str:
        key = profile["id"] + ":" + self.args.language
        with self.lock:
            if key not in self.state["runs"]:
                topic = profile.get("default_topic_" + self.args.language)
                require(isinstance(topic, str) and 1 <= len(topic) <= 8000, "missing_default_topic")
                self.state["runs"][key] = {"agent_id": profile["id"], "language": self.args.language,
                    "topic": topic, "request_key": "workspace-smoke-" + uuid4().hex, "status": "prepared"}
                self.save_state()  # Must commit before any potentially billable POST.
        return key

    def capture(self, client, key: str, record: dict, *, verify=False, download=True) -> dict:
        entry = self.state["runs"][key]
        count, pages = validate_report(record, entry)
        payload_digest = digest(record["result"])
        require(not entry.get("payload_sha256") or entry["payload_sha256"] == payload_digest, "saved_report_payload_changed")
        report_id = record["report_id"]
        write_private(self.directory / (report_id + ".json"), encoded(record))
        entry = self.update(key, report_id=report_id, payload_sha256=payload_digest, count=count, pages=pages)
        if download and record.get("pdf", {}).get("status") == "ready":
            with client.stream("GET", PREFIX + "/reports/" + report_id + "/pdf") as response:
                require(response.status_code == 200 and response.headers.get("content-type", "").split(";", 1)[0] == "application/pdf", "pdf_download_failed")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    require(size <= MAX_PDF_BYTES, "pdf_too_large")
                    chunks.append(chunk)
            data = b"".join(chunks)
            require(data.startswith(b"%PDF-") and len(data) == record["pdf"].get("size_bytes") and pages is not None, "invalid_pdf_bytes_or_size")
            pdf_digest = hashlib.sha256(data).hexdigest()
            require(not verify or not entry.get("pdf_sha256") or entry["pdf_sha256"] == pdf_digest, "saved_pdf_changed")
            write_private(self.directory / (report_id + ".pdf"), data)
            entry = self.update(key, pdf_sha256=pdf_digest)
        return entry

    def run_one(self, key: str, *, verify=False) -> bool:
        began, last_stage = time.monotonic(), None
        entry = copy.deepcopy(self.state["runs"][key])
        try:
            with self.client() as client:
                if not verify and not entry.get("duplicate_verified"):
                    body = {"topic": entry["topic"], "language": entry["language"]}
                    first = self.json(client, PREFIX + "/agents/" + entry["agent_id"] + "/reports",
                        method="POST", body=body, key=entry["request_key"], expected=202)
                    job = first.get("job", {})
                    require(ID.fullmatch(job.get("job_id", "")) and job["job_id"].startswith("job_"), "invalid_job_id")
                    require(not entry.get("job_id") or entry["job_id"] == job["job_id"], "saved_job_identity_changed")
                    entry = self.update(key, job_id=job["job_id"], research_window=job["research_window"])
                    duplicate = self.json(client, PREFIX + "/agents/" + entry["agent_id"] + "/reports",
                        method="POST", body=body, key=entry["request_key"], expected=202)
                    require(duplicate.get("joined_existing") is True and duplicate.get("job", {}).get("job_id") == job["job_id"], "duplicate_submission_created_job")
                    entry = self.update(key, duplicate_verified=True)
                require(entry.get("job_id"), "no_saved_job_to_verify")
                while time.monotonic() - began <= self.args.timeout:
                    update = self.json(client, PREFIX + "/jobs/" + entry["job_id"])
                    job = update.get("job", {})
                    require(job.get("job_id") == entry["job_id"] and job.get("agent_id") == entry["agent_id"], "job_snapshot_mismatch")
                    require(job.get("research_window") == entry["research_window"], "job_window_changed")
                    require(job.get("stage") in STAGES, "invalid_job_stage")
                    if update.get("report"):
                        entry = self.capture(client, key, update["report"], verify=verify, download=False)
                    state = job.get("status")
                    stage = state + "/" + str(job.get("error_code") or "unspecified") if state in {"failed", "interrupted", "pdf_failed"} else job["stage"]
                    entry = self.update(key, status=state, stage=job["stage"])
                    if stage != last_stage:
                        emit(agent=entry["agent_id"], job=entry["job_id"], report=entry.get("report_id"), status=stage,
                             count=entry.get("count"), pages=entry.get("pages"), timing=round(time.monotonic() - began, 2))
                        last_stage = stage
                    if state == "ready":
                        require(entry.get("report_id"), "ready_report_missing")
                        saved = self.json(client, PREFIX + "/reports/" + entry["report_id"])
                        entry = self.capture(client, key, saved, verify=True)
                        require(entry.get("pdf_sha256"), "ready_pdf_missing")
                        self.check_foreign(entry)
                        self.update(key, verified_at=datetime.now(timezone.utc).isoformat(), elapsed_seconds=round(time.monotonic() - began, 2))
                        emit(agent=entry["agent_id"], job=entry["job_id"], report=entry["report_id"],
                             status="saved_verified" if verify else "ready_no_news" if entry.get("count") == 0 and saved["result"].get("kind") == "briefing" else "ready_verified",
                             count=entry.get("count"), pages=entry.get("pages"), timing=round(time.monotonic() - began, 2))
                        return True
                    if state in {"failed", "interrupted", "pdf_failed"}:
                        return False
                    require(state in {"queued", "running"}, "invalid_job_status")
                    if self.stop.wait(self.args.poll_interval):
                        raise CheckError("operator_interrupted")
                raise CheckError("poll_timeout_job_may_continue")
        except (CheckError, httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as error:
            code = error.code if isinstance(error, CheckError) else "network_unconfirmed" if isinstance(error, httpx.HTTPError) else "validation_failed"
            self.update(key, validation_error=code)
            emit(agent=entry["agent_id"], job=entry.get("job_id"), report=entry.get("report_id"), status=code, timing=round(time.monotonic() - began, 2))
            return False

    def execute(self) -> int:
        self.basic()
        if not self.args.live and not self.args.verify_saved:
            return 0
        if self.args.verify_saved:
            allowed = {profile["id"] for profile in self.selected()} if self.args.agent or self.args.all_primary else None
            keys = [key for key, entry in self.state["runs"].items() if entry.get("job_id") and (allowed is None or entry["agent_id"] in allowed)]
            require(keys, "no_saved_jobs_to_verify")
        else:
            keys = [self.prepare(profile) for profile in self.selected()]
        with ThreadPoolExecutor(max_workers=min(2, len(keys))) as executor:
            futures = [executor.submit(self.run_one, key, verify=self.args.verify_saved) for key in keys]
            try:
                results = [future.result() for future in as_completed(futures)]
            except KeyboardInterrupt:
                self.stop.set()
                for future in futures:
                    future.cancel()
                raise
        return 0 if all(results) else 1


def main(argv=None, *, transport=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True, help="The HTTPS origin of the deployed 2.x service.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Explicitly authorize potentially billable report submissions.")
    mode.add_argument("--verify-saved", action="store_true", help="GET-only verification of this directory's saved jobs/reports/PDFs.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--all-primary", action="store_true", help="Select all seven configured active agents.")
    selection.add_argument("--agent", action="append", help="Select an agent ID; repeat for multiple agents. Defaults to the first agent.")
    parser.add_argument("--language", choices=("ar", "en"), default="ar")
    parser.add_argument("--session-file", help="Private Mozilla cookie jar; defaults to OUTPUT/session.cookies.")
    parser.add_argument("--output-dir", default="./data/workspace-validation", help="Private artifact/state directory; reuse to resume the same batch.")
    parser.add_argument("--timeout", type=float, default=900, help="Per-agent polling deadline, 1–900 seconds.")
    parser.add_argument("--poll-interval", type=float, default=2, help="Polling interval, 0.1–15 seconds.")
    args = parser.parse_args(argv)
    parsed = urlsplit(args.base_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ("", "/") or re.search(r"[\x00-\x20\x7f\\]", args.base_url)):
        parser.error("--base-url must be a plain HTTPS origin without credentials, a path, query or fragment.")
    if not 1 <= args.timeout <= 900 or not .1 <= args.poll_interval <= 15:
        parser.error("Use timeout 1–900 and poll interval 0.1–15 seconds.")
    args.base_url = args.base_url.rstrip("/")
    lock_stream = None
    try:
        # A process-level lock protects persistent keys against two operators
        # accidentally starting the same local validation batch concurrently.
        import fcntl
        directory = Path(args.output_dir).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(directory / ".validation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        lock_stream = os.fdopen(descriptor, "a+")
        try:
            fcntl.flock(lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CheckError("validation_batch_already_running") from None
        return Validator(args, transport=transport).execute()
    except KeyboardInterrupt:
        emit(status="operator_interrupted_saved_jobs_may_continue")
        return 130
    except (CheckError, httpx.HTTPError, OSError, ValueError, KeyError, TypeError, ImportError) as error:
        emit(status=error.code if isinstance(error, CheckError) else "network_unconfirmed" if isinstance(error, httpx.HTTPError) else "validation_setup_failed")
        return 1
    finally:
        if lock_stream:
            lock_stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
