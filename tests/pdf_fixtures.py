"""Synthetic PDF design fixtures. Never retrieval results or production endpoints."""

from copy import deepcopy


def report_fixture(language="en", kind="briefing", *, long=False):
    ar = language == "ar"
    report = {
        "schema_version": 2, "report_kind": "research", "report_id": "rpt_pdf_fixture",
        "agent_id": "geopolitical-morning", "agent_name": "الإحاطة الجيوسياسية العالمية" if ar else "Global Geopolitical Briefing",
        "topic": "بيانات اصطناعية لمراجعة التصميم فقط" if ar else "Synthetic content for design review only",
        "title": "معاينة التصميم: التحولات الإقليمية وانعكاساتها" if ar else "Design preview: regional developments and their implications",
        "language": language, "kind": kind,
        "research_window": {"start": "2026-10-20T00:00:00Z", "end": "2026-10-20T12:00:00Z", "cutoff": "2026-10-20T12:00:00Z", "timezone": "UTC", "display_timezone": "Asia/Dubai"},
        "items": [], "sources": [], "sections": [], "citation_mappings": {},
        "limitations": ["هذه بيانات اختبار اصطناعية، وليست أخبارًا مسترجعة. لا تثبت مراجعة التصميم صحة أي تطور." if ar else "This is synthetic test content, not retrieved news. Design review is not verification of any development."],
    }
    topics = [
        ("executive_summary", "الخلاصة التنفيذية", "Executive summary"),
        ("what_changed", "ما الذي تغير", "What changed"),
        ("why_it_matters", "الأهمية", "Why it matters"),
        ("analyst_assessment", "تقدير المحلل", "Analyst assessment"),
        ("uae_implications", "الانعكاسات على دولة الإمارات", "UAE implications"),
        ("what_to_watch", "ما ينبغي متابعته", "What to watch"),
    ]
    arabic = (
        "تستخدم هذه الفقرة الاصطناعية لاختبار القراءة العربية وتداخل الأرقام والأسماء الإنجليزية مثل UAE وOpenAI. "
        "يميز التقرير بين حقيقة منشورة، وادعاء منسوب إلى جهة، واستنتاج تحليلي يحتاج إلى أدلة إضافية. "
        "قد تنتقل آثار التطورات إلى الأعمال عبر تكلفة التأمين، ومدة النقل، وتوافر الرحلات، وتوقعات الاستثمار. "
        "لا يعني نشر المادة في الساعة 14:30 أن الحدث نفسه وقع في تلك الساعة، ولا يحل تاريخ جمع البيانات محل تاريخ النشر. "
        "تظل النتيجة مشروطة بمصدرها وبالفترة المحددة، وتعرض مواطن الاختلاف من دون اختلاق نسب احتمال."
    )
    english = (
        "This synthetic paragraph tests the distinction between a reported fact, an attributed claim and an analytical judgment. "
        "A development can affect businesses through insurance costs, transport times, flight availability and investment expectations. "
        "The publication timestamp fixes when the article entered the research window; it does not establish when the described event occurred. "
        "For the UAE, an assessment should identify the transmission channel and the exposed activity, then explain which evidence would strengthen or weaken the conclusion. "
        "A conditional scenario remains conditional. No percentage is assigned without a defensible basis, and an absence of qualifying sources is not proof that nothing happened."
    )
    for index, (sid, title_ar, title_en) in enumerate(topics, 1):
        text = arabic if ar else english
        body = text + "\n\n" + text
        if index == 5:
            body += ("\n\n| القناة | الأثر المحتمل | ما نراقبه |\n|---|---|---|\n| النقل البحري | تغير زمن الوصول والتأمين | إشعارات الموانئ المؤرخة |\n| الطيران | تغير المسارات والطلب | الإعلانات التشغيلية |" if ar else
                     "\n\n| Channel | Potential effect | Observable indicator |\n|---|---|---|\n| Shipping | Transit and insurance conditions | Dated port notices |\n| Aviation | Route availability and demand | Operational announcements |")
        report["sections"].append({"id": sid, "title": title_ar if ar else title_en, "body": body, "source_ids": [f"S{(index - 1) % 3 + 1}"]})
    for i in range(1, 4):
        url = f"https://www.ft.com/content/synthetic-pdf-design-reference-{i}"
        headline = f"مادة اختبار {i}: عنوان عربي وروابط واضحة" if ar else f"Synthetic source {i}: a readable headline and explicit attribution"
        source = {"id": f"S{i}", "title": headline, "publisher": "Synthetic publisher / ناشر تجريبي", "url": url,
                  "published_at": f"2026-10-20T0{i}:15:00Z", "role": "fresh", "source_kind": "article",
                  "publication_verification": "synthetic_fixture", "excerpt": "Synthetic evidence only.", "citation_annotations": []}
        report["sources"].append(source)
        report["citation_mappings"][f"S{i}"] = {k: source[k] for k in ("url", "title", "published_at")}
        report["items"].append({"id": f"N{i}", "headline": headline, "publisher": source["publisher"], "published_at": source["published_at"],
                                "published_date": source["published_at"], "url": url, "summary": (arabic if ar else english)[:340],
                                "why_it_matters": "مثال توضيحي لقناة أثر محددة، دون ادعاء واقعة حقيقية." if ar else "An illustrative impact channel, without claiming a real event.",
                                "source_ids": [f"S{i}"], "section": "general", "event_date": None})
    if kind == "reading":
        report.update(agent_id="historical-reading", agent_name="Historical Reading Companion", title="Design preview: cities, trade and historical interpretation")
        report["items"] = []
        for s in report["sources"]:
            s["role"] = "background"
            s["published_at"] = "2018-01-01"
        report["sections"] = [
            {"id": "context", "title": "Historical context", "body": english + "\n\n" + english, "source_ids": ["S1"]},
            {"id": "chronology", "title": "Chronology", "body": "- First period: establish the conditions described in the supplied source.\n- Second period: compare the institutions and actors without assuming a single cause.\n- Third period: separate contemporary testimony from later interpretation.", "source_ids": ["S1", "S2"]},
            {"id": "people", "title": "People and concepts", "body": "| Concept | Reading question |\n|---|---|\n| Institutions | Who could make decisions, and under which constraints? |\n| Exchange | Which material and cultural connections are documented? |", "source_ids": ["S2"]},
            {"id": "interpretations", "title": "Competing interpretations", "body": english + "\n\n" + english, "source_ids": ["S3"]},
            {"id": "questions", "title": "Questions for close reading", "body": "- Which claim comes directly from a contemporary source?\n- What does the author infer?\n- Which missing evidence could alter the account?", "source_ids": []},
        ]
    if kind == "moodboard":
        report.update(agent_id="story-moodboard", agent_name="لوحة القصة" if ar else "Story Moodboard",
                      title="معاينة إبداعية: مدينة بين البحر والذاكرة" if ar else "Creative preview: a city between sea and memory")
        report["items"] = []
        for s in report["sources"]:
            s["role"] = "reference"
        report["sections"] = [
            {"id": "premise", "title": "الفكرة" if ar else "Premise", "body": "تبحث مرممة شابة عن أثر رسالة تركها مسافر قديم، فتقرأ المدينة من خلال أصوات مينائها وظلال بيوتها. هذه فكرة إبداعية، وليست رواية واقعة موثقة." if ar else "A young restorer follows the trace of a traveller's letter through a city's harbour sounds and shaded streets. This is creative interpretation, not a documented event.", "source_ids": []},
            {"id": "tone", "title": "النبرة والمكان" if ar else "Tone and setting", "body": "إيقاع متأن، ضوء جانبي، مساحات صامتة، وتفاصيل ملمسية في الحجر والخشب. يتجاور الأزرق البحري مع لون الرمل ونحاس معتّق." if ar else "A measured rhythm, side light, quiet space and tactile details of stone and wood. Sea blue meets sand and aged copper.", "source_ids": []},
            {"id": "characters", "title": "الشخصيات" if ar else "Character cues", "body": "- المرممة: تلاحظ ما يغفل عنه الآخرون.\n- الملاح: يحفظ طرقًا لم تعد على الخرائط.\n- الأرشيفية: تميز ما وثقته الأوراق عما أضافته الذاكرة." if ar else "- The restorer notices overlooked details.\n- The navigator remembers routes no longer on maps.\n- The archivist separates records from memory.", "source_ids": []},
            {"id": "beats", "title": "نبضات القصة" if ar else "Story beats", "body": "- اكتشاف الرسالة.\n- تعارض روايتين.\n- قراءة الأثر في سياقه.\n- اختيار ما يستحق الحفظ." if ar else "- Discover the letter.\n- Encounter conflicting accounts.\n- Read the trace in context.\n- Choose what to preserve.", "source_ids": []},
        ]
        report["visuals"] = {"palette": [{"name": "بحر" if ar else "Sea", "hex": "#123B4A"}, {"name": "رمل" if ar else "Sand", "hex": "#E4D3B5"}, {"name": "نحاس" if ar else "Copper", "hex": "#AF694E"}, {"name": "ورق" if ar else "Paper", "hex": "#F2EEE4"}],
                             "direction": "استعمل التكوين المفتوح والإيقاع المتكرر والتباين بين الكتل لاستكشاف نبرة القصة؛ الأشكال التالية دراسات تصميم أصلية." if ar else "Use open space, repeated rhythm and contrasting masses to explore tone; these shapes are original design studies.", "reference_source_ids": ["S1"]}
    if long:
        paragraph = arabic if ar else english
        report["sections"] = [{"id": "edge", "title": "اختبار الجدول الطويل" if ar else "Long-table edge case", "body": "| A | B |\n|---|---|\n| " + paragraph * 14 + " | " + paragraph * 7 + " |\n| FINAL SENTINEL | نهاية الجدول |", "source_ids": ["S1"]}]
        report["sources"][0]["url"] += "/" + "long-path-segment-" * 90
        report["sources"][0]["title"] += " " + ("مصدر ذو عنوان طويل " if ar else "A long source title ") * 13
        report["items"] = []
    return deepcopy(report)
