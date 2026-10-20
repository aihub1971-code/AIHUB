"""The existing registry remains the sole source of agent identity."""
from app.agent_profiles import default_topic, get_profile, list_profiles
from app.registry import get_agents


def test_profiles_preserve_all_thirteen_registry_identities():
    agents, profiles = get_agents(), list_profiles()
    assert len(profiles) == len(agents) == 13
    assert [(item["id"], item["name"]) for item in profiles] == [(item["id"], item["name"]) for item in agents]
    assert len({item["id"] for item in profiles}) == 13
    for item in profiles:
        assert item["name_ar"] and item["default_topic_ar"] and item["default_topic_en"]
        assert item["views"] == item["view_labels"]


def test_eight_domain_profiles_have_distinct_scopes_and_default_subjects():
    ids = ["ubi-research", "iran-news", "uae-eu-trade-talks", "geopolitical-morning", "education-observatory",
           "historical-reading-companion", "story-moodboard", "economic-cultural-briefing"]
    profiles = [get_profile(identifier) for identifier in ids]
    assert len({item["scope"] for item in profiles}) == 8
    assert len({item["default_topic_en"] for item in profiles}) == 8
    assert all(item["uses_retrieval"] for item in profiles)
    assert "cultur" in get_profile("economic-cultural-briefing")["scope"].lower()
    assert "geographic breadth" in get_profile("geopolitical-morning")["scope"].lower()


def test_iran_has_two_news_groups_and_separate_iran_us_uae_assessments():
    profile = get_profile("iran-news")
    assert {item["id"] for item in profile["item_sections"]} == {"conflict_diplomacy", "major_consequences"}
    assert {"iran_assessment", "us_assessment", "uae_implications"} <= {item["id"] for item in profile["sections"]}
    assert "US domestic" in profile["scope"]


def test_historical_and_creative_defaults_are_editable_and_views_distinct():
    historical, story = get_profile("historical-reading-companion"), get_profile("story-moodboard")
    assert historical["views"][1]["title_en"] == "Reading Pack"
    assert story["views"][1]["title_en"] == "Moodboard"
    assert historical["kind"] == "reading" and story["kind"] == "moodboard"
    assert "chronology" in {part["id"] for part in historical["sections"]}
    assert "colour_palette" in {part["id"] for part in story["sections"]}
    assert "Baghdad" in default_topic(historical["id"], "en")
    assert "أرشيف" in default_topic(story["id"], "ar")


def test_extra_utilities_never_gain_invented_retrieval_or_account_access():
    for identifier in ["mom-agent", "whatsapp-briefing", "literary-translation"]:
        profile = get_profile(identifier)
        assert profile["uses_retrieval"] is False
        assert profile["kind"] == "workspace"
        assert "user" in profile["scope"].lower()
    assert get_profile("pre-meeting-briefing")["uses_retrieval"]
    assert get_profile("decision-briefing")["uses_retrieval"]


def test_returned_profiles_cannot_mutate_global_configuration():
    first = get_profile("iran-news")
    first["sections"][0]["title_en"] = "changed"
    first["terms"].append("invented-scope")
    second = get_profile("iran-news")
    assert second["sections"][0]["title_en"] == "Executive summary"
    assert "invented-scope" not in second["terms"]
