"""Synthetic feed fixtures; no NewsData, publisher or model calls are allowed."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from app import news_feed, newsdata
from app.agent_profiles import get_profile
from app.storage import QuotaLimits, Store, StoreError


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def article(number=1, **changes):
    return {"article_id": f"synthetic-{number}",
        "title": f"Synthetic Iran nuclear negotiations fixture article number {number}",
        "description": f"Fictional Iran and US nuclear diplomacy test description number {number}.",
        "link": f"https://publisher-fixture.example/articles/{number}", "pubDate": "2026-10-20 09:00:00",
        "pubDateTZ": "UTC", "language": "english", "source_name": "Synthetic Publisher", "duplicate": False,
        **changes}


def page(items=None, **changes):
    items = [article()] if items is None else items
    return {"status": "success", "results": items, "totalResults": len(items), "nextPage": None, **changes}


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWSDATA_API_KEY", "synthetic-key-not-a-real-credential")
    clock = [NOW]
    base = Store(tmp_path / "feed.sqlite3", QuotaLimits(max_reports_per_day=100,
        newsdata_daily_credit_limit=200), clock=lambda: clock[0])
    base.initialize()
    base.test_clock = clock
    return base


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: original(*args, **kwargs, transport=transport))


def refresh(database, agent="iran-news"):
    store = news_feed.FeedStore(database)
    claim = store.claim(agent)
    assert claim is not None
    news_feed._refresh(store, agent, claim)
    return news_feed.get_feed(agent, "en", refresh=False, database=database)


def test_queries_are_topic_specific_bilingual_bounded_and_match_seven_briefing_agents():
    assert len(news_feed.QUERIES) == 7
    assert len(set(news_feed.QUERIES.values())) == 7
    for agent, query in news_feed.QUERIES.items():
        assert get_profile(agent)["kind"] == "briefing"
        assert len(query) <= 100
        assert any("\u0600" <= char <= "\u06ff" for char in query)
    assert not news_feed.supports_agent("historical-reading-companion")
    assert not news_feed.supports_agent("story-moodboard")


def test_up_to_one_hundred_accepted_articles_not_first_raw_results(monkeypatch, database):
    calls = []
    raw = [article(number) for number in range(1, 131)]
    for index in range(0, 30):
        raw[index]["title"] = "Synthetic unrelated local sports fixture " + str(index)
        raw[index]["description"] = "No geopolitical subject here."
    def handler(request):
        calls.append(request)
        assert request.headers["X-ACCESS-KEY"] == "synthetic-key-not-a-real-credential"
        assert "synthetic-key" not in str(request.url)
        assert request.url.params["language"] == "en,ar"
        assert request.url.params["size"] == "10"
        assert "timeframe" not in request.url.params
        index = int(request.url.params.get("page", "0"))
        return httpx.Response(200, json=page(raw[index:index + 10], totalResults=130,
            nextPage=str(index + 10) if index < 120 else None))
    mock_http(monkeypatch, handler)
    result = refresh(database)
    assert len(calls) == 13
    assert result["actual_count"] == result["maximum_items"] == 100
    assert result["pagination"]["raw_count"] == 130
    assert result["pagination"]["stop_reason"] == "article_limit"
    assert result["excluded"] == {"irrelevant": 30}
    assert result["coverage_hours"] == 12 and not result["partial"]
    with database._connect() as connection:
        assert news_feed.accounted_credits(connection, local_date="2026-10-20") == 13
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_paid_page_size_50_can_reach_100_without_legacy_twenty_cap(monkeypatch, database):
    monkeypatch.setenv("NEWSDATA_PLAN", "paid")
    monkeypatch.setenv("NEWSDATA_PAGE_SIZE", "50")
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.params["size"] == "50"
        start = 1 if "page" not in request.url.params else 51
        return httpx.Response(200, json=page([article(n) for n in range(start, start + 50)],
            totalResults=200, nextPage="second" if start == 1 else "must-not-fetch"))
    mock_http(monkeypatch, handler)
    result = refresh(database)
    assert len(calls) == 2 and result["actual_count"] == 100
    assert newsdata.MAX_NEWSDATA_RESULTS == 20
    assert newsdata.initial_context(NOW)["window_start"].startswith("2026-10-18")


@pytest.mark.parametrize("plan", ["unknown", "free", "not-a-plan"])
def test_unknown_or_free_plan_does_not_guess_paid_page_entitlement(monkeypatch, plan):
    monkeypatch.setenv("NEWSDATA_PLAN", plan)
    monkeypatch.setenv("NEWSDATA_PAGE_SIZE", "50")
    assert news_feed._page_size() == 10
    assert not news_feed.plan_metadata()["plan_verified"]


def test_first_sorted_page_older_than_exact_window_stops_and_reports_observed_delay(monkeypatch, database):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([article(1, pubDate="2026-10-19 23:59:50"),
            article(2, pubDate="2026-10-19 23:58:10")], totalResults=3153, nextPage="older-only"))
    mock_http(monkeypatch, handler)
    result = refresh(database)
    assert len(calls) == 1 and result["actual_count"] == 0
    assert result["status"] == "empty" and result["pagination"]["stop_reason"] == "window_exhausted"
    assert result["observed_latest_provider_publication_at"] == "2026-10-19T23:59:50.000000+00:00"
    assert result["observed_earliest_provider_publication_at"] == "2026-10-19T23:58:10.000000+00:00"
    assert result["provider_age_seconds"] == 43210 and result["suspected_provider_delay"]
    assert result["provider_plan"]["configured_plan"] == "unknown"
    assert "provider_delay" in result["limitation_codes"]
    assert "free_plan_12_hour_delay" not in result["limitation_codes"]


def test_unsorted_provider_page_is_not_used_to_assume_remaining_pages_are_old(monkeypatch, database):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json=page([article(1, pubDate="2026-10-19 22:00:00"),
                article(2, pubDate="2026-10-19 23:00:00")], nextPage="newer"))
        return httpx.Response(200, json=page([article(3)]))
    mock_http(monkeypatch, handler)
    result = refresh(database)
    assert len(calls) == 2 and result["actual_count"] == 1
    assert not result["suspected_provider_delay"]


def test_exact_window_boundaries_future_missing_date_and_arabic_metadata(monkeypatch, database):
    values = [article(1, pubDate="2026-10-20 00:00:00"), article(2, pubDate="2026-10-20 12:00:00"),
        article(3, pubDate="2026-10-19 23:59:59"), article(4, pubDate="2026-10-20 12:00:01"),
        article(5, pubDate="2026-10-20"), article(6, language="arabic", title="إيران تعلن بدء مفاوضات نووية جديدة",
            description="هذا وصف اصطناعي لا يمثل خبراً حقيقياً."), article(7, language="french")]
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=page(values)))
    result = refresh(database)
    assert result["actual_count"] == 3
    assert result["excluded"] == {"outside_window": 1, "future_publication": 1, "invalid_publication": 1, "unsupported_language": 1}
    arabic = next(item for item in result["items"] if item["article_id"] == "synthetic-6")
    assert arabic["original_language"] == arabic["language"] == "ar"
    assert arabic["translation_status"] == "original_only"
    assert not arabic["publisher_text_verified"] and not arabic["publication_verification"]["verified"]


def test_maximum_provider_identifier_keeps_persisted_source_ids_within_bound(monkeypatch, database):
    identifier = "a" * 200
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=page([article(article_id=identifier)])))
    result = refresh(database)
    item = result["items"][0]
    assert item["article_id"] == identifier
    assert item["id"].startswith("newsdata:sha256-") and len(item["id"]) <= 200
    assert item["source_ids"] == [item["id"]]


def test_percent_encoded_article_url_has_an_explicit_final_length_bound(monkeypatch, database):
    url = "https://fixture.example/" + "عنوان" * 160
    assert len(url) < 1600
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=page([article(link=url)])))
    result = refresh(database)
    assert result["actual_count"] == 0 and result["excluded"]["invalid_link"] == 1


def test_cross_page_identity_canonical_link_and_near_headline_deduplication(monkeypatch, database):
    first = article(1, title="Iran and United States announce fresh nuclear negotiation timetable in synthetic test")
    second = [deepcopy(first), article(2, link=first["link"] + "?utm_source=fixture"),
        article(3, title=first["title"] + "."), article(4, title=first["title"].replace("fresh", "new")), article(5)]
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([first] if len(calls) == 1 else second,
            nextPage="next" if len(calls) == 1 else None))
    mock_http(monkeypatch, handler)
    result = refresh(database)
    assert result["actual_count"] == 3
    assert result["excluded"]["duplicate"] == 3
    assert result["pagination"]["raw_count"] == 6


@pytest.mark.parametrize("title,description,expected", [
    ("Synthetic Iran nuclear talks with the US", "", True),
    ("Synthetic US domestic election dispute", "Candidates mention Iran in passing.", False),
    ("Synthetic Iranian football tournament", "A match report.", False),
    ("Synthetic fuel prices rise", "Shipping costs reflect the Iran conflict in this fixture.", True),
    ("Synthetic fuel prices rise", "Demand increased in Iran because of summer temperatures.", False),
    ("Synthetic fuel prices rise", "Demand increased across the region.", False),
    ("مفاوضات نووية بين إيران وأمريكا", "وصف اصطناعي", True),
])
def test_iran_relevance_requires_direct_conflict_connection(title, description, expected):
    assert news_feed._relevant("iran-news", title, description) is expected


def test_global_topic_query_and_filter_are_distinct_from_iran(monkeypatch, database):
    def handler(request):
        assert request.url.params["q"] == news_feed.QUERIES["geopolitical-morning"]
        return httpx.Response(200, json=page([article(1, title="Synthetic European sanctions after border conflict",
            description="A fictional diplomatic dispute, unrelated to Iran.")]))
    mock_http(monkeypatch, handler)
    result = refresh(database, "geopolitical-morning")
    assert result["actual_count"] == 1


@pytest.mark.parametrize("title,description", [
    ("Digital performers create a conflict in Hollywood", "Studios debate replacing actors in new films."),
    ("الذكاء الاصطناعي يثير صراعا داخل هوليوود", "منتجو الأفلام يناقشون مستقبل الممثلين والإبداع الرقمي."),
    ("State party endorses governor under zoning arrangement", "Stakeholders divide the governorship nomination before a general election."),
    ("حزب محلي يوزع ترشيحات الانتخابات البلدية", "أعضاء المجلس البلدي يناقشون المنافسة داخل الحزب."),
    ("President endorses candidate at county campaign rally", "The local election focuses on road repairs."),
    ("قمة كروية وسط صراع على الصدارة", "مباراة جديدة بين الأندية في دوري أبطال أوروبا."),
    ("War film wins an award", "Actors discuss a fictional military offensive and a president's resignation."),
    ("City council wages war on dumping", "Municipal officials announce new rubbish collection rules."),
    ("Restaurant wins guide recognition", "The owners fled a military conflict decades ago."),
    ("New family SUV borrows a fighter's name", "The design recalls a military aircraft from the Second World War."),
    ("Local polling locations announce extended opening hours", "Residents prepare to cast ballots in a national election."),
    ("Provincial task force mediates tribal conflict", "Local officials seek a ceasefire after a village dispute."),
    ("President insults journalists during an event", "The personality dispute dominates the event. " * 35 + "Foreign ministers negotiate a peace treaty."),
    ("Energy prices fall with cooler weather", "Ordinary demand changes explain the movement."),
    ("Youth conference teaches good conflict", "Young people discuss teamwork and personal disagreements."),
    ("A royal family hopes to settle a private dispute", "Two brothers describe their personal conflict."),
    ("Company president resigns after board dispute", "The private business chooses a replacement executive."),
    ("War film recreates an airstrike on a church", "The fictional military operation is part of a cinema production."),
    ("Club president resigns after a poor season", "The football club chooses a replacement."),
    ("رئيس النادي يستقيل بعد خسارة الفريق", "يناقش مجلس الإدارة مستقبل النادي."),
    ("رئيس الجامعة يستقيل بعد خلاف مع مجلس الأمناء", "تبحث المؤسسة عن إدارة جديدة."),
])
def test_global_relevance_rejects_local_and_metaphorical_keyword_matches(title, description):
    assert not news_feed._relevant("geopolitical-morning", title, description)


@pytest.mark.parametrize("title,description", [
    ("UN peacekeepers face withdrawal as Sudan fighting crosses the border", "Military units warn of conflict spreading into a neighbouring country."),
    ("Burundi seeks regional peace in the Great Lakes", "The foreign minister meets counterparts to negotiate regional cooperation."),
    ("Pacific foreign ministers agree a security pact", "Vanuatu and Samoa discuss maritime security and bilateral cooperation."),
    ("Multinational military exercise begins in East Asia", "Troops from several nations practise joint operations."),
    ("Chile's president resigns after confidence crisis", "Parliament begins a constitutional transition."),
    ("Prime minister forms government in a small island nation", "A new parliamentary coalition takes office."),
    ("European Parliament selects new leadership for its top post", "Members confirm the parliamentary leadership result."),
    ("Election results change control of congress", "The opposition wins a national legislative majority."),
    ("الحكومة تفرض عقوبات على صادرات الطاقة بسبب النزاع", "تستهدف الإجراءات تمويل العمليات العسكرية."),
    ("المعارضة تفوز بأغلبية في البرلمان", "تبدأ مفاوضات تشكيل حكومة جديدة بعد إعلان نتائج الانتخابات."),
    ("مباحثات تبحث الترتيبات الأمنية والانسحاب العسكري", "تجتمع وفود عسكرية لمناقشة الحدود والضمانات المتبادلة."),
    ("زيارة دولة تعزز العلاقات بين بلدين", "يلتقي رئيس الدولة وزير الخارجية لمناقشة اتفاق تعاون."),
    ("Defence minister holds talks on ending the conflict", "Envoys seek an agreement on troop withdrawals."),
    ("Energy deal creates a new international security divide", "The agreement affects sanctions and the course of the war."),
    ("Army reports attacks against armed factions", "Military operations destroyed weapons and vehicles."),
    ("Nuclear powers negotiate over their arsenals", "Foreign policy officials call for diplomacy and direct talks."),
    ("Embassy staff withdrawn after a diplomatic dispute", "The government recalls its diplomats."),
    ("Leaders sign peace treaty at a football stadium", "Foreign ministers conclude the agreement after years of war."),
    ("Government imposes tariffs on strategic imports", "The export controls respond to an international security dispute."),
    ("Congress warns of nationwide election disruptions", "Officials seek protections against foreign interference in the national vote."),
    ("ممثل الأمم المتحدة يعلن اتفاقا لوقف إطلاق النار", "تتضمن المبادرة انسحاب القوات من منطقة النزاع."),
    ("UN envoy announces ceasefire after talks with international actors", "Diplomats agree to monitor the peace agreement."),
    ("Military airstrike hits university, killing dozens", "The army operation damages classrooms and nearby homes."),
    ("President resigns after parliament withdraws confidence", "University students had protested nationwide before the vote."),
    ("Army airstrike hits church during border conflict", "Military officials confirm the attack on the civilian building."),
    ("President assassinated at a church during a state visit", "Parliament announces an emergency national leadership transition."),
    ("UN envoy announces peace agreement at a church", "Foreign ministers negotiate the withdrawal of troops."),
])
def test_global_relevance_preserves_security_diplomacy_and_major_national_changes(title, description):
    assert news_feed._relevant("geopolitical-morning", title, description)


@pytest.mark.parametrize("include_relevant", [False, True], ids=["honest-empty", "retained-geopolitics"])
def test_existing_global_cache_is_revalidated_without_resetting_snapshot_cooldown_or_credits(monkeypatch, database, include_relevant):
    values = [article(1, title="Hollywood's digital conflict", description="Film actors debate their future."),
        article(2, title="State party adopts zoning arrangement", description="The governorship election is approaching."),
        article(3, title="Foreign ministers sign peace treaty", description="A diplomatic agreement ends the war.",
            pubDate="2026-10-20 00:00:01")]
    if include_relevant:
        values.append(article(4, title="Army announces ceasefire", description="Military forces halt the conflict."))
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page(values))
    mock_http(monkeypatch, handler)
    # Seed the exact legacy acceptance behaviour, then activate the new filter.
    with monkeypatch.context() as legacy:
        legacy.setattr(news_feed, "_global_relevant", lambda *_: True)
        old = refresh(database, "geopolitical-morning")
    store = news_feed.FeedStore(database)
    row_before, provider_before = store.read("geopolitical-morning"), store.provider_state()
    database.test_clock[0] += timedelta(minutes=2)
    for language in ("en", "ar"):
        result = news_feed.get_feed("geopolitical-morning", language, force=True, database=database)
        assert result["actual_count"] == int(include_relevant)
        assert result["status"] == ("cached" if include_relevant else "empty")
        assert result["snapshot_id"] == old["snapshot_id"]
        assert result["last_successful_refresh_at"] == old["last_successful_refresh_at"]
        assert result["next_refresh_at"] == old["next_refresh_at"]
        assert result["window_excluded_from_cache"] == 1
        assert result["relevance_excluded_from_cache"] == result["excluded"]["irrelevant_cached"] == 2
        assert result["cached_article_count"] == len(values)
        assert all(item["article_id"] == "synthetic-4" for item in result["items"])
    report = news_feed.get_report_feed("geopolitical-morning", language="en", database=database)
    assert report["actual_count"] == len(report["sources"]) == int(include_relevant)
    assert store.read("geopolitical-morning") == row_before
    assert store.provider_state() == provider_before
    assert len(calls) == 1
    with database._connect() as connection:
        assert news_feed.accounted_credits(connection, local_date="2026-10-20") == 1


def test_repeated_cursor_stops_without_retry_and_preserves_partial_data(monkeypatch, database):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([article(len(calls))], nextPage="repeated"))
    mock_http(monkeypatch, handler)
    result = refresh(database)
    assert len(calls) == result["actual_count"] == 2
    assert result["partial"] and result["pagination"]["stop_reason"] == "repeated_cursor"


def test_page_limit_is_twenty_without_relaxing_relevance(monkeypatch, database):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([article(len(calls), title="Synthetic unrelated sports item", description="")],
            nextPage=f"page-{len(calls)}"))
    mock_http(monkeypatch, handler)
    result = refresh(database)
    assert len(calls) == 20 and result["actual_count"] == 0
    assert result["partial"] and result["pagination"]["stop_reason"] == "page_limit"


def test_total_deadline_cancels_inflight_request_and_retains_attempted_credit(monkeypatch, database):
    monkeypatch.setattr(news_feed, "MAX_REFRESH_SECONDS", .03)
    calls, cancelled = [], []
    async def handler(request):
        calls.append(request)
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        raise AssertionError("deadline must cancel this synthetic request")
    mock_http(monkeypatch, handler)
    result = refresh(database)
    assert len(calls) == 1 and cancelled == [True]
    assert result["status"] == "provider_error" and result["error"]["code"] == "newsdata_timeout"
    with database._connect() as connection:
        assert news_feed.accounted_credits(connection, local_date="2026-10-20") == 1


@pytest.mark.parametrize("status,code,expected", [
    (401, "Unauthorized", "newsdata_auth_failed"), (403, "AccessDenied", "newsdata_access_denied"),
    (429, "ApiLimitExceeded", "newsdata_rate_limited"), (200, "RateLimitExceeded", "newsdata_rate_limited"),
    (503, "ServiceUnavailable", "newsdata_unavailable"),
])
def test_provider_errors_are_safe_and_globally_cooled_down(monkeypatch, database, status, code, expected):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"status": "error", "results": {"code": code,
            "message": "SECRET raw provider response must never escape"}}, headers={"Retry-After": "1800"})
    mock_http(monkeypatch, handler)
    result = refresh(database)
    assert result["status"] == "provider_error" and result["error"]["code"] == expected
    assert result["error"]["retry_after_seconds"] == 1800
    assert "SECRET" not in json.dumps(result)
    assert news_feed.FeedStore(database).claim("geopolitical-morning") is None
    again = news_feed.get_feed("iran-news", "ar", force=True, database=database)
    assert not again["refresh_started"] and len(calls) == 1


def test_partial_page_success_then_error_keeps_accepted_articles(monkeypatch, database):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json=page(nextPage="broken"))
        return httpx.Response(503, json={"status": "error"})
    mock_http(monkeypatch, handler)
    result = refresh(database)
    assert len(calls) == 2 and result["actual_count"] == 1
    assert result["status"] == "provider_error" and result["partial"]
    assert result["last_successful_refresh_at"] is None


@pytest.mark.parametrize("header", ["X-API-Limit-Remaining", "X-RateLimit-Remaining"])
def test_successful_empty_final_credit_page_remains_a_valid_snapshot(monkeypatch, database, header):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([], totalResults=0), headers={header: "0"})
    mock_http(monkeypatch, handler)
    result = refresh(database)
    assert result["status"] == "empty" and result["error"] is None and not result["stale"]
    assert news_feed.FeedStore(database).claim("geopolitical-morning") is None
    report_feed = news_feed.get_report_feed("iran-news", language="en", database=database)
    assert report_feed["actual_count"] == 0 and report_feed["error"] is None
    assert report_feed["snapshot_id"] == result["snapshot_id"] and len(calls) == 1


def test_cache_reuse_language_switch_and_manual_refresh_do_not_spend_again(monkeypatch, database):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page())
    mock_http(monkeypatch, handler)
    initial = refresh(database)
    for language in ("ar", "en", "ar"):
        result = news_feed.get_feed("iran-news", language, force=True, database=database)
        assert not result["refresh_started"]
        assert result["snapshot_id"] == initial["snapshot_id"]
    assert len(calls) == 1
    assert result["items"][0]["translation_status"] == "original_only"
    assert "translation_unavailable_for_some_items" in result["limitation_codes"]


def test_failed_refresh_preserves_old_cache_and_clearly_marks_it_stale(monkeypatch, database):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page()) if len(calls) == 1 else httpx.Response(429, json={"status": "error"})
    mock_http(monkeypatch, handler)
    old = refresh(database)
    database.test_clock[0] += timedelta(seconds=901)
    result = refresh(database)
    assert result["snapshot_id"] == old["snapshot_id"]
    assert result["last_successful_refresh_at"] == old["last_successful_refresh_at"]
    assert result["stale"] and result["cached"] and result["status"] == "provider_error"


def test_missing_configuration_costs_no_credit_and_is_not_an_empty_success(monkeypatch, database):
    monkeypatch.delenv("NEWSDATA_API_KEY")
    result = refresh(database)
    assert result["status"] == "provider_error" and result["error"]["code"] == "newsdata_not_configured"
    assert not result["error"]["retryable"]
    with database._connect() as connection:
        assert news_feed.accounted_credits(connection, local_date="2026-10-20") == 0


def test_cache_claims_are_atomic_across_store_instances_and_agents(database):
    barrier = threading.Barrier(6)
    def claim(index):
        store = news_feed.FeedStore(database)
        barrier.wait()
        return store.claim("iran-news" if index < 3 else "geopolitical-morning")
    with ThreadPoolExecutor(max_workers=6) as workers:
        claims = list(workers.map(claim, range(6)))
    assert sum(claim is not None for claim in claims) == 1


def test_old_ledger_usage_and_feed_requests_share_daily_credit_ceiling(monkeypatch, database):
    database._limits = QuotaLimits(newsdata_daily_credit_limit=2)
    database.reserve_run("iran-news", "synthetic old report", 0, provider="newsdata", model="",
        newsdata_credits=1, newsdata_context=newsdata.initial_context(NOW), allow_zero_cost=True)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page(nextPage="no-budget-for-next-page"))
    mock_http(monkeypatch, handler)
    result = refresh(database)
    assert len(calls) == 1 and result["actual_count"] == 1
    assert result["error"]["code"] == "newsdata_daily_credit_limit"
    with database._connect() as connection:
        assert news_feed.accounted_credits(connection, local_date="2026-10-20") == 1
        assert connection.execute("SELECT SUM(accounted_newsdata_credits) FROM runs").fetchone()[0] == 1
    assert result["next_refresh_at"] == "2026-10-20T20:00:00.000000+00:00"


def test_safe_failure_reference_is_linked_to_structured_log(monkeypatch, database, caplog):
    mock_http(monkeypatch, lambda request: httpx.Response(503,
        json={"status": "error", "results": {"code": "ServiceUnavailable", "message": "SECRET-provider-message"}},
        headers={"X-Request-Id": "fixture-request-123"}))
    with caplog.at_level("WARNING", logger=news_feed.LOGGER.name):
        result = refresh(database)
    assert result["error"]["reference"] in caplog.text
    assert "fixture-request-123" in caplog.text and '"provider_status":503' in caplog.text
    assert "SECRET-provider-message" not in caplog.text and "synthetic-key" not in caplog.text
    assert "https://" not in caplog.text


def test_report_uses_same_snapshot_and_exact_requested_window_without_second_fetch(monkeypatch, database):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([article(1, pubDate="2026-10-20 00:00:01"), article(2)]))
    mock_http(monkeypatch, handler)
    original = refresh(database)
    database.test_clock[0] += timedelta(minutes=2)
    window = news_feed._window(None, database.test_clock[0])
    progress = []
    result = news_feed.get_report_feed("iran-news", get_profile("iran-news")["default_topic_ar"],
        research_window=window, language="ar", progress=progress.append, database=database)
    assert len(calls) == 1 and result["snapshot_id"] == original["snapshot_id"]
    assert result["actual_count"] == len(result["sources"]) == 1
    assert result["requested_research_window"] == window
    assert result["research_window"] != window
    assert not result["freshness"]["coverage_complete"]
    assert progress == ["searching"]


@pytest.mark.parametrize("new_items", [[], [article(2)]], ids=["empty", "nonempty"])
def test_report_waits_for_dashboard_refresh_of_expired_snapshot(monkeypatch, database, new_items):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([] if len(calls) == 1 else new_items))
    mock_http(monkeypatch, handler)
    old = refresh(database)
    database.test_clock[0] += timedelta(seconds=901)
    store = news_feed.FeedStore(database)
    dashboard_claim = store.claim("iran-news")
    assert dashboard_claim is not None
    loading = news_feed.get_feed("iran-news", "en", refresh=False, database=database)
    assert loading["status"] == "loading" and loading["stale"]
    assert loading["snapshot_id"] == old["snapshot_id"]
    waits = []
    def finish_dashboard(seconds):
        waits.append(seconds)
        news_feed._refresh(store, "iran-news", dashboard_claim)
    monkeypatch.setattr(news_feed, "time", SimpleNamespace(
        monotonic=news_feed.time.monotonic, sleep=finish_dashboard))
    result = news_feed.get_report_feed("iran-news", language="en", database=database)
    assert len(waits) == 1 and len(calls) == 2
    assert result["snapshot_id"] == dashboard_claim[0] != old["snapshot_id"]
    assert result["last_successful_refresh_at"] == news_feed._stamp(database.test_clock[0])
    assert result["actual_count"] == len(new_items)
    assert result["status"] != "loading" and not result["stale"]
    with database._connect() as connection:
        assert news_feed.accounted_credits(connection, local_date="2026-10-20") == 2


@pytest.mark.parametrize("prior_failure", [False, True], ids=["expired-cache", "elapsed-error-cooldown"])
def test_report_waits_for_other_feed_then_claims_its_own_refresh(monkeypatch, database, prior_failure):
    calls = []
    def handler(request):
        calls.append(request)
        if prior_failure and len(calls) == 2:
            return httpx.Response(503, json={"status": "error"})
        return httpx.Response(200, json=page([] if len(calls) == 1 else [article(2)]))
    mock_http(monkeypatch, handler)
    old = refresh(database)
    database.test_clock[0] += timedelta(seconds=901)
    if prior_failure:
        failed = refresh(database)
        assert failed["error"]["code"] == "newsdata_unavailable"
        database.test_clock[0] += timedelta(seconds=901)
    store = news_feed.FeedStore(database)
    other_claim = store.claim("geopolitical-morning")
    assert other_claim is not None
    elapsed, waits, budgets = [0.0], [], []
    original_refresh = news_feed._refresh
    def finish_other(seconds):
        waits.append(seconds)
        elapsed[0] += 30
        database.test_clock[0] += timedelta(seconds=30)
        original_refresh(store, "geopolitical-morning", other_claim)
    def record_refresh(store, agent_id, claim, **kwargs):
        assert agent_id == "iran-news"
        budgets.append(kwargs["timeout_seconds"])
        original_refresh(store, agent_id, claim, **kwargs)
    monkeypatch.setattr(news_feed, "time", SimpleNamespace(monotonic=lambda: elapsed[0], sleep=finish_other))
    monkeypatch.setattr(news_feed, "_refresh", record_refresh)
    result = news_feed.get_report_feed("iran-news", language="en", database=database)
    assert len(waits) == 1 and budgets == [60]
    assert result["snapshot_id"] not in {old["snapshot_id"], other_claim[0]}
    assert result["actual_count"] == 1 and not result["stale"] and result["error"] is None
    assert [request.url.params["q"] for request in calls[-2:]] == [
        news_feed.QUERIES["geopolitical-morning"], news_feed.QUERIES["iran-news"]]
    expected = 4 if prior_failure else 3
    assert len(calls) == expected
    with database._connect() as connection:
        assert news_feed.accounted_credits(connection, local_date="2026-10-20") == expected


@pytest.mark.parametrize("holder", ["iran-news", "geopolitical-morning"], ids=["dashboard-refresh", "other-feed"])
def test_report_wait_timeout_keeps_previous_snapshot_and_never_succeeds_with_old_empty_data(monkeypatch, database, holder):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([]))
    mock_http(monkeypatch, handler)
    old = refresh(database)
    database.test_clock[0] += timedelta(seconds=901)
    store = news_feed.FeedStore(database)
    held = store.claim(holder)
    assert held is not None
    elapsed, waits = [0.0], []
    def delayed_sleep(seconds):
        waits.append(seconds)
        elapsed[0] += 45
        database.test_clock[0] += timedelta(seconds=45)
    monkeypatch.setattr(news_feed, "time", SimpleNamespace(monotonic=lambda: elapsed[0], sleep=delayed_sleep))
    with pytest.raises(StoreError) as error:
        news_feed.get_report_feed("iran-news", language="en", database=database)
    assert error.value.code == "newsdata_timeout" and error.value.status_code == 504
    assert elapsed[0] == news_feed.MAX_REFRESH_SECONDS and len(waits) == 2
    assert len(calls) == 1
    row = store.read("iran-news")
    assert json.loads(row["snapshot_json"])["snapshot_id"] == old["snapshot_id"]
    assert row["last_successful_refresh_at"] == old["last_successful_refresh_at"]
    assert store.read(holder)["refresh_id"] == held[0]
    with database._connect() as connection:
        assert news_feed.accounted_credits(connection, local_date="2026-10-20") == 1


def test_report_wait_preserves_provider_failure_and_shared_cooldown(monkeypatch, database):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([])) if len(calls) == 1 else httpx.Response(429, json={"status": "error"})
    mock_http(monkeypatch, handler)
    old = refresh(database)
    database.test_clock[0] += timedelta(seconds=901)
    store = news_feed.FeedStore(database)
    held = store.claim("iran-news")
    assert held is not None
    monkeypatch.setattr(news_feed, "time", SimpleNamespace(monotonic=news_feed.time.monotonic,
        sleep=lambda _: news_feed._refresh(store, "iran-news", held)))
    with pytest.raises(StoreError) as error:
        news_feed.get_report_feed("iran-news", language="en", database=database)
    assert error.value.code == "newsdata_rate_limited"
    cached = news_feed.get_feed("iran-news", "en", refresh=False, database=database)
    assert error.value.reference == cached["error"]["reference"] == error.value.feed_error_reference
    assert cached["snapshot_id"] == old["snapshot_id"]
    assert store.claim("geopolitical-morning") is None
    assert len(calls) == 2


def test_report_claim_that_exhausts_wait_budget_does_not_start_a_provider_page(monkeypatch, database):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([]))
    mock_http(monkeypatch, handler)
    old = refresh(database)
    database.test_clock[0] += timedelta(seconds=901)
    elapsed = [0.0]
    original_claim = news_feed.FeedStore.claim
    def delayed_claim(store, agent_id, window=None):
        claim = original_claim(store, agent_id, window)
        if claim:
            elapsed[0] = news_feed.MAX_REFRESH_SECONDS
        return claim
    monkeypatch.setattr(news_feed.FeedStore, "claim", delayed_claim)
    monkeypatch.setattr(news_feed, "time", SimpleNamespace(monotonic=lambda: elapsed[0],
        sleep=lambda _: pytest.fail("An exhausted report budget must not start another wait.")))
    with pytest.raises(StoreError) as error:
        news_feed.get_report_feed("iran-news", language="en", database=database)
    assert error.value.code == "newsdata_timeout"
    assert len(calls) == 1
    row = news_feed.FeedStore(database).read("iran-news")
    assert row["refresh_id"] is None
    assert json.loads(row["snapshot_json"])["snapshot_id"] == old["snapshot_id"]
    with database._connect() as connection:
        assert news_feed.accounted_credits(connection, local_date="2026-10-20") == 1


def test_report_reuses_recent_empty_snapshot_while_other_feed_refreshes(monkeypatch, database):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([]))
    mock_http(monkeypatch, handler)
    old = refresh(database)
    database.test_clock[0] += timedelta(minutes=2)
    store = news_feed.FeedStore(database)
    assert store.claim("geopolitical-morning") is not None
    def unexpected_sleep(_):
        pytest.fail("A recent snapshot must remain reusable without waiting or a second provider call.")
    monkeypatch.setattr(news_feed, "time", SimpleNamespace(monotonic=news_feed.time.monotonic, sleep=unexpected_sleep))
    result = news_feed.get_report_feed("iran-news", language="en", database=database)
    assert result["snapshot_id"] == old["snapshot_id"] and result["actual_count"] == 0
    assert not result["stale"] and len(calls) == 1


def test_dashboard_ages_out_articles_but_report_preserves_its_frozen_cutoff(monkeypatch, database):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([article(pubDate="2026-10-20 00:00:01")]))
    mock_http(monkeypatch, handler)
    original = refresh(database)
    database.test_clock[0] += timedelta(minutes=2)
    dashboard = news_feed.get_feed("iran-news", "en", refresh=False, database=database)
    assert dashboard["actual_count"] == 0 and dashboard["cached_article_count"] == 1
    assert dashboard["window_excluded_from_cache"] == 1
    assert dashboard["research_window"] == original["research_window"]
    frozen_report = news_feed.get_report_feed("iran-news", research_window=original["research_window"],
        language="en", database=database)
    assert frozen_report["actual_count"] == 1 and len(calls) == 1


def test_provider_error_with_only_expired_cached_items_fails_report(monkeypatch, database):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json=page([article(pubDate="2026-10-20 00:00:01")]))
        return httpx.Response(503, json={"status": "error"})
    mock_http(monkeypatch, handler)
    refresh(database)
    database.test_clock[0] += timedelta(seconds=901)
    with pytest.raises(StoreError) as error:
        news_feed.get_report_feed("iran-news", language="en", database=database)
    assert error.value.code == "newsdata_unavailable"
    assert error.value.reference.startswith("news_")
    assert len(calls) == 2


def test_report_provider_failure_without_cache_is_raised_not_hidden(monkeypatch, database):
    mock_http(monkeypatch, lambda request: httpx.Response(503, json={"status": "error"}))
    with pytest.raises(StoreError) as error:
        news_feed.get_report_feed("iran-news", language="en", database=database)
    assert error.value.code == "newsdata_unavailable"


def test_report_custom_topic_filters_do_not_pollute_or_publish_shared_query(monkeypatch, database):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=page([article(1),
        article(2, title="Synthetic Iran shipping conflict", description="Fictional shipping blockade news.")])))
    refresh(database)
    result = news_feed.get_report_feed("iran-news", "shipping private-company-secret", language="en", database=database)
    assert result["actual_count"] == 1 and result["items"][0]["article_id"] == "synthetic-2"
    public = news_feed.get_feed("iran-news", "en", refresh=False, database=database)
    assert public["actual_count"] == 2 and "private-company-secret" not in json.dumps(public)


def test_budgeted_report_translations_cache_only_known_safe_prose(monkeypatch, database):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=page([article(1), article(2, description="")])) )
    before = refresh(database)
    translated = {**before["items"][1], "headline": "مفاوضات نووية اصطناعية", "summary": "وصف اصطناعي مترجم."}
    assert translated["article_id"] == "synthetic-1"
    invalid_empty = {**before["items"][0], "headline": "عنوان", "summary": "ملخص مخترع لنص غير موجود"}
    unknown = {**translated, "id": "newsdata:unknown"}
    bad_url = {**translated, "url": "https://elsewhere.example/wrong"}
    injected = {**translated, "headline": "https://injected.example/"}
    for value in (translated, invalid_empty, unknown, bad_url, injected):
        value.pop("headline_ar", None)
        value.pop("summary_ar", None)
    assert news_feed.apply_translations("iran-news", [translated, invalid_empty, unknown, bad_url, injected],
        "ar", database=database) == 1
    after = news_feed.get_feed("iran-news", "ar", refresh=False, database=database)
    original = next(item for item in after["items"] if item["article_id"] == "synthetic-1")
    assert original["language"] == "ar" and original["translation_status"] == "translated"
    assert original["original_headline"] == article()["title"]
    assert original["url"] == article()["link"]
    assert original["published_at"] == before["items"][1]["published_at"]


def test_cached_translation_is_not_reused_after_original_text_changes(monkeypatch, database):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page([article(1, description="Original one." if len(calls) == 1 else "Original changed.")]))
    mock_http(monkeypatch, handler)
    original = refresh(database)["items"][0]
    assert news_feed.apply_translations("iran-news", [{"id": original["id"], "headline_ar": "عنوان مترجم",
        "summary_ar": "ملخص مترجم"}], "ar", database=database) == 1
    database.test_clock[0] += timedelta(seconds=901)
    refresh(database)
    result = news_feed.get_feed("iran-news", "ar", refresh=False, database=database)
    assert result["items"][0]["translation_status"] == "original_only"
