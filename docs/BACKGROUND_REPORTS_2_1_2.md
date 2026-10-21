# AI HUB 2.1.2 — preserve supported background analysis

Live verification of 2.1.1 found a content failure in an Arabic education report. Retrieval succeeded and supplied seven background sources alongside one fresh provider candidate, but postprocessing replaced the synthesis with a single no-fresh-news paragraph. The source inventory remained visible, which made a retrieved report look complete without delivering its analysis.

## Cause and correction

The empty-result guard treated any rejected fresh item, or any section citing a fresh candidate that was not selected as news, as a reason to discard every analysis section. Fresh-item validation and independently supported background analysis are now handled separately. When no fresh item survives, only validated sections citing background evidence may remain; sections that depend on unsupported fresh material are withheld. A discarded fresh candidate is never relabelled as background. Background reports retain an explicit freshness notice, evidence dates and a clear limitation when content is withheld.

The synthesis instructions also cover mixed evidence: the presence of fresh candidates does not require the model to select them, and an empty set of qualifying news can still support a substantial background briefing. No extra, unreserved provider call is added by this recovery.

## Verification record

The predecessor [PR #25](https://github.com/aihub1971-code/AIHUB/pull/25) passed 1,407 Python and 131 JavaScript tests and was deployed as 2.1.1. Its live checks established:

- A deliberate UBI refresh retrieved 30 NewsData records across three pages and completed web fallback. None qualified as fresh, relevant news; no old story was presented as new.
- A second deliberate refresh was accepted immediately, without the former fifteen-minute successful-cache wait.
- English and Arabic report jobs were accepted, and an active Generate action reconnected to the same job.
- Chat streamed a response using the displayed report's evidence and date while report generation continued. Switching agents restored their separate saved conversations.
- English report `rpt_757ddccd546f4c6bbdc7b2edfea68f4c` completed with 13 sources and a six-page PDF. All six pages were visually inspected; citations, dates and substantive UAE analysis were retained.
- Arabic report `rpt_1302777c3b124e529480bff3dd6a2120` exposed the postprocessing failure addressed here. Its two-page PDF is not counted as a substantive report success.

Regression tests for this correction must cover Arabic and English mixed-source reports, rejected fresh candidates, section-level evidence boundaries, and genuinely empty/unsupported results. The full CI gate, deployed commit and corrected Arabic report/PDF acceptance evidence are recorded in this release's pull request. This source document does not itself establish deployment or live acceptance.

## Limits retained

Application allowances remain 500 NewsData credits and 500 shared research/report attempts per Dubai day, with 10 active report jobs per visitor and 50 globally. Provider quotas, the 50-article raw retrieval bound, the 12-hour news window, existing USD budgets, visitor isolation, manual-only fetching and reviewed memory remain enforced. Undated references are identified as such; the fix does not turn them into fresh news.
