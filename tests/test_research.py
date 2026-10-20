"""Native research engine contracts; production news dispatch is tested separately.

Synthetic provider and publisher fixtures: no live requests or current claims.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import html
import json
import socket

import httpx
import pytest
from fastapi import HTTPException

from app import research
from app.agent_profiles import get_profile


WINDOW = {"start": "2026-10-20T00:00:00+00:00", "end": "2026-10-20T12:00:00+00:00", "timezone": "UTC"}
URL = "https://publisher.example/news/synthetic-iran-talks"
TITLE = "Iran and US announce a new round of nuclear diplomacy"
DOWNLOAD_PAGE = research._download_page


@pytest.fixture(autouse=True)
def deny_publisher_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Publisher networking must use synthetic fixtures.")
    monkeypatch.setattr(research, "_download_page", blocked)


def source_record(url=URL, title=TITLE):
    return {"url": url, "title": title, "citation_annotations": [{"type": "url_citation", "url": url,
            "title": title, "start_index": 0, "end_index": 12, "part_index": 0}]}


def page_record(url=URL, title=TITLE, published="2026-10-20T10:00:00Z", *, body=None, description=None,
                extra=None, html_suffix="", article_type="NewsArticle"):
    body = body or title + ". This synthetic article reports a declared diplomatic meeting; its outcome is not established."
    node = {"@type": article_type, "url": url, "headline": title, "description": description if description is not None else title,
            "articleBody": body, "publisher": {"@type": "Organization", "name": "Synthetic Publisher"}}
    if published is not None:
        node["datePublished"] = published
    node.update(extra or {})
    content = '<html><head><script type="application/ld+json">' + json.dumps(node, ensure_ascii=False) + '</script></head>'
    content += '<body><article><h1>' + html.escape(title) + '</h1><p>' + html.escape(body) + '</p></article>' + html_suffix + '</body></html>'
    return {"url": url, "html": content, "retrieved_at": "2026-10-20T12:01:00Z", "sha256": hashlib.sha256(content.encode()).hexdigest()}


def verify(page, agent="iran-news", native=None):
    return research._verify_page(native or source_record(page["url"]), page, get_profile(agent), "Iran US conflict", research._window(WINDOW))


def reference_page(markup):
    return {"url": URL, "html": markup, "retrieved_at": "2026-10-20T12:01:00Z",
            "sha256": hashlib.sha256(markup.encode()).hexdigest()}


def native_call(action="search", sources=None, url=URL):
    detail = {"type": action}
    if action == "search":
        detail.update(query="synthetic Iran nuclear diplomacy", sources=sources if sources is not None else [{"url": url}])
    else:
        detail["url"] = url
    return {"type": "web_search_call", "id": "ws_synthetic_" + action, "status": "completed", "action": detail}


def provider_response(content, *, native=None, annotations=None, usage=True, tier=True):
    result = {"status": "completed", "output": list(native or []) + [{"type": "message", "role": "assistant", "status": "completed",
               "content": [{"type": "output_text", "text": content, "annotations": annotations or []}]}]}
    if usage:
        result["usage"] = {"input_tokens": 1200, "output_tokens": 900}
    if tier:
        result["service_tier"] = "default"
    return result


def structured_pack(profile, sources, *, items=True):
    source_ids = [source["id"] for source in sources]
    result = {"title": "Synthetic evidence briefing", "items": [],
        "sections": [{"id": section["id"], "body": "Attributed finding: a meeting was announced. Assessment: outcomes remain uncertain.",
                      "source_ids": source_ids[:1]} for section in profile["sections"]],
        "limitations": ["Synthetic fixtures are used for this test."],
        "visuals": {"palette": [], "direction": "", "reference_source_ids": []}}
    if items:
        for index, source in enumerate(sources):
            quotes = source.get("evidence_quotes") or research._source_quotes(source)
            result["items"].append({"source_id": source["id"], "supporting_source_ids": [],
                "headline": source["title"], "summary": "A synthetic publisher reports a new diplomatic step. Its outcome remains uncertain.",
                "why_it_matters": "Assessment: verified diplomatic channels can affect regional planning; concrete outcomes are unknown.",
                "evidence_quote": quotes[0], "event_key": "synthetic distinct event " + str(index),
                "section": "conflict_diplomacy" if profile["id"] == "iran-news" else "news"})
    if profile["kind"] == "moodboard":
        for section in result["sections"]:
            section["attribution"] = "reference_based" if sources else "original_creative"
        result["visuals"] = {"palette": [{"name": "Ocean", "hex": "#124578"}, {"name": "Sand", "hex": "#DCBA98"},
                                            {"name": "Ink", "hex": "#112233"}],
                             "direction": "Original creative interpretation: layer an archive grid over tidal curves.", "reference_source_ids": source_ids[:1]}
    return result


def install_transport(monkeypatch, handler):
    original = httpx.Client
    def factory(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(research.httpx, "Client", factory)


@pytest.mark.parametrize("value", ["2026-10-20", "2026-10-20T09:30:00", "2026-10-20T25:99:00Z", "2026-10-20T09:30:00Z trailing",
    "2026-10-20T09:30:00Z؛ آخر تحديث", "20 October 2026", "2026-10-20T09:30:00+99:99", "yesterday", None])
def test_publication_parser_rejects_incomplete_naive_or_malformed_timestamps(value):
    assert research._timestamp(value) is None


@pytest.mark.parametrize("value", ["2026-10-20T12:00:00+04:00", "2026-10-20T08:00:00Z", "٢٠٢٦-١٠-٢٠T١٢:٠٠:٠٠+٠٤:٠٠",
                                  "۲۰۲۶-۱۰-۲۰T۱۲:۰۰:۰۰+۰۴:۰۰", "Tue, 20 Oct 2026 08:00:00 GMT"])
def test_explicit_timestamp_zones_and_digits_share_utc_normalization(value):
    assert research._timestamp(value) == datetime(2026, 10, 20, 8, tzinfo=timezone.utc)


def test_frozen_window_is_exactly_twelve_hours_and_cutoff_is_not_recomputed():
    result = research._window(WINDOW)
    assert result["end"] == WINDOW["end"]
    assert result["display_timezone"] == "Asia/Dubai"
    for window in [{**WINDOW, "start": "2026-10-19T00:00:00Z"}, {**WINDOW, "end": "2026-10-20T12:00:00"}]:
        with pytest.raises(HTTPException) as error:
            research._window(window)
        assert error.value.detail["code"] == "invalid_research_window"
    with pytest.raises(HTTPException):
        research._window(WINDOW, "2026-10-20T11:00:00Z")


@pytest.mark.parametrize("agent,is_current", [("iran-news", True), ("geopolitical-morning", True),
                                             ("historical-reading-companion", False), ("story-moodboard", False)])
def test_current_discovery_uses_remaining_bounded_actions_without_calendar_query_tokens(agent, is_current):
    payload = research._research_payload("gpt-6.1-sol", get_profile(agent), "Synthetic topic", "ar",
                                         research._window(WINDOW), 6)
    instructions = payload["input"][0]["content"]
    assert ("Omit calendar date and year tokens" in instructions) is is_current
    assert ("within the remaining native-action budget" in instructions) is is_current
    assert payload["max_tool_calls"] == 6 and len(payload["tools"]) == 1
    assert "filters" not in payload["tools"][0]
    assert json.loads(payload["input"][1]["content"])["window"]["end"] == WINDOW["end"]
    assert research.runtime._json_bytes(payload) <= research.MAX_RESEARCH_INPUT_BYTES


@pytest.mark.parametrize("published,reason", [("2026-10-19T23:59:59Z", "outside_window"), ("2026-10-20T12:00:01Z", "future_publication"),
    ("2026-10-20", "unverified_publication_timestamp"), (None, "unverified_publication_timestamp"),
    ("2026-10-20T25:30:00Z", "unverified_publication_timestamp")])
def test_news_requires_publisher_full_timestamp_within_frozen_window(published, reason):
    source, rejection = verify(page_record(published=published))
    assert source is None and rejection == reason


@pytest.mark.parametrize("stamp", [WINDOW["start"], WINDOW["end"]])
def test_window_boundaries_are_inclusive_and_annotations_preserved(stamp):
    source, reason = verify(page_record(published=stamp))
    assert reason is None and source["role"] == "fresh"
    assert source["publication_verification"]["value"] == stamp
    assert source["publication_verification"]["verified"] is True
    assert source["publication_verification"]["page_sha256"]
    assert source["citation_annotations"][0]["start_index"] == 0


def test_date_modified_and_crawl_dates_cannot_make_old_news_fresh():
    source, reason = verify(page_record(published="2026-10-01T08:00:00Z", extra={"dateModified": "2026-10-20T11:00:00Z", "dateCreated": "2026-10-20T11:00:00Z"}))
    assert source is None and reason == "outside_window"
    source, reason = verify(page_record(published=None, extra={"dateModified": "2026-10-20T11:00:00Z"}))
    assert source is None and reason == "unverified_publication_timestamp"


def test_unrelated_sidebar_article_date_is_not_borrowed():
    page = page_record(published=None, extra={"url": "https://publisher.example/news/unrelated"})
    source, reason = verify(page)
    assert source is None and reason == "unverified_publication_timestamp"


def test_unbound_sidebar_headline_cannot_supply_this_articles_timestamp():
    page = page_record(extra={"url": None, "headline": "Iran announces a separate military development"})
    assert verify(page)[1] == "unbound_article_metadata"
    page = page_record(extra={"url": None})
    assert verify(page)[1] is None  # Headline is explicitly bound to this article's H1.


@pytest.mark.parametrize("label", ["Checking your browser - reCAPTCHA", "Just a moment...",
                                  "Verify you are human", "Attention Required! | Cloudflare"])
def test_browser_challenges_cannot_become_historical_references(label):
    markup = '<html><head><title>' + html.escape(label) + '</title></head><body><h1>' + html.escape(label)
    markup += '</h1><p>Please complete the browser verification before viewing the requested research article.</p></body></html>'
    source, reason = verify(reference_page(markup), "historical-reading-companion",
                            source_record(title="A synthetic study of ancient manuscripts"))
    assert source is None and reason == "publisher_interstitial"


def test_real_article_about_captcha_is_not_rejected_for_its_topic_or_widget():
    title = "A synthetic history of CAPTCHA and online accessibility"
    page = page_record(title=title, published="2018-01-01T08:00:00Z",
        body="This synthetic research article examines CAPTCHA design and its accessibility implications over time.",
        html_suffix='<aside><h1>Verify you are human</h1><div class="g-recaptcha">Verification widget</div></aside>')
    source, reason = verify(page, "historical-reading-companion")
    assert reason is None and source["title"] == title
    assert source["role"] == "background"


def test_reference_title_preserves_one_complete_article_h1_without_modal_headings():
    markup = ('<html><body><header><h1>Archive navigation</h1></header><article>'
              '<h1>A <em>synthetic</em> manuscript history</h1>'
              '<p>This fictional reference describes the cataloguing of manuscripts in a teaching archive.</p>'
              '</article><aside><h1>Save article to a reading list</h1><h1>Bibliographic and Citation Tools</h1>'
              '</aside></body></html>')
    source, reason = verify(reference_page(markup), "historical-reading-companion")
    assert reason is None and source["title"] == "A synthetic manuscript history"
    assert "Save article" not in source["title"] and "navigation" not in source["title"]


def test_reference_open_graph_title_precedes_generic_and_article_navigation_h1():
    title = "A synthetic study of translation manuscripts"
    markup = ('<html><head><meta property="og:title" content="' + title + '"></head><body>'
              '<h1>Archive browser</h1><article><h1>Reading options</h1>'
              '<p>This synthetic study examines how translated manuscripts were catalogued and shared.</p>'
              '</article><h1>Save article</h1></body></html>')
    source, reason = verify(reference_page(markup), "historical-reading-companion")
    assert reason is None and source["title"] == title


def test_reference_document_title_precedes_category_and_later_tools_headings():
    title = "Synthetic manuscript classification in digital archives"
    markup = ('<html><head><title>' + title + '</title></head><body>'
              '<h1>History and Philosophy of Science</h1><h1>' + title + '</h1>'
              '<p>This synthetic reference discusses the classification of historical manuscripts for teaching.</p>'
              '<h1>Bibliographic and Citation Tools</h1></body></html>')
    source, reason = verify(reference_page(markup), "historical-reading-companion")
    assert reason is None and source["title"] == title


def test_unbound_article_binding_uses_article_heading_without_later_modal_titles():
    page = page_record(extra={"url": None}, html_suffix='<aside><h1>Save article</h1><h1>Bibliographic tools</h1></aside>')
    source, reason = verify(page)
    assert reason is None and source["title"] == TITLE
    assert source["publication_verification"]["verified"] is True


def test_conflicting_publisher_dates_are_rejected_not_cherry_picked():
    page = page_record(html_suffix='<meta property="article:published_time" content="2026-10-19T12:00:00Z">')
    assert verify(page)[1] == "conflicting_publication_timestamps"


def test_visible_article_time_requires_publication_label_and_not_update():
    base = page_record(published=None)
    for label, expected in [("Published", None), ("Updated", "unverified_publication_timestamp")]:
        page = dict(base)
        page["html"] = page["html"].replace("</article>", f'<p>{label} <time datetime="2026-10-20T09:00:00Z">09:00 UTC</time></p></article>')
        assert verify(page)[1] == expected
    page = dict(base)
    page["html"] += '<footer>Published <time datetime="2026-10-20T09:00:00Z">Today</time></footer>'
    assert verify(page)[1] == "unverified_publication_timestamp"


def test_liveblog_needs_new_entry_publication_not_its_last_update():
    url = "https://publisher.example/news/liveblog/2026/10/20/synthetic-iran"
    page = page_record(url=url, article_type="LiveBlogPosting", published="2026-10-10T12:00:00Z",
        extra={"dateModified": "2026-10-20T11:00:00Z"})
    assert verify(page)[1] == "unverified_live_entry"
    update = {"@type": "BlogPosting", "headline": "Tehran announces nuclear talks with Washington",
              "datePublished": "2026-10-20T11:00:00Z", "articleBody": "This synthetic entry reports new nuclear talks and explicitly attributes the announcement to officials."}
    page = page_record(url=url, article_type="LiveBlogPosting", published="2026-10-10T12:00:00Z", extra={"liveBlogUpdate": [update]})
    source, reason = verify(page)
    assert reason is None and source["source_kind"] == "live_coverage"
    assert source["published_at"] == "2026-10-20T11:00:00+00:00"
    assert "liveBlogUpdate[0]" in source["publication_verification"]["path"]
    assert source["title"] == update["headline"]


def test_irrelevant_live_entry_does_not_inherit_parent_conflict_relevance():
    update = {"headline": "Local football club signs a new player", "datePublished": "2026-10-20T11:00:00Z",
              "articleBody": "This unrelated synthetic update concerns a sporting result and transfer negotiations."}
    page = page_record(article_type="LiveBlogPosting", extra={"liveBlogUpdate": [update]})
    assert verify(page)[1] == "irrelevant_live_entry"


def test_iran_relevance_requires_direct_publisher_lead_not_incidental_body_keyword():
    title = "Congressional candidates publish quarterly fundraising totals"
    page = page_record(title=title, body=title + ". This fictional story mentions Iran war in a long list of voter concerns.")
    assert verify(page)[1] == "irrelevant"
    title = "Iranian business exhibition opens in Tehran"
    assert verify(page_record(title=title))[1] == "irrelevant"  # 'business' is not 'US'.
    assert verify(page_record(title="Tehran announces nuclear negotiations with Washington"))[1] is None
    title = "Congressional candidate discusses quarterly fundraising and local schools"
    page = page_record(title=title, description="The interview includes questions about Iran war among many voter concerns.")
    assert verify(page)[1] == "irrelevant"


@pytest.mark.parametrize("title", ["Brent rises as attacks on ships raise supply concerns",
    "Crude climbs as the conflict disrupts regional transport", "Freight costs rise after attacks on tankers",
    "Fuel prices rise as Gulf supply routes come under pressure", "برنت يرتفع مع تعطل حركة السفن"])
def test_iran_consequences_keep_explicit_publisher_conflict_connection(title):
    description = "Iran-linked military attacks have raised concerns over regional energy shipments."
    assert verify(page_record(title=title, description=description))[1] is None
    assert verify(page_record(title=title, description="Quarterly business results and ordinary seasonal demand.",
        body=title + ". An unrelated paragraph mentions Iran war among many historical risks."))[1] == "irrelevant"


@pytest.mark.parametrize("title,agent", [("Irán anuncia negociaciones nucleares con Estados Unidos", "iran-news"),
    ("Une nouvelle politique pour les écoles et universités", "education-observatory"),
    ("Nuevas negociaciones para poner fin a la guerra", "geopolitical-morning"),
    ("Une initiative économique pour les musées régionaux", "economic-cultural-briefing")])
def test_relevant_local_language_headlines_are_not_forced_to_english(title, agent):
    assert verify(page_record(title=title), agent)[1] is None


def test_uae_eu_agent_requires_both_parties_and_trade_link():
    title = "UAE and European Union launch a new trade negotiation round"
    assert verify(page_record(title=title), "uae-eu-trade-talks")[1] is None
    assert verify(page_record(title="UAE university opens a European art exhibition"), "uae-eu-trade-talks")[1] == "irrelevant"


@pytest.mark.parametrize("agent,role", [("historical-reading-companion", "background"), ("story-moodboard", "reference")])
def test_historical_material_is_background_not_current_news(agent, role):
    source, reason = verify(page_record(title="An archive of Abbasid translation manuscripts", published="2018-01-01T08:00:00Z"), agent)
    assert reason is None and source["role"] == role
    source, reason = verify(page_record(title="An archive of Abbasid translation manuscripts", published=None), agent)
    assert reason is None and source["role"] == role
    assert source["publication_verification"]["verified"] is False


def test_arbitrary_publisher_is_allowed_but_model_prose_url_is_not_authorization():
    response = {"native_calls": [native_call(sources=[{"url": "https://independent.example/news/verified"}])],
                "parts": [{"text": "Read https://invented.example/news/story", "annotations": []}]}
    assert [item["url"] for item in research._discovered(response)] == ["https://independent.example/news/verified"]
    assert verify(page_record(url="https://www.reuters.com/world/synthetic-iran-evidence"))[1] is None


def test_native_url_annotations_and_open_actions_preserve_source_authentication():
    response = {"native_calls": [native_call("open_page")], "parts": [{"text": "Synthetic note",
        "annotations": [{"type": "url_citation", "url": URL, "title": TITLE, "start_index": 0, "end_index": 14}]}]}
    items = research._discovered(response)
    assert len(items) == 1
    assert items[0]["citation_annotations"][0]["part_index"] == 0


def test_discovery_interleaves_publishers_before_bounded_page_inspection():
    sources = [{"url": f"https://first.example/news/synthetic-{index}"} for index in range(45)]
    sources += [{"url": "https://second.example/news/one"}, {"url": "https://third.example/news/one"}]
    result = research._discovered({"native_calls": [native_call(sources=sources)], "parts": []})
    assert len(result) == research.MAX_PUBLISHER_PAGES
    assert result[1]["url"].startswith("https://second.example")
    assert result[2]["url"].startswith("https://third.example")


def test_cited_and_opened_articles_survive_background_source_overflow():
    background = [{"url": f"https://background.example/reference/{index}"} for index in range(90)]
    cited = "https://news.example/article/current-development"
    opened = "https://official.example/releases/announced-today"
    response = {"native_calls": [native_call(sources=background), native_call("open_page", url=opened)],
        "parts": [{"text": "Attributed note", "annotations": [{"type": "url_citation", "url": cited,
            "title": TITLE, "start_index": 0, "end_index": 10}]}]}
    urls = [item["url"] for item in research._discovered(response)]
    assert cited in urls and opened in urls
    assert len(urls) == research.MAX_PUBLISHER_PAGES


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1", "192.0.2.1"])
def test_publisher_dns_refuses_private_metadata_loopback_or_reserved_ips(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))])
    with pytest.raises(ValueError, match="unsafe_or_unresolved_destination"):
        research._public_addresses("publisher.example")


def test_mixed_public_and_private_dns_answers_are_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", 443))])
    with pytest.raises(ValueError):
        research._public_addresses("publisher.example")


def install_download_connections(monkeypatch, responses):
    hosts, requests = [], []
    class Response:
        def __init__(self, data):
            self.data, self.status = data, data.get("status", 200)
        def getheader(self, key, default=None):
            return self.data.get("headers", {}).get(key, default)
        def read(self, count):
            return self.data.get("body", b"<html>synthetic</html>")[:count]
    class Connection:
        def __init__(self, hostname, address):
            hosts.append((hostname, address))
        def request(self, method, path, headers):
            requests.append((method, path, headers))
        def getresponse(self):
            return Response(responses.pop(0))
        def close(self):
            pass
    monkeypatch.setattr(research, "_public_addresses", lambda hostname: ["93.184.216.34"])
    monkeypatch.setattr(research, "_PinnedHTTPS", Connection)
    return hosts, requests


def test_https_fetch_only_starts_from_native_attested_urls(monkeypatch):
    hosts, _ = install_download_connections(monkeypatch, [])
    with pytest.raises(ValueError, match="unattested_url"):
        DOWNLOAD_PAGE(URL, {"https://different.example/news/real"})
    with pytest.raises(ValueError, match="unsafe_destination"):
        DOWNLOAD_PAGE("http://publisher.example/news/article", {"http://publisher.example/news/article"})
    assert not hosts


def test_https_fetch_checks_redirect_host_and_preserves_content_hash(monkeypatch):
    target = "https://www.publisher.example/news/synthetic-canonical"
    hosts, requests = install_download_connections(monkeypatch, [
        {"status": 302, "headers": {"Location": target}},
        {"headers": {"Content-Type": "text/html; charset=utf-8"}, "body": b"<html>actual fixture</html>"}])
    result = DOWNLOAD_PAGE(URL, {URL})
    assert result["url"] == target and result["redirects"] == [target]
    assert result["sha256"] == hashlib.sha256(b"<html>actual fixture</html>").hexdigest()
    assert hosts == [("publisher.example", "93.184.216.34"), ("www.publisher.example", "93.184.216.34")]
    assert all(request[2]["Accept-Encoding"] == "identity" for request in requests)


def test_unattested_cross_domain_redirect_is_not_followed(monkeypatch):
    hosts, _ = install_download_connections(monkeypatch, [{"status": 302, "headers": {"Location": "https://attacker.example/news/redirect"}}])
    with pytest.raises(ValueError, match="unattested_redirect"):
        DOWNLOAD_PAGE(URL, {URL})
    assert len(hosts) == 1


@pytest.mark.parametrize("response,reason", [
    ({"headers": {"Content-Type": "application/pdf"}}, "unsupported_publisher_content"),
    ({"headers": {"Content-Type": "text/html", "Content-Encoding": "gzip"}}, "unsupported_content_encoding"),
    ({"headers": {"Content-Type": "text/html", "Content-Length": "1000001"}}, "publisher_page_too_large"),
    ({"status": 403}, "publisher_unavailable")])
def test_publisher_fetch_size_type_and_blocked_access_are_bounded(monkeypatch, response, reason):
    install_download_connections(monkeypatch, [response])
    with pytest.raises(ValueError, match=reason):
        DOWNLOAD_PAGE(URL, {URL})


def test_tls_connection_is_pinned_to_validated_ip_but_verifies_original_hostname(monkeypatch):
    called = []
    class Sock:
        def close(self):
            pass
    sock = Sock()
    monkeypatch.setattr(socket, "create_connection", lambda address, timeout: called.append((address, timeout)) or sock)
    connection = research._PinnedHTTPS("publisher.example", "93.184.216.34")
    class TLS:
        def wrap_socket(self, received, server_hostname):
            assert received is sock and server_hostname == "publisher.example"
            return sock
    connection._context = TLS()
    connection.connect()
    assert called == [(("93.184.216.34", 443), research.PAGE_TIMEOUT_SECONDS)]


def test_duplicated_publisher_content_and_wire_copies_are_one_source(monkeypatch):
    first, second = URL, "https://another.example/news/synthetic-wire-copy"
    pages = {url: page_record(url=url, body="Reuters: " + TITLE + ". A synthetic shared wire report provides the same factual text.") for url in (first, second)}
    monkeypatch.setattr(research, "_download_page", lambda url, allowed: pages[url])
    sources, excluded = research._verified_sources([source_record(url) for url in pages], get_profile("iran-news"), "Iran", research._window(WINDOW))
    assert len(sources) == 1 and excluded["duplicate_or_syndicated"] == 1
    assert sources[0]["syndication_agency"] == "Reuters"
    assert len(sources[0]["syndicated_sources"]) == 1


def test_page_validation_finishes_before_item_ceiling_is_applied(monkeypatch):
    countries = ["Japan", "Brazil", "Chile", "Kenya", "Ghana", "Egypt", "France", "Italy", "Spain", "Poland", "India", "Canada",
                 "Norway", "Sweden", "Finland", "Pakistan", "Indonesia", "Singapore", "Vietnam", "Nigeria", "Peru", "Bolivia", "Nepal", "Morocco", "Mexico"]
    pages = {}
    for index in range(10):
        url = f"https://old.example/news/outside-{index}"
        pages[url] = page_record(url=url, title="Old war report " + str(index), published="2026-10-01T10:00:00Z")
    for country in countries:
        url = f"https://regional.example/news/{country.casefold()}"
        title = country + " diplomacy: " + (country.casefold() + " ") * 4
        pages[url] = page_record(url=url, title=title)
    monkeypatch.setattr(research, "_download_page", lambda url, allowed: pages[url])
    result, exclusions = research._verified_sources([source_record(url) for url in pages], get_profile("geopolitical-morning"), "World diplomacy", research._window(WINDOW))
    assert len(result) == 25 and exclusions["outside_window"] == 10


def test_structured_pack_cannot_change_source_link_or_date():
    profile = get_profile("iran-news")
    source, _ = verify(page_record())
    data = structured_pack(profile, [source])
    data["items"][0]["url"] = "https://invented.example/article"
    validated, exclusions = research._validated_pack(json.dumps(data), profile, [source], research._window(WINDOW))
    assert not validated["items"] and exclusions["invalid_item_schema"] == 1
    data = structured_pack(profile, [source])
    data["items"][0]["evidence_quote"] = "A paraphrase not present in publisher evidence."
    assert research._validated_pack(json.dumps(data), profile, [source], research._window(WINDOW))[1]["unsupported_evidence_quote"] == 1


def test_invalid_factual_section_reference_or_link_fails_safe():
    profile = get_profile("iran-news")
    source, _ = verify(page_record())
    data = structured_pack(profile, [source])
    data["sections"][0]["source_ids"] = ["fabricated_source"]
    with pytest.raises(HTTPException, match="evidence contract"):
        research._validated_pack(json.dumps(data), profile, [source], research._window(WINDOW))
    data = structured_pack(profile, [source])
    data["sections"][0]["body"] = "Read https://invented.example/current"
    with pytest.raises(HTTPException):
        research._validated_pack(json.dumps(data), profile, [source], research._window(WINDOW))


@pytest.mark.parametrize("heading", ["## السياق التاريخي", "###   Historical   context: ###",
                                     "## **السياق التاريخي:**", "Historical context\n---"])
def test_report_section_strips_its_repeated_leading_heading_only(heading):
    profile = get_profile("historical-reading-companion")
    source, _ = verify(page_record(title="Synthetic archive of translation manuscripts", published=None), profile["id"])
    data = structured_pack(profile, [source], items=False)
    original = "A source-attributed historical interpretation.\n\n### A distinct subtopic\nKeep this supporting discussion."
    data["sections"][0]["body"] = heading + "\n\n" + original
    pack, _ = research._validated_pack(json.dumps(data), profile, [source], research._window(WINDOW))
    assert pack["sections"][0]["body"] == original
    assert pack["sections"][0]["source_ids"] == [source["id"]]
    assert len(pack["sections"]) == len(profile["sections"])


@pytest.mark.parametrize("body", ["## Historical context and debate\n\nKeep this discussion.",
    "## Historical context?\n\nThis is a specific question.",
    "Historical context is necessary to interpret the evidence.\n\nKeep this paragraph."])
def test_section_preserves_nonmatching_headings_and_normal_prose(body):
    profile = get_profile("historical-reading-companion")
    source, _ = verify(page_record(title="Synthetic archive of translation manuscripts", published=None), profile["id"])
    data = structured_pack(profile, [source], items=False)
    data["sections"][0]["body"] = body
    pack, _ = research._validated_pack(json.dumps(data), profile, [source], research._window(WINDOW))
    assert pack["sections"][0]["body"] == body


def test_twenty_limit_applies_only_after_model_item_validation_and_dedup():
    profile = get_profile("iran-news")
    source, _ = verify(page_record())
    sources = [{**source, "id": f"src_unique_{index}", "title": f"Iran nuclear diplomacy distinct development {index}",
                "url": f"https://publisher.example/news/unique-{index}"} for index in range(25)]
    data = structured_pack(profile, sources)
    invalid = {**data["items"][0], "source_id": "unknown"}
    data["items"] = [invalid] * 5 + [data["items"][0]] * 3 + data["items"]
    pack, exclusions = research._validated_pack(json.dumps(data), profile, sources, research._window(WINDOW))
    assert len(pack["items"]) == 20
    assert exclusions["nonfresh_item"] == 5 and exclusions["duplicate_event"] == 3
    assert exclusions["after_validation_item_limit"] == 5
    assert pack["items"][-1]["url"].endswith("unique-19")


@pytest.mark.parametrize("language,title", [("en", "No verified developments in the last 12 hours"),
                                           ("ar", "لا توجد مستجدات موثقة خلال آخر 12 ساعة")])
def test_no_evidence_is_concise_dated_and_does_not_make_a_synthesis_request(monkeypatch, language, title):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-real-key")
    requests, stages = [], []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=provider_response("No inspectable source was returned.", native=[native_call(sources=[])]))
    install_transport(monkeypatch, handler)
    result = research._generate_native_report("iran-news", language=language, research_window=WINDOW, progress=stages.append)
    assert len(requests) == 2 and result["items"] == []
    assert all(request["max_tool_calls"] == 3 for request in requests)
    assert "coverage_feedback" in json.loads(requests[1]["input"][1]["content"])
    assert result["title"] == title
    assert [section["id"] for section in result["sections"]] == ["executive_summary"]
    body = result["sections"][0]["body"]
    assert len(body) < 220 and body.count(WINDOW["start"]) == body.count(WINDOW["end"]) == 1
    assert body not in result["limitations"]
    assert result["sections"][0]["source_ids"] == []
    assert result["status"] == "no_evidence"
    assert result["cost_tracking_complete"] is True
    assert result["usage"]["web_search_calls"] == 2
    assert result["web_search"]["request_count"] == 2
    assert stages == ["searching", "checking_sources", "searching", "checking_sources", "analyzing"]


def test_full_pipeline_uses_native_evidence_publisher_timestamp_and_one_synthesis(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-real-key")
    monkeypatch.setattr(research, "_download_page", lambda url, allowed: page_record(url=url))
    profile, requests = get_profile("iran-news"), []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["tools"]:
            return httpx.Response(200, json=provider_response("Synthetic attributed note.", native=[native_call(), native_call("open_page"), native_call("find_in_page")],
                annotations=[{"type": "url_citation", "url": URL, "title": TITLE, "start_index": 0, "end_index": 26}]))
        sources = json.loads(payload["input"][1]["content"])["sources"]
        return httpx.Response(200, json=provider_response(json.dumps(structured_pack(profile, sources))))
    install_transport(monkeypatch, handler)
    result = research._generate_native_report("iran-news", language="en", research_window=WINDOW)
    assert len(requests) == 2
    assert requests[0]["max_tool_calls"] == 3
    assert requests[0]["tools"][0]["external_web_access"] is True
    assert "filters" not in requests[0]["tools"][0]
    assert requests[1]["tools"] == [] and requests[1]["text"]["format"]["strict"] is True
    assert result["schema_version"] == 2 and result["item_count"] == 1
    assert len(result["sections"]) == len(profile["sections"])
    assert result["items"][0]["url"] == URL and result["items"][0]["published_at"] == "2026-10-20T10:00:00+00:00"
    assert result["items"][0]["event_date"] is None
    assert result["sources"][0]["publication_verification"]["method"] == "publisher_jsonld_datePublished"
    assert result["_research_provenance"]["parts"][0]["annotations"][0]["start_index"] == 0
    assert result["usage"]["input_tokens"] == 2400 and result["usage"]["output_tokens"] == 1800
    assert result["usage"]["native_actions"] == 3 and result["usage"]["web_search_calls"] == 1
    assert result["estimated_cost_usd"] == pytest.approx(.0415)
    assert URL in result["response"]


def test_zero_coverage_gets_one_focused_stage_with_shared_page_and_action_bounds(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-real-key")
    old = [f"https://archive.example/news/old-{index}" for index in range(40)]
    new = [f"https://news.example/news/iran-followup-{index}" for index in range(40)]
    downloads, requests = [], []
    def download(url, allowed):
        assert url in allowed
        downloads.append(url)
        return page_record(url=url, title=TITLE if url == new[0] else "Iran nuclear archive " + url.rsplit("-", 1)[-1],
            published="2026-10-20T10:00:00Z" if url == new[0] else "2026-10-01T10:00:00Z")
    monkeypatch.setattr(research, "_download_page", download)
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(200, json=provider_response("Old material.",
                native=[native_call(sources=[{"url": url} for url in old]), native_call("open_page", url=old[0])]))
        if len(requests) == 2:
            data = json.loads(payload["input"][1]["content"])
            assert data["window"]["start"] == WINDOW["start"] and data["window"]["end"] == WINDOW["end"]
            assert data["coverage_feedback"]["excluded_source_counts"] == {"outside_window": research.FIRST_NEWS_PAGE_LIMIT}
            assert data["coverage_feedback"]["previous_queries"] == ["synthetic Iran nuclear diplomacy"]
            return httpx.Response(200, json=provider_response("New attributed finding.",
                native=[native_call(sources=[{"url": url} for url in new]), native_call("open_page", url=new[0])],
                annotations=[{"type": "url_citation", "url": new[0], "title": TITLE, "start_index": 0, "end_index": 22}]))
        sources = json.loads(payload["input"][1]["content"])["sources"]
        return httpx.Response(200, json=provider_response(json.dumps(structured_pack(get_profile("iran-news"), sources))))
    install_transport(monkeypatch, handler)
    result = research._generate_native_report("iran-news", language="en", research_window=WINDOW)
    assert len(requests) == 3 and [p["max_tool_calls"] for p in requests[:2]] == [3, 3]
    assert len(downloads) == len(set(downloads)) == research.MAX_PUBLISHER_PAGES
    assert result["verification_diagnostics"]["discovered_url_count"] <= research.MAX_DISCOVERY_URLS
    assert result["verification_diagnostics"]["inspected_url_count"] == research.MAX_PUBLISHER_PAGES
    assert result["verification_diagnostics"]["excluded_sources"] == {"outside_window": 39}
    assert result["items"][0]["url"] == new[0]
    assert result["sources"][0]["citation_annotations"][0]["part_index"] == 1
    assert len(result["_research_provenance"]["parts"]) == 2
    assert len(result["_research_provenance"]["native_actions"]) == 4
    assert result["usage"]["native_actions"] == 4 and result["web_search"]["request_count"] == 2
    assert result["cost_tracking_complete"] is True


def test_failed_focused_stage_is_never_retried_or_sent_to_synthesis(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-real-key")
    requests = []
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json=provider_response("No candidates.", native=[native_call(sources=[])]))
        return httpx.Response(429, json={"error": {"message": "private provider diagnostic"}})
    install_transport(monkeypatch, handler)
    with pytest.raises(HTTPException) as error:
        research._generate_native_report("iran-news", research_window=WINDOW)
    assert len(requests) == 2 and error.value.detail["code"] == "openai_rate_limited"


def test_focused_feedback_trims_to_reserved_payload_without_changing_window():
    feedback = {"excluded_source_counts": {"outside_window": 24}, "previous_queries": ["q" * 400] * 6,
        "inspected_urls": ["https://publisher.example/" + "x" * 1400 + str(index) for index in range(8)]}
    payload = research._research_payload("gpt-6.1-sol", get_profile("iran-news"), "م" * 8000, "ar",
        research._window(WINDOW), 3, coverage_feedback=feedback)
    assert research.runtime._json_bytes(payload) <= research.MAX_RESEARCH_INPUT_BYTES
    data = json.loads(payload["input"][1]["content"])
    assert data["topic"] == "م" * 8000 and data["window"]["end"] == WINDOW["end"]


def test_focused_feedback_preserves_native_multi_query_searches():
    response = {"native_calls": [{"action": {"type": "search", "queries": ["Iran diplomacy latest", "إيران أخبار"]}}]}
    feedback = research._research_feedback(response, {"outside_window": 2}, [])
    assert feedback["previous_queries"] == ["Iran diplomacy latest", "إيران أخبار"]


@pytest.mark.parametrize("missing", ["usage", "tier"])
def test_unknown_stage_usage_or_actual_tier_retains_full_reservation(monkeypatch, missing):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-real-key")
    install_transport(monkeypatch, lambda request: httpx.Response(200, json=provider_response("No source.", native=[native_call(sources=[])],
        usage=missing != "usage", tier=missing != "tier")))
    result = research._generate_native_report("iran-news", research_window=WINDOW)
    assert result["estimated_cost_usd"] is None and result["cost_tracking_complete"] is False


def test_native_cap_refusal_incomplete_and_unexpected_tools_never_retry(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-real-key")
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=provider_response("Too many actions.", native=[native_call()] * 7))
    install_transport(monkeypatch, handler)
    with pytest.raises(HTTPException) as error:
        research._generate_native_report("iran-news", research_window=WINDOW)
    assert error.value.detail["code"] == "search_call_limit_exceeded" and len(calls) == 1


def test_provider_failure_is_sanitized_and_not_retried(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-real-key")
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"message": "secret echoed must never be public"}})
    install_transport(monkeypatch, handler)
    with pytest.raises(HTTPException) as error:
        research._generate_native_report("iran-news", research_window=WINDOW)
    assert error.value.status_code == 429 and len(calls) == 1
    assert "secret" not in json.dumps(error.value.detail)


@pytest.mark.parametrize("agent", ["mom-agent", "whatsapp-briefing", "literary-translation", "pre-meeting-briefing"])
def test_retired_assistants_cannot_estimate_or_start_new_research(agent):
    for operation in (research.estimate_reservation, research.generate_report):
        with pytest.raises(HTTPException) as error:
            operation(agent, language="en", research_window=WINDOW)
        assert error.value.status_code == 404


def test_reservation_bounds_cover_all_actions_full_payload_and_output(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-real-key")
    estimate = research._estimate_native_reservation("iran-news", research_window=WINDOW)
    assert estimate["max_native_actions"] == estimate["max_web_search_calls"] == 6
    assert estimate["max_model_calls"] == 3
    assert estimate["research_stage_action_limits"] == [3, 3]
    assert estimate["research_input_usd_per_million"] == "5.00"
    assert estimate["research_output_usd_per_million"] == "15.0"
    assert estimate["report_input_tokens_bound"] == research.MAX_SYNTHESIS_INPUT_BYTES + research.runtime.PROVIDER_INPUT_OVERHEAD_TOKENS
    expected = (Decimal(estimate["research_input_tokens_bound"]) * 5 + Decimal(16384) * 15 + Decimal(estimate["report_input_tokens_bound"]) * Decimal("2.5") + Decimal(8192) * 10) / 1000000 + Decimal(".06")
    assert estimate["estimated_cost_usd"] == pytest.approx(float(expected))


def test_custom_model_has_no_unverified_long_context_pricing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-real-key")
    monkeypatch.setenv("OPENAI_MODEL_NAME", "synthetic-custom-model")
    with pytest.raises(HTTPException):
        research._estimate_native_reservation("iran-news", research_window=WINDOW)
    monkeypatch.setenv("OPENAI_PRICING_MODEL", "synthetic-custom-model")
    monkeypatch.setenv("OPENAI_INPUT_USD_PER_MILLION", "2")
    monkeypatch.setenv("OPENAI_OUTPUT_USD_PER_MILLION", "10")
    assert research._estimate_native_reservation("iran-news", research_window=WINDOW)["max_native_actions"] == 1


def test_synthesis_context_is_bounded_before_call_and_untrusted_prior_report_is_whitelisted():
    source, _ = verify(page_record())
    sources = [{**source, "id": f"src_{index}", "excerpt": "Very long synthetic evidence. " * 2000} for index in range(40)]
    previous = {"agent_id": "iran-news", "report_id": "rpt_synthetic", "secret": "never copy", "items": "malformed",
                "sections": [{"id": "executive_summary", "body": "Earlier saved analysis. " * 1000}]}
    payload, selected = research._synthesis_payload("gpt-6.1-sol", get_profile("iran-news"), "Iran", "en", research._window(WINDOW), sources, previous)
    assert research.runtime._json_bytes(payload) <= research.MAX_SYNTHESIS_INPUT_BYTES
    assert selected
    assert "never copy" not in json.dumps(payload)
    assert research._previous({**previous, "agent_id": "other"}, get_profile("iran-news")) is None


def test_moodboard_requires_concrete_hex_palette_and_reference_citations():
    source, _ = verify(page_record(title="Archive of coastal architectural drawings", published=None), "story-moodboard")
    profile = get_profile("story-moodboard")
    raw = structured_pack(profile, [source], items=False)
    result, _ = research._validated_pack(json.dumps(raw), profile, [source], research._window(WINDOW))
    assert len(result["visuals"]["palette"]) == 3
    raw["visuals"]["palette"][0]["hex"] = "javascript:bad"
    with pytest.raises(HTTPException):
        research._validated_pack(json.dumps(raw), profile, [source], research._window(WINDOW))


@pytest.mark.parametrize("language,label", [("en", "Original creative interpretation"), ("ar", "تصور إبداعي أصلي")])
def test_referenced_moodboard_keeps_original_scenes_separate_from_cited_inspiration(language, label):
    profile = get_profile("story-moodboard")
    source, _ = verify(page_record(title="Synthetic archive of coastal drawings", published=None), profile["id"])
    raw = structured_pack(profile, [source], items=False)
    raw["sections"][0].update(attribution="original_creative", source_ids=[],
                              body="An imagined archivist follows fictional memories carried by an invented tide.")
    result, _ = research._validated_pack(json.dumps(raw), profile, [source], research._window(WINDOW), language=language)
    original, reference = result["sections"][:2]
    assert original["attribution"] == "original_creative" and original["source_ids"] == []
    assert original["body"].startswith("**" + label + "**\n\n")
    assert reference["attribution"] == "reference_based" and reference["source_ids"] == [source["id"]]
    assert len(result["sections"]) == len(profile["sections"])


@pytest.mark.parametrize("attribution,has_citation,reason", [
    ("reference_based", False, "uncited_section"),
    ("original_creative", True, "creative_section_with_citations"),
])
def test_moodboard_classification_never_substitutes_for_reference_attribution(attribution, has_citation, reason, caplog):
    profile = get_profile("story-moodboard")
    source, _ = verify(page_record(title="Synthetic archive of coastal drawings", published=None), profile["id"])
    raw = structured_pack(profile, [source], items=False)
    raw["sections"][0].update(attribution=attribution, source_ids=[source["id"]] if has_citation else [])
    with pytest.raises(HTTPException) as error:
        research._validated_pack(json.dumps(raw), profile, [source], research._window(WINDOW))
    assert error.value.detail["code"] == "invalid_research_report"
    assert caplog.messages[-1] == "research_report_rejected agent_id=story-moodboard reason=" + reason


@pytest.mark.parametrize("agent", ["iran-news", "historical-reading-companion"])
def test_noncreative_factual_sections_still_require_their_own_source_ids(agent):
    profile = get_profile(agent)
    source, _ = verify(page_record(), agent)
    raw = structured_pack(profile, [source])
    raw["sections"][0]["source_ids"] = []
    with pytest.raises(HTTPException) as error:
        research._validated_pack(json.dumps(raw), profile, [source], research._window(WINDOW))
    assert error.value.detail["code"] == "invalid_research_report"


def test_source_free_schema_forces_empty_reference_arrays_and_exposes_validator_bounds():
    profile = get_profile("story-moodboard")
    payload, _ = research._synthesis_payload("gpt-6.1-sol", profile, "An imagined archive", "en",
                                           research._window(WINDOW), [], None)
    schema = payload["text"]["format"]["schema"]["properties"]
    assert schema["items"]["maxItems"] == 0
    sections = schema["sections"]["items"]["properties"]
    assert sections["source_ids"]["maxItems"] == 0
    assert sections["attribution"]["enum"] == ["original_creative"]
    visuals = schema["visuals"]["properties"]
    assert visuals["reference_source_ids"]["maxItems"] == 0
    assert "no_verified_source" not in json.dumps(payload)
    assert (visuals["palette"]["minItems"], visuals["palette"]["maxItems"]) == (3, 6)
    assert visuals["palette"]["items"]["properties"]["hex"]["pattern"] == "^#[0-9a-fA-F]{6}$"
    limit = visuals["direction"]["maxLength"]
    assert limit == 1800
    raw = structured_pack(profile, [], items=False)
    raw["visuals"]["direction"] = "A" * limit
    result, _ = research._validated_pack(json.dumps(raw), profile, [], research._window(WINDOW))
    assert len(result["visuals"]["direction"]) == limit
    raw["visuals"]["direction"] += "B"
    with pytest.raises(HTTPException):
        research._validated_pack(json.dumps(raw), profile, [], research._window(WINDOW))


@pytest.mark.parametrize("malformed", [False, True])
def test_report_rejection_logs_only_whitelisted_reason_and_agent(malformed, caplog):
    profile = get_profile("story-moodboard")
    raw = structured_pack(profile, [], items=False)
    private = "https://private.example/DO_NOT_LOG_PRIVATE_SOURCE_OR_OUTPUT"
    raw["sections"][0]["body"] = private
    content = "{\"" + private if malformed else json.dumps(raw)
    with pytest.raises(HTTPException) as error:
        research._validated_pack(content, profile, [], research._window(WINDOW))
    reason = "invalid_report_schema" if malformed else "untrusted_link_or_html"
    assert caplog.messages[-1] == "research_report_rejected agent_id=story-moodboard reason=" + reason
    assert private not in caplog.text and "DO_NOT_LOG" not in json.dumps(error.value.detail)


def test_reference_empty_moodboard_still_produces_explicit_original_creative_pack(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-real-key")
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["tools"]:
            return httpx.Response(200, json=provider_response("No factual inspiration reference was returned.", native=[native_call(sources=[])]))
        pack = structured_pack(get_profile("story-moodboard"), [], items=False)
        for section in pack["sections"]:
            section["body"] = "Original creative interpretation: an imagined archivist follows a tide of memories."
        return httpx.Response(200, json=provider_response(json.dumps(pack)))
    install_transport(monkeypatch, handler)
    result = research.generate_report("story-moodboard", language="en", research_window=WINDOW)
    assert len(requests) == 2 and result["status"] == "complete"
    assert result["sources"] == result["items"] == []
    assert result["factual_basis"] == "creative_original"
    assert all(section["attribution"] == "original_creative" for section in result["sections"])
    assert len(result["visuals"]["palette"]) == 3
    assert any("no factual inspiration references" in item.lower() for item in result["limitations"])
