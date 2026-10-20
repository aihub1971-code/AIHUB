"""Domain-specific research profiles keyed by the existing agent registry IDs."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import HTTPException

from app.registry import get_agent, get_agents


_STANDARD = [
    ("executive_summary", "Executive summary", "الملخص التنفيذي"),
    ("what_changed", "What changed", "ما الذي تغيّر"),
    ("why_it_matters", "Why it matters", "لماذا يهم"),
    ("analyst_assessment", "Analyst assessment", "تقدير المحلل"),
    ("uae_implications", "UAE implications", "الآثار على الإمارات"),
    ("what_to_watch", "What to watch", "ما يستحق المتابعة"),
]

_PROFILES = {
    "ubi-research": {
        "kind": "briefing", "uses_retrieval": True,
        "default_topic_en": "New universal basic income policy, pilot results and research, with lessons for the UAE",
        "default_topic_ar": "مستجدات الدخل الأساسي الشامل ونتائج التجارب والأبحاث ودروسها للإمارات",
        "scope": "Universal basic income: policy decisions, pilot design and findings, funding, eligibility, fiscal and distributional effects, labour-market evidence. Distinguish a new release from the older period studied; do not present old results as new announcements.",
        "research_strategy": "Search official policy releases, trial registers, research institutions and national/local reporting in relevant languages. Compare what a study measures with what politicians claim. Assess transferability to UAE population, labour and fiscal arrangements without assuming identical institutions.",
        "terms": ["universal basic income", "basic income", "guaranteed income", "citizen income", "الدخل الأساسي", "الدخل الاساسي", "دخل مضمون", "revenu universel", "renta básica", "grundeinkommen"],
        "sections": _STANDARD,
    },
    "iran-news": {
        "kind": "briefing", "uses_retrieval": True,
        "default_topic_en": "Iran–US conflict, diplomacy, nuclear issues and major regional consequences",
        "default_topic_ar": "الصراع الإيراني الأمريكي والدبلوماسية والملف النووي والتداعيات الإقليمية الكبرى",
        "scope": "Iran–US specialist: military and diplomatic developments, sanctions, nuclear issues and regional involvement. Assess Iran and the United States separately, then UAE security, diplomacy, energy, shipping, aviation and business exposure. A US domestic story is outside scope unless its direct conflict connection is evidenced in the article.",
        "research_strategy": "Use Persian, Arabic and English reporting as relevant. Cross-check Iranian, US and regional official claims against independent reporting. Distinguish claims from confirmation and contemporary military evidence from speculation. Find consequences with an explicit causal link to the conflict.",
        "terms": ["iran", "iranian", "tehran", "hormuz", "irgc", "إيران", "ايران", "طهران", "هرمز", "إيراني", "ايراني", "ایران", "تهران", "هرمز", "irán", "téhéran", "иран", "тегеран", "伊朗", "德黑兰"],
        "sections": _STANDARD[:3] + [("iran_assessment", "Iran assessment", "تقدير موقف إيران"),
            ("us_assessment", "United States assessment", "تقدير موقف الولايات المتحدة")] + _STANDARD[3:],
        "item_sections": [{"id": "conflict_diplomacy", "title_en": "Conflict and Diplomacy", "title_ar": "الصراع والدبلوماسية"},
                          {"id": "major_consequences", "title_en": "Major Consequences", "title_ar": "التداعيات الكبرى"}],
    },
    "uae-eu-trade-talks": {
        "kind": "briefing", "uses_retrieval": True,
        "default_topic_en": "Verified UAE–EU trade negotiations, official positions and affected sectors",
        "default_topic_ar": "التطورات الموثقة في مفاوضات تجارة الإمارات والاتحاد الأوروبي ومواقفهما والقطاعات المتأثرة",
        "scope": "Bilateral UAE–EU trade relations, negotiations, market access, investment, regulation, standards and affected sectors. Explain concrete changes and what remains unannounced. Never infer negotiating progress from a meeting, aspiration or older announcement.",
        "research_strategy": "Prioritize UAE and European Commission releases and original legal/trade documents, then sector and national reporting in Arabic, English and relevant European languages. Distinguish proposed, negotiated, signed and implemented measures.",
        "terms": ["uae", "emirates", "الإمارات", "الامارات", "émirats", "vereinigte arabische"],
        "sections": _STANDARD,
    },
    "geopolitical-morning": {
        "kind": "briefing", "uses_retrieval": True,
        "default_topic_en": "Consequential global geopolitical developments across regions and their UAE implications",
        "default_topic_ar": "التطورات الجيوسياسية المؤثرة حول العالم وآثارها على الإمارات",
        "scope": "Global conflicts, diplomacy, alliances, sanctions, strategic competition and political changes with international effects. Maintain geographic breadth. Iran can appear when globally consequential, but this is not a duplicate Iran specialist report.",
        "research_strategy": "Use complementary geographic searches across Asia, Europe, the Americas, Africa and the Middle East. Combine reputable international newsrooms with national/local and primary evidence. Separate state claims, corroborated reporting and analysis.",
        "terms": ["war", "conflict", "diplomac", "sanction", "treaty", "alliance", "military", "election", "ceasefire", "summit", "geopolit", "border", "حرب", "نزاع", "دبلوماس", "عقوبات", "انتخاب", "قمة", "عسكري", "حدود", "صراع", "guerre", "guerra", "diplomatie", "diplomacia", "krieg", "waffenstillstand", "воен", "войн", "дипломат", "战争", "外交", "停火"],
        "sections": _STANDARD,
    },
    "education-observatory": {
        "kind": "briefing", "uses_retrieval": True,
        "default_topic_en": "New global education policy, research, skills and institutional developments",
        "default_topic_ar": "مستجدات سياسات التعليم والأبحاث والمهارات والمؤسسات التعليمية عالميًا",
        "scope": "Education policy, institutions, research, skills, technology, access and international collaboration. Explain practical implications for educators, policymakers and UAE institutions; distinguish release date from study/data collection dates.",
        "research_strategy": "Prioritize ministries, institutions, peer-reviewed research and original datasets alongside specialist and local education reporting. Identify study design, population, limitations and implementation status.",
        "terms": ["education", "school", "universit", "student", "teacher", "learning", "curriculum", "skills", "تعليم", "مدرس", "جامعة", "طلاب", "طلبة", "مهارات", "éducation", "université", "école", "educación", "escuela", "bildung", "schule", "образован", "университет", "教育", "大学"],
        "sections": _STANDARD,
    },
    "historical-reading-companion": {
        "kind": "reading", "uses_retrieval": True,
        "default_topic_en": "The Abbasid translation movement in Baghdad: knowledge, institutions and competing interpretations",
        "default_topic_ar": "حركة الترجمة في بغداد العباسية: المعرفة والمؤسسات وتعدد التفسيرات",
        "scope": "A sourced historical reading guide: context, chronology, people, concepts, competing interpretations and reading questions. Historical material is background, not fresh news. Use the supplied text if present without pretending an upload exists.",
        "research_strategy": "Find museum, library, archive, academic and primary historical sources. Attribute competing interpretations and distinguish contemporary scholarship from historical actors' claims. Label all older material as background.",
        "terms": [],
        "sections": [("context", "Historical context", "السياق التاريخي"), ("chronology", "Chronology", "التسلسل الزمني"),
            ("key_people", "Key people", "الشخصيات الرئيسية"), ("concepts", "Key concepts", "المفاهيم الرئيسية"),
            ("interpretations", "Competing interpretations", "تفسيرات متباينة"), ("reading_questions", "Reading questions", "أسئلة القراءة")],
    },
    "story-moodboard": {
        "kind": "moodboard", "uses_retrieval": True,
        "default_topic_en": "A near-future coastal city where an archivist discovers memories carried by the tide",
        "default_topic_ar": "مدينة ساحلية في مستقبل قريب تكتشف فيها أمينة أرشيف ذكريات يحملها المدّ",
        "scope": "A creative pack with premise, themes, tone, setting, character cues, story beats, visual direction, colour palette and references. Label invented story elements as creative interpretation; factual inspiration requires real source attribution.",
        "research_strategy": "Find factual visual/cultural/environmental inspiration through museums, archives, original artists' or institutions' pages and relevant research. Older references are references, not fresh news. Do not invent copyrighted image permissions or claim images were downloaded/generated.",
        "terms": [],
        "sections": [("premise", "Premise", "الفكرة الأساسية"), ("themes", "Themes", "الثيمات"), ("tone", "Tone", "النبرة"),
            ("setting", "Setting", "العالم والمكان"), ("character_cues", "Character cues", "ملامح الشخصيات"),
            ("story_beats", "Story beats", "محطات القصة"), ("visual_direction", "Visual direction", "التوجه البصري"),
            ("colour_palette", "Colour palette", "لوحة الألوان")],
    },
    "economic-cultural-briefing": {
        "kind": "briefing", "uses_retrieval": True,
        "default_topic_en": "Economic developments, culture, tourism and the creative economy with UAE relevance",
        "default_topic_ar": "التطورات الاقتصادية والثقافة والسياحة والاقتصاد الإبداعي ذات الصلة بالإمارات",
        "scope": "Economics alongside culture, tourism, creative industries and cultural exchange. Explain business and societal consequences for the UAE; distinguish this from a briefing centered on states, security and strategic power.",
        "research_strategy": "Balance economic data, company/central-bank releases and sector reporting with cultural institutions, local creative-sector reporting and tourism sources. Explain actual transmission channels to the UAE rather than generic relevance.",
        "terms": ["econom", "inflation", "interest rate", "market", "investment", "trade", "tourism", "cultur", "museum", "heritage", "creative", "art ", "film", "اقتصاد", "تضخم", "استثمار", "تجارة", "سياح", "ثقاف", "متحف", "تراث", "فنون", "إبداع", "économ", "échange", "musée", "economía", "turismo", "wirtschaft", "kultur", "эконом", "культур", "经济", "文化"],
        "sections": _STANDARD,
    },
    "decision-briefing": {
        "kind": "briefing", "uses_retrieval": True,
        "default_topic_en": "Evidence and decision criteria for adopting AI tools in a UAE organization",
        "default_topic_ar": "الأدلة ومعايير اتخاذ القرار بشأن تبني أدوات الذكاء الاصطناعي في مؤسسة إماراتية",
        "scope": "An executive decision brief with options, evidence, comparable criteria, uncertainties and conditional recommendations. Do not invent the owner's resources or authority.",
        "research_strategy": "Find primary evidence relevant to the decision and independent comparisons. Keep facts, assumptions and recommendation conditions explicit.",
        "terms": [], "sections": _STANDARD + [("options", "Options and trade-offs", "الخيارات والمفاضلات")],
    },
}


def get_profile(agent_id: str) -> dict[str, Any]:
    agent = get_agent(agent_id)
    if agent is None or agent_id not in _PROFILES:
        raise HTTPException(404, detail={"code": "unknown_agent", "message": "Unknown agent."})
    profile = deepcopy(_PROFILES[agent_id])
    profile.update(id=agent_id, agent_id=agent_id, name=agent["name"], name_en=agent["name"],
                   name_ar=agent.get("display_name_ar", agent["name"]), number=agent.get("number", ""),
                   enabled=agent.get("enabled") is True)
    profile["sections"] = [{"id": item[0], "title_en": item[1], "title_ar": item[2]} for item in profile["sections"]]
    if profile["kind"] == "reading":
        labels = ("Sources", "Reading Pack", "المصادر", "دليل القراءة")
    elif profile["kind"] == "moodboard":
        labels = ("References", "Moodboard", "المراجع", "اللوحة الإبداعية")
    elif profile["kind"] == "workspace":
        labels = ("Source text", "Working Pack", "النص الأصلي", "ملف العمل")
    else:
        labels = ("News", "Briefing Pack", "الأخبار", "ملف الإحاطة")
    profile["views"] = [{"id": "evidence", "title_en": labels[0], "title_ar": labels[2]},
                        {"id": "pack", "title_en": labels[1], "title_ar": labels[3]}]
    profile["view_labels"] = deepcopy(profile["views"])
    return profile


def list_profiles() -> list[dict[str, Any]]:
    return [get_profile(agent["id"]) for agent in get_agents()]


def default_topic(agent_id: str, language: str = "ar") -> str:
    return get_profile(agent_id)["default_topic_en" if language == "en" else "default_topic_ar"]
