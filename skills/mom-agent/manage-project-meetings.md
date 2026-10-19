---
name: manage-project-meetings
description: Transform project meeting transcripts, notes, recordings, or email-based meeting records into accurate minutes, confirmed decisions, action items, open matters, risks, escalations, and a concise follow-up email draft. Use when the user asks to process a meeting, prepare minutes, extract deliverables or actions, identify decisions, or draft a post-meeting email. Do not send or distribute the email without explicit project-manager approval.
---

# Manage Project Meetings

Process only the supplied or retrieved meeting record. Preserve uncertainty and distinguish confirmed commitments from discussion.

## Workflow

1. Obtain readable meeting content. If a recording cannot be accessed or transcribed, request a transcript or supported attachment.
2. Identify the meeting title, date, participants, and purpose. Use `To be confirmed` for missing information.
3. Organize the discussion by agenda item. Keep the meeting summary at 150 words or fewer.
4. Classify statements as:
   - confirmed decisions;
   - suggestions or proposals;
   - unresolved or ambiguous matters.
5. Extract every explicit action or deliverable.
6. Assign an owner, due date, priority, dependency, and status only when supported by the record. Otherwise use `To be confirmed`.
7. Identify explicit dependencies, risks, and escalations. Do not manufacture them from general concerns.
8. Draft a concise follow-up email containing the purpose, confirmed decisions, assigned deliverables, stated deadlines, and next-meeting details when available.
9. Highlight ambiguities requiring confirmation before distribution.

## Evidence controls

- Never invent or infer an owner, deadline, decision, commitment, priority, dependency, status, participant, or next meeting.
- Treat tentative language such as “should,” “could,” “recommend,” or “consider” as a suggestion unless the record clearly confirms agreement.
- Do not convert silence, discussion, or a proposal into approval.
- Preserve dates exactly enough to avoid ambiguity. Do not guess a year or timezone.
- If sources conflict, show the conflict under Open matters and request confirmation.
- Keep each action separate when owners or deadlines differ.
- Exclude unsupported background knowledge.

## Required output

### Meeting details

| Field | Information |
|---|---|
| Meeting title | |
| Date | |
| Participants | |
| Purpose | |

### Meeting summary

Write no more than 150 words, grouped by agenda item when multiple agenda items exist.

### Decisions

| Decision | Decision owner | Date confirmed |
|---|---|---|

Include confirmed decisions only. If none are confirmed, state `No confirmed decisions identified.`

### Action items

| Deliverable | Owner | Due date | Priority | Dependency | Status |
|---|---|---|---|---|---|

Use `To be confirmed` for each unsupported field. If no actions are identified, state `No action items identified.`

### Open matters

| Matter | Required decision | Responsible person |
|---|---|---|

Include suggestions, unresolved matters, conflicting statements, and ambiguities needing human confirmation.

### Risks and escalations

List only risks and escalations explicitly present in the record. State `None explicitly identified` when applicable.

### Follow-up email draft

Provide:

- `To:` only when recipients are supplied or confirmed; otherwise `To be confirmed`;
- `Subject:` based on the meeting title unless the user supplies one;
- a formal, concise body covering the purpose, confirmed decisions, deliverables, deadlines, and next meeting information;
- a clear note for any missing confirmation that affects distribution.

## Distribution gate

Drafting does not authorize sending. Before any send or distribution action:

1. Show the complete final email, including recipients, subject, and body.
2. Ask the project manager for explicit approval.
3. If any recipient, subject, body text, attachment, owner, deadline, or meeting fact changes after approval, show the revised final version and obtain approval again.
4. Send only when the user explicitly instructs sending the approved version and the required communication tool is available.
