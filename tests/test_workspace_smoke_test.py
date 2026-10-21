"""Operator smoke runner fixtures only: never make a real service/provider call."""
from copy import deepcopy
import hashlib
import json
import stat
import threading

import httpx
import pytest

from app.agent_profiles import list_profiles
from scripts import workspace_smoke_test as smoke


BASE = "https://workspace.fixture.test"
AGENT = "geopolitical-morning"
WINDOW = {"start": "2026-10-20T00:00:00Z", "end": "2026-10-20T12:00:00Z",
          "cutoff": "2026-10-20T12:00:00Z", "timezone": "UTC", "display_timezone": "Asia/Dubai"}
PDF = b"%PDF-1.7\nsynthetic fixture only\n%%EOF\n"


class Server:
    def __init__(self, directory):
        self.directory, self.requests, self.jobs, self.reports, self.keys = directory, [], {}, {}, {}
        self.sessions, self.owner, self.lock, self.drop_duplicate = 0, None, threading.Lock(), False
        self.config_changes = {}

    def __call__(self, request):
        with self.lock:
            return self.handle(request)

    def handle(self, request):
        self.requests.append(request)
        path, method = request.url.path, request.method
        cookie = request.headers.get("cookie", "")
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "version": "2.1.0"})
        if path == "/v1/public/bootstrap":
            assert request.headers["origin"] == BASE
            if cookie:
                return httpx.Response(200, json={})
            self.sessions += 1
            token = chr(64 + self.sessions) * 43
            if self.owner is None:
                self.owner = token
            return httpx.Response(200, json={}, headers={"Set-Cookie": f"{smoke.COOKIE}={token}; Secure; HttpOnly; Path=/; SameSite=strict"})
        if path == smoke.PREFIX + "/config":
            agents = [{**{key: profile[key] for key in
                ("id", "enabled", "default_topic_ar", "default_topic_en", "retrieval_options")},
                "news_feed_enabled": True} for profile in list_profiles()]
            return httpx.Response(200, json={"agents": agents, "agent_count": 7,
                "coverage_hours": 12, "maximum_news_items": 50, "maximum_newsdata_articles_per_refresh": 50,
                "refresh_mode": "manual", "display_timezone": "Asia/Dubai", **self.config_changes})
        if path == "/v1/usage":
            assert "authorization" not in request.headers
            return httpx.Response(401)
        if cookie != f"{smoke.COOKIE}={self.owner}":
            return httpx.Response(404, json={"detail": {"code": "report_not_found"}})
        if method == "POST":
            assert request.headers["origin"] == BASE
            body = json.loads(request.content)
            agent = path.split("/")[-2]
            key = request.headers["idempotency-key"]
            persisted = json.loads((self.directory / "validation.json").read_text())
            assert persisted["runs"][agent + ":" + body["language"]]["request_key"] == key
            repeated = key in self.keys
            if repeated and self.drop_duplicate:
                self.drop_duplicate = False
                raise httpx.ReadTimeout("DO NOT PRINT THIS PRIVATE TRANSPORT DETAIL", request=request)
            if not repeated:
                identifier = hashlib.sha256(key.encode()).hexdigest()[:32]
                job_id, report_id = "job_" + identifier, "rpt_" + identifier
                job = {"job_id": job_id, "agent_id": agent, "language": body["language"], "topic": body["topic"],
                       "status": "ready", "stage": "ready", "research_window": WINDOW, "report_id": report_id}
                record = self.record(job)
                self.keys[key] = job_id
                self.jobs[job_id] = {"job": job, "report": record}
                self.reports[report_id] = record
            return httpx.Response(202, json={"job": self.jobs[self.keys[key]]["job"], "joined_existing": repeated})
        if path.startswith(smoke.PREFIX + "/jobs/"):
            return httpx.Response(200, json=self.jobs[path.rsplit("/", 1)[1]])
        if path.endswith("/pdf"):
            return httpx.Response(200, content=PDF, headers={"Content-Type": "application/pdf"})
        if path.startswith(smoke.PREFIX + "/reports/"):
            return httpx.Response(200, json=self.reports[path.rsplit("/", 1)[1]])
        raise AssertionError("Unexpected fixture path")

    @staticmethod
    def record(job):
        source = {"id": "src_fixture", "url": "https://publisher.fixture.test/synthetic", "title": "PRIVATE NEWS MUST STAY IN FILE",
            "publisher": "Synthetic publisher", "published_at": "2026-10-20T10:00:00Z", "role": "fresh",
            "publication_verification": {"verified": True, "method": "publisher_jsonld_datePublished",
                                         "value": "2026-10-20T10:00:00Z"}}
        return {"report_id": job["report_id"], "job_id": job["job_id"], "agent_id": job["agent_id"], "language": job["language"],
            "research_cutoff": WINDOW["end"], "pdf": {"status": "ready", "size_bytes": len(PDF), "page_count": 1},
            "result": {"schema_version": 2, "agent_id": job["agent_id"], "language": job["language"], "kind": "briefing",
                "research_window": WINDOW, "title": "Private report title", "sources": [source], "sections": [],
                "items": [{"id": "item_1", "headline": "PRIVATE NEWS MUST STAY IN FILE", "published_at": source["published_at"],
                           "url": source["url"], "source_ids": [source["id"]]}],
                "citation_mappings": {source["id"]: {"url": source["url"], "published_at": source["published_at"]}}}}


@pytest.fixture
def runner(tmp_path):
    directory = tmp_path / "private-qa"
    server = Server(directory)
    common = ["--base-url", BASE, "--output-dir", str(directory), "--language", "en"]
    def run(*flags):
        return smoke.main([*common, *flags], transport=httpx.MockTransport(server))
    return run, server, directory


def test_live_runner_persists_keys_before_post_then_verifies_saved_with_gets_only(runner, capsys):
    run, server, directory = runner
    assert run("--live", "--agent", AGENT) == 0
    posts = [request for request in server.requests if request.method == "POST"]
    assert len(posts) == 2 and posts[0].headers["idempotency-key"] == posts[1].headers["idempotency-key"]
    state = json.loads((directory / "validation.json").read_text())
    entry = state["runs"][AGENT + ":en"]
    assert entry["duplicate_verified"] and entry["pdf_sha256"] and entry["payload_sha256"]
    assert (directory / (entry["report_id"] + ".pdf")).read_bytes() == PDF
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in directory.iterdir())
    start = len(server.requests)
    assert run("--verify-saved") == 0
    assert all(request.method == "GET" for request in server.requests[start:])
    start = len(server.requests)
    assert run("--live", "--agent", AGENT) == 0
    assert all(request.method == "GET" for request in server.requests[start:]), "The same directory resumes its batch; it must not create another report."
    output = capsys.readouterr().out
    assert server.owner not in output and "PRIVATE NEWS" not in output and entry["request_key"] not in output
    assert all(set(json.loads(line)) <= {"agent", "job", "report", "status", "count", "pages", "timing"} for line in output.splitlines())


def test_basic_is_get_only_and_all_primary_submits_only_seven_same_session_jobs(runner):
    run, server, _ = runner
    assert run() == 0
    assert all(request.method == "GET" for request in server.requests)
    assert run("--live", "--all-primary") == 0
    posts = [request for request in server.requests if request.method == "POST"]
    assert len(posts) == 14 and len(server.jobs) == 7
    assert {value["job"]["agent_id"] for value in server.jobs.values()} == smoke.EXPECTED_AGENT_IDS
    assert len({request.headers["cookie"] for request in posts}) == 1


@pytest.mark.parametrize("changes", [
    {"agent_count": 9}, {"maximum_news_items": 20}, {"maximum_newsdata_articles_per_refresh": 100},
    {"refresh_mode": "automatic"}, {"display_timezone": "UTC"},
])
def test_invalid_configuration_blocks_live_mode_before_any_paid_post(runner, capsys, changes):
    run, server, _ = runner
    server.config_changes = changes
    assert run("--live", "--all-primary") == 1
    assert all(request.method == "GET" for request in server.requests)
    assert "invalid_workspace_configuration" in capsys.readouterr().out


def test_both_retrieval_options_are_required_before_paid_submissions(runner, capsys):
    run, server, _ = runner
    agents = [{**{key: profile[key] for key in ("id", "enabled", "default_topic_ar", "default_topic_en")},
        "news_feed_enabled": True, "retrieval_options": ["newsdata", "web_search"]} for profile in list_profiles()]
    agents[-1]["retrieval_options"] = ["web_search"]
    server.config_changes = {"agents": agents}
    assert run("--live", "--all-primary") == 1
    assert all(request.method == "GET" for request in server.requests)
    assert "invalid_workspace_configuration" in capsys.readouterr().out


def test_network_failure_after_first_post_keeps_job_available_for_get_only_recovery(runner, capsys):
    run, server, directory = runner
    server.drop_duplicate = True
    assert run("--live", "--agent", AGENT) == 1
    saved = json.loads((directory / "validation.json").read_text())["runs"][AGENT + ":en"]
    assert saved["job_id"] and not saved.get("duplicate_verified")
    assert "PRIVATE TRANSPORT" not in capsys.readouterr().out
    start = len(server.requests)
    assert run("--verify-saved") == 0
    assert all(request.method == "GET" for request in server.requests[start:])


def test_missing_original_cookie_refuses_paid_resume_before_any_requests(runner):
    run, server, directory = runner
    assert run("--live", "--agent", AGENT) == 0
    (directory / "session.cookies").unlink()
    start = len(server.requests)
    assert run("--live", "--agent", AGENT) == 1
    assert server.requests[start:] == []


def test_saved_payload_change_is_detected_without_submitting_another_report(runner, capsys):
    run, server, _ = runner
    assert run("--live", "--agent", AGENT) == 0
    next(iter(server.reports.values()))["result"]["title"] = "Changed after restart"
    start = len(server.requests)
    assert run("--verify-saved") == 1
    assert all(request.method == "GET" for request in server.requests[start:])
    assert "saved_report_payload_changed" in capsys.readouterr().out


@pytest.mark.parametrize("invalid", ["too_many", "outside_window", "unverified", "http_url"])
def test_report_validation_rejects_invalid_source_and_window_claims(invalid):
    job = {"job_id": "job_" + "a" * 32, "report_id": "rpt_" + "b" * 32, "agent_id": AGENT, "language": "en"}
    record = Server.record(job)
    if invalid == "too_many":
        record["result"]["items"] *= 51
    elif invalid == "outside_window":
        record["result"]["items"][0]["published_at"] = "2026-10-19T12:00:00Z"
    elif invalid == "unverified":
        record["result"]["sources"][0]["publication_verification"]["verified"] = False
    else:
        record["result"]["sources"][0]["url"] = "http://publisher.fixture.test/synthetic"
    with pytest.raises(smoke.CheckError):
        smoke.validate_report(record, {**job, "research_window": deepcopy(WINDOW)})


def test_report_validation_accepts_labelled_newsdata_metadata_without_claiming_publisher_verification():
    job = {"job_id": "job_" + "a" * 32, "report_id": "rpt_" + "b" * 32, "agent_id": AGENT, "language": "en"}
    record = Server.record(job)
    source = record["result"]["sources"][0]
    source.update(publisher_text_verified=False, evidence_level="newsdata_structured_metadata",
        publication_verification={"verified": False, "method": "newsdata_pubDate_pubDateTZ",
            "provider_metadata_validated": True, "timestamp": source["published_at"], "timezone": "UTC"})
    entry = {**job, "research_window": deepcopy(WINDOW)}
    assert smoke.validate_report(record, entry) == (1, 1)
    source["publication_verification"]["provider_metadata_validated"] = False
    with pytest.raises(smoke.CheckError):
        smoke.validate_report(record, entry)


def test_report_validation_accepts_up_to_fifty_distinct_current_articles():
    job = {"job_id": "job_" + "a" * 32, "report_id": "rpt_" + "b" * 32, "agent_id": AGENT, "language": "en"}
    record = Server.record(job)
    result = record["result"]
    template = deepcopy(result["sources"][0])
    result.update(items=[], sources=[], citation_mappings={})
    for number in range(50):
        source = {**deepcopy(template), "id": f"source_{number}", "url": template["url"] + f"/{number}"}
        result["sources"].append(source)
        result["items"].append({"id": f"item_{number}", "headline": f"Synthetic distinct article {number}",
            "published_at": source["published_at"], "url": source["url"], "source_ids": [source["id"]]})
        result["citation_mappings"][source["id"]] = {"url": source["url"], "published_at": source["published_at"]}
    assert smoke.validate_report(record, {**job, "research_window": deepcopy(WINDOW)}) == (50, 1)
