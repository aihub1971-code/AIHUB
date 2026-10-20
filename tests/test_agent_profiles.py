"""The existing registry remains the sole source of agent identity."""
import pytest
from fastapi import HTTPException

from app.agent_profiles import default_topic, get_profile, list_profiles
from app.registry import RETIRED_AGENT_IDS, get_agents


def test_profiles_preserve_all_nine_active_registry_identities():
    agents, profiles = get_agents(), list_profiles()
    assert len(profiles) == len(agents) == 9
    assert [(item["id"], item["name"]) for item in profiles] == [(item["id"], item["name"]) for item in agents]
    assert len({item["id"] for item in profiles}) == 9
    assert [item["number"] for item in profiles] == [f"{number:02}" for number in range(1, 10)]
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


@pytest.mark.parametrize("agent_id", sorted(RETIRED_AGENT_IDS))
def test_retired_assistants_have_no_active_profile_or_launch_default(agent_id):
    with pytest.raises(HTTPException) as error:
        get_profile(agent_id)
    assert error.value.status_code == 404
    assert error.value.detail["code"] == "unknown_agent"
    with pytest.raises(HTTPException) as error:
        default_topic(agent_id)
    assert error.value.status_code == 404


def test_all_remaining_profiles_support_their_research_workspaces():
    assert all(profile["uses_retrieval"] for profile in list_profiles())


def test_returned_profiles_cannot_mutate_global_configuration():
    first = get_profile("iran-news")
    first["sections"][0]["title_en"] = "changed"
    first["terms"].append("invented-scope")
    second = get_profile("iran-news")
    assert second["sections"][0]["title_en"] == "Executive summary"
    assert "invented-scope" not in second["terms"]
