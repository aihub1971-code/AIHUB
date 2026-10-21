"""Bounded, explicit web discovery shared by feed and report workflows.

Discovery uses the existing native tool/source attestation and publisher-page
verification. It does not synthesize a report, translate headlines, or expand a
fresh-news window. Callers must have an intentional user action and a reservation.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app import runtime
from app.agent_profiles import get_profile
from app.storage import Store, StoreError


MAX_DISCOVERY_ACTIONS = 2
MAX_DISCOVERY_PAGES = 12


def estimate_discovery(agent_id: str, *, research_window: dict[str, Any],
                       topic: str = "", language: str = "en") -> dict[str, Any]:
    # Lazy import avoids coupling report-module initialization to feed storage.
    from app import research

    estimate = research._estimate_native_reservation(agent_id, topic, language,
        research_window=research_window)
    actions = min(MAX_DISCOVERY_ACTIONS, estimate["max_native_actions"])
    if actions < 1:
        raise StoreError("web_search_not_enabled", "Web discovery is not enabled for this agent.", 403)
    payload = research._research_payload(estimate["model"], get_profile(agent_id),
        estimate["topic"], language, estimate["research_window"], actions)
    inputs, outputs = research._input_bound(payload, actions), int(payload["max_output_tokens"])
    amount = ((inputs * Decimal(estimate["research_input_usd_per_million"])
        + outputs * Decimal(estimate["research_output_usd_per_million"])) / Decimal(1000000)
        + actions * Decimal(estimate["search_usd_per_call"]))
    return {**estimate, "estimated_cost_usd": runtime._rounded_usd(amount),
        "max_model_calls": 1, "max_input_tokens": inputs, "max_output_tokens": outputs,
        "max_web_search_calls": actions, "max_native_actions": actions,
        "max_publisher_pages": MAX_DISCOVERY_PAGES, "research_stage_action_limits": [actions],
        "research_input_tokens_bound": inputs, "research_output_tokens_bound": outputs,
        "report_input_tokens_bound": 0, "pricing_basis": "bounded_native_discovery_without_synthesis"}


def discover_sources(agent_id: str, estimate: dict[str, Any]) -> dict[str, Any]:
    """Perform one already-reserved discovery; never retry a failed API call."""
    from app import research

    profile = get_profile(agent_id)
    _, model, key, _ = runtime._provider_config()
    if not key or model != estimate["model"]:
        raise StoreError("runtime_configuration_changed", "Research configuration changed; start a new refresh.", 503)
    actions = estimate["max_native_actions"]
    if type(actions) is not int or not 1 <= actions <= MAX_DISCOVERY_ACTIONS:
        raise StoreError("retrieval_reservation_invalid", "The discovery reservation is invalid.", 500)
    payload = research._research_payload(model, profile, estimate["topic"], estimate["language"],
        estimate["research_window"], actions)
    if research._input_bound(payload, actions) > estimate["research_input_tokens_bound"]:
        raise StoreError("retrieval_reservation_invalid", "Discovery exceeds its reserved input bound.", 500)
    response = research._call_response(key, payload, actions=actions)
    discovered = research._discovered(response)
    sources, excluded = research._verified_sources(discovered, profile, estimate["topic"],
        estimate["research_window"], page_limit=MAX_DISCOVERY_PAGES)
    return {"sources": sources, "excluded": excluded,
        "discovered_count": len(discovered), "verified_source_count": len(sources),
        "stages": [{"stage": "web_discovery", "native_actions": len(response.get("native_calls", [])),
            "search_calls": response.get("search_calls", 0), "publisher_page_limit": MAX_DISCOVERY_PAGES}],
        **research._accounting(estimate, [("research", response)])}


def run_feed_discovery(database: Store, agent_id: str, refresh_id: str,
                       research_window: dict[str, Any]) -> dict[str, Any]:
    """Reserve the same app limits as reports, without public report ownership.

    The task is the agent's generic public topic. Visitor prompts and private
    conversation data never enter a shared feed or the discovery reservation.
    """
    estimate = estimate_discovery(agent_id, research_window=research_window)
    reservation = database.reserve_run(agent_id,
        "Shared public news feed discovery: " + get_profile(agent_id)["default_topic_en"],
        estimate["estimated_cost_usd"], idempotency_key="feed-web:" + refresh_id,
        trigger="manual", provider="openai", model=estimate["model"],
        pricing_snapshot=estimate, language="en")
    if not reservation.created:
        raise StoreError("retrieval_already_attempted", "This refresh already has a web-discovery attempt.", 409)
    report_id = reservation.report["report_id"]
    try:
        result = discover_sources(agent_id, estimate)
        # Keep cost/usage in the internal ledger. Do not publish a generated
        # report or associate this shared operation with a visitor's workspace.
        database.finish_run(report_id, {"report_kind": "feed_discovery", "agent_id": agent_id,
            "source_count": result["verified_source_count"], "feed_refresh_id": refresh_id,
            "stages": result["stages"], "web_search": result["web_search"]},
            estimated_cost_usd=result["estimated_cost_usd"],
            cost_tracking_complete=result["cost_tracking_complete"], usage=result["usage"],
            newsdata_credits_used=0, credit_tracking_complete=True)
        return result
    except Exception as error:
        code = getattr(error, "code", None)
        if not isinstance(code, str):
            detail = getattr(error, "detail", None)
            code = detail.get("code") if isinstance(detail, dict) else None
        database.fail_run(report_id, error_code=code if isinstance(code, str) else "retrieval_failed")
        raise
