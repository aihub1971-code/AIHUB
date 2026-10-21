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

## 2.1.4 — citation contract and fallback recovery

Live acceptance of an ordinary factual question exposed two report-processing failures after successful retrieval. The generation schema permitted empty citations in required sections, while the evidence validator correctly rejected an uncited section. The NewsData fallback then attempted to report an earlier progress stage, masking the original evidence error with `invalid_report_stage`.

The follow-up correction aligns the generation schema and writing instructions with the existing citation validator. When retrieved sources exist, required sourced sections must identify their supporting evidence. Empty-source and original-creative cases retain their own rules; unsupported factual claims remain unacceptable.

The fallback reuses the NewsData inventory already fetched and keeps progress at analysis once analysis has begun. The durable job's stage guards remain intact. The original web-research failure remains visible in retrieval diagnostics, and uncertain provider charges retain their conservative reservation. This change introduces no automatic provider retry.

## 2.1.5 — task evidence and honest chat answers

The next live acceptance completed its job and PDF, but the answer was unsupported by the retrieved report. An ordinary European Central Bank question had inherited a news-only profile: older and undated official documents were excluded, while generic request words let an unrelated NewsData article match the task. The answer step then returned a correct remembered fact without a citation. That completed request was not accepted as a successful sourced answer.

Specific research tasks now admit inspected background references with explicit dates or an undated label, while default news discovery keeps its original twelve-hour policy. Reservation estimates and execution use the same effective task profile. Task discovery prioritizes evidence answering the question; useful background can avoid an unnecessary second discovery stage. Fresh-news timestamp rules and provider action ceilings remain unchanged.

NewsData task matching ignores common instructions such as “answer briefly” and preserves meaningful subject acronyms. Fresh-research chat uses sources supporting the report content and requires citations. If the report has no supported content, the chat gives a clear bilingual retrieval-limitation response instead of filling the gap from model memory. These changes retain the established conversation ownership, streaming, idempotency and cost controls.
