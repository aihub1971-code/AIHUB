"""The existing registry remains the sole source of agent identity."""
import pytest
from fastapi import HTTPException

from app.agent_profiles import default_topic, get_profile, list_profiles
from app.registry import RETIRED_AGENT_IDS, get_agents


def test_profiles_preserve_all_seven_active_registry_identities():
    agents, profiles = get_agents(), list_profiles()
    assert len(profiles) == len(agents) == 7
    assert [(item["id"], item["name"]) for item in profiles] == [(item["id"], item["name"]) for item in agents]
    assert len({item["id"] for item in profiles}) == 7
    assert [item["number"] for item in profiles] == [f"{number:02}" for number in range(1, 8)]
    assert RETIRED_AGENT_IDS.isdisjoint(item["id"] for item in profiles)
    for item in profiles:
        assert item["name_ar"] and item["default_topic_ar"] and item["default_topic_en"]
        assert item["views"] == item["view_labels"]


def test_seven_domain_profiles_have_distinct_scopes_and_default_subjects():
    ids = ["ubi-research", "iran-news", "uae-eu-trade-talks", "geopolitical-morning", "education-observatory",
           "historical-reading-companion", "economic-cultural-briefing"]
    profiles = [get_profile(identifier) for identifier in ids]
    assert len({item["scope"] for item in profiles}) == 7
    assert len({item["default_topic_en"] for item in profiles}) == 7
    assert all(item["uses_retrieval"] for item in profiles)
    assert "cultur" in get_profile("economic-cultural-briefing")["scope"].lower()
    assert "geographic breadth" in get_profile("geopolitical-morning")["scope"].lower()


def test_iran_has_two_news_groups_and_separate_iran_us_uae_assessments():
    profile = get_profile("iran-news")
    assert {item["id"] for item in profile["item_sections"]} == {"conflict_diplomacy", "major_consequences"}
    assert {"iran_assessment", "us_assessment", "uae_implications"} <= {item["id"] for item in profile["sections"]}
    assert "US domestic" in profile["scope"]


def test_historical_companion_supports_scholarship_interpretations_and_qualified_lessons():
    historical = get_profile("historical-reading-companion")
    assert historical["views"][0]["title_en"] == "Sources"
    assert historical["views"][1]["title_en"] == "Reading Pack"
    assert historical["kind"] == "reading"
    assert historical["allow_background"] and historical["supplement_primary_research"]
    assert {"context", "chronology", "interpretations", "contemporary_lessons", "reading_questions"} <= {
        part["id"] for part in historical["sections"]}
    assert "books" in historical["scope"] and "scholar" in historical["scope"]
    assert "qualifying contemporary analogies" in historical["scope"]
    assert "Baghdad" in default_topic(historical["id"], "en")
    assert "بغداد" in default_topic(historical["id"], "ar")


@pytest.mark.parametrize("agent_id", sorted(RETIRED_AGENT_IDS))
def test_retired_assistants_have_no_active_profile_or_launch_default(agent_id):
    with pytest.raises(HTTPException) as error:
        get_profile(agent_id)
    assert error.value.status_code == 404
    assert error.value.detail["code"] == "unknown_agent"
    with pytest.raises(HTTPException) as error:
        default_topic(agent_id)
    assert error.value.status_code == 404


def test_all_remaining_profiles_support_both_retrieval_options():
    for profile in list_profiles():
        assert profile["uses_retrieval"]
        assert profile["retrieval_options"] == ["newsdata", "web_search"]


def test_returned_profiles_cannot_mutate_global_configuration():
    first = get_profile("iran-news")
    first["sections"][0]["title_en"] = "changed"
    first["terms"].append("invented-scope")
    second = get_profile("iran-news")
    assert second["sections"][0]["title_en"] == "Executive summary"
    assert "invented-scope" not in second["terms"]
