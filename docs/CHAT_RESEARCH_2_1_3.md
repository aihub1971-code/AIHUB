# AI HUB 2.1.3 — research ordinary agent-chat questions

Agent chat previously recognized a narrow set of research words. An ordinary factual question could miss that routing even when no saved report answered it, leaving the answer step without the evidence it needed.

## Behavior

- Substantive English and Arabic information requests can start research without including words such as “search” or “latest”. The existing retrieval path can use NewsData and OpenAI web search to gather new evidence.
- Relevant saved evidence remains available for discussion. An unrelated displayed report does not prevent research into a new question.
- English and Arabic requests to avoid searching are respected. Greetings, platform help, personal preferences and discussion of a saved report do not automatically become research requests.
- Current follow-up questions retain the prior subject when they do not introduce a different subject.
- Answers retain the existing evidence and citation checks. A source or report outside the twelve-hour news window remains dated background, rather than being presented as a new development.

The existing manual-refresh, queue, quota and spending settings continue to apply. Searches still require an intentional user request. This change does not add page-load, agent-switch or timer retrieval, and does not change provider subscriptions.

## Validation

Focused routing and API regressions cover ordinary factual questions with no report or unrelated saved evidence, English and Arabic no-search requests, contextual follow-ups and ordinary conversation. Deployment and live acceptance evidence is recorded in the release pull request after verification.
