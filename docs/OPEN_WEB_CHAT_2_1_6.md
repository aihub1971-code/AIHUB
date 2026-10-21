# AI HUB 2.1.6 — verified web research in conversation

The owner clarified that every agent should answer from the wider web, with accurate sourcing, rather than being restricted to a report's bibliography. This release separates ordinary researched questions from full report generation.

This release builds on the deployed 2.1.5 branch. It retains the citation-schema correction, monotonic report recovery, task-aware background evidence, NewsData topic matching and honest no-evidence replies from releases 2.1.3–2.1.5.

## Conversation behavior

- A substantive factual question can start bounded web research when the visitor sends it. The agent's usual subject and saved report bibliography do not restrict the eligible public publishers.
- Reports, relevant same-visitor conversations and reviewed memory help interpret the question. They do not replace its topic or make an unsupported assertion verified.
- An explicit request to use only a report or not conduct new research stays local. Greetings, writing operations and questions about the visitor's own saved preferences/history do not require a web search when their context suffices.
- Only an explicit request to generate or update a report enters the report/PDF job pipeline. An ordinary researched question is answered directly in the conversation.
- The answer streams after discovery and source checking. Final links are limited to this visitor's saved report citations and publisher-checked chat references; partial text remains unlinked until the answer's links are validated. The existing report evidence labels remain visible and do not imply that every saved report citation received a publisher-page inspection.
- Older scholarship and date-only or undated references may be useful for general questions. Their dates and uncertainty remain explicit. The twelve-hour rule still governs fresh news, not all knowledge used in conversation.

## Retrieval and evidence bounds

One web-chat turn permits one native discovery stage, at most two native actions and eight inspected publisher pages. There is no fixed publisher allowlist. Existing URL attestation, public-IP HTTPS checks, TLS, redirect limits and bounded downloads remain mandatory. The current verifier reads HTML/XHTML, so a PDF-only source may be excluded. An inaccessible publisher is an evidence limitation, not permission to bypass its controls.

The synthesis distinguishes checked facts, attributed official statements, opinion and analytical judgment. It should prefer original reporting and primary documentation, give source dates where available, identify disagreement and avoid claiming that a limited search proves no event occurred. A citation proves which source supports a statement; it does not make every assertion on that source true.

A researched factual answer must cite a checked source from the current question's discovery. An old report link alone cannot satisfy this requirement. When no publisher text can be verified, the application saves a clear localized limitation without asking the model to fill the gap from memory. It accounts for the completed discovery and makes no answer-model call. Previously checked references remain explicitly labelled as earlier context.

Task-relevant recent conversation can resolve follow-ups. Unrelated shared memories, preferences and visitor identifiers do not belong in public search queries. Checked source metadata and evidence are saved with the owning turn for history restoration and relevant same-visitor follow-ups. Other visitors' evidence cannot be attached by supplying their identifiers.

## Accounting and recovery

The application reserves the combined discovery-and-answer cost before provider I/O. A new web-research turn consumes one attempt from the shared 500-per-Dubai-day research allowance. Ordinary local conversation keeps its existing model-budget treatment. Replaying an existing request consumes no additional research attempt and performs no duplicate search. Failed or ambiguous paid attempts remain conservatively accounted for.

This release does not change provider subscription quotas or the existing daily/monthly USD ceilings. NewsData retrieval remains capped at 50 raw articles per intentional refresh; its page settings and paid-plan entitlement are separate from conversational web search. Opening a page, switching an agent or language, restoring history and status polling do not initiate paid retrieval.

## Verification

The implementation requires regression coverage for broad questions without a report job, explicit report/no-search routing, contextual follow-ups, historical evidence, arbitrary credible public publishers, private/unknown URL rejection, citation dates, pre-I/O accounting, replay, streaming and visitor isolation. Full CI and deployed bilingual chat acceptance results belong in the corresponding pull request. This document describes source behavior and does not itself establish live deployment.
