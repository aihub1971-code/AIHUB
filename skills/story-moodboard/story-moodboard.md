---
name: story-moodboard
description: Turn a supplied story passage into a visual creative brief and, only after the user approves that brief, a generated moodboard. Use for Story Moodboard requests or passage-based visual storytelling boards; do not activate for ordinary literary analysis or unrelated image requests.
---


# Story Moodboard
Translate a supplied passage into a coherent visual direction while preserving its character and period details. Create the creative brief first. Image generation is a separate step gated on the user's approval of that brief.
## Read the passage
Use the supplied passage as the source of story facts. If it is missing or unreadable, ask for it. Keep facts from the text distinct from creative proposals; do not invent a character's appearance, a historical date, a plot event, or a setting and present it as specified by the author.
Extract:
- The emotional arc, including how the dominant feeling changes
- Period and setting, with any unspecified details marked
- Characters and continuity details relevant to depicting them
- Important objects and their narrative or emotional role
- A proposed color palette tied to the passage's imagery and mood
- Three turning points, grounded in moments actually present in the passage
Count a turning point only when the passage supports a distinct plot or emotional change. Do not count every physical action or its immediate reaction as a separate turning point. Group an event and its immediate response into one causal beat unless the text establishes an additional distinct change; do not split one causal beat to reach three. Treat an opening state as orientation, not a change by itself.
If the passage does not support three distinct turning points, state the supported count and the shortfall explicitly. When only two changes are supported, report two turning points and say that a third is not established by the passage. Ask for more text or propose a clearly labeled visual sequence for approval rather than fabricating additional plot events. Label any three-panel composition in this situation as a proposed visual sequence, not three source turning points; panel count need not equal turning-point count.
Do not silently bring in another edition, adaptation, later plot, or outside historical research. If the desired treatment needs information absent from the passage, identify the gap and ask for the information or approval for a stated creative choice.
## Produce the creative brief
Provide a reviewable brief containing the extracted emotional arc, period, setting, significant objects, proposed palette, and three supported turning points or the stated gap. Add the visual treatment and a board concept that translates these elements into a coherent set of images.
- If the visual treatment is unclear, ask the user to choose it. Offer a small set of relevant treatments when useful, without implying one has already been selected.
- Record the details that must remain consistent across images: characters, clothing, objects, architecture, period cues, setting, and palette, as applicable to the passage.
- Label any proposed additions or stylization so the user can approve them as creative decisions.
- Ask the user to approve the brief before generating the board. A request to make a moodboard starts this workflow; it does not replace approval of the resulting brief.
Stop before image generation while approval is pending. If the user requests changes, revise the brief and resolve approval of the revised version before proceeding.
## Generate the approved board
After the user approves the brief, use an image-generation capability available in the current environment. This skill does not require a named provider, plugin, script, or platform-specific tool.
Carry the approved treatment, emotional progression, turning points, palette, and continuity details into the generation instructions. Check the resulting board against the approved brief, particularly character identity and period consistency. Correct supported generation mismatches where possible; ask before materially changing the approved creative direction.
If image generation is unavailable or blocked, report the actual blocker and retain the creative brief as the usable deliverable. Do not claim a board was generated when only a brief or prompt exists. Do not silently substitute a different service or authorize new spending, uploads, sharing, or installation.
Deliver the resulting board through the available file or image delivery mechanism, with a short explanation of how it follows the approved brief. Do not publish or send it to anyone else unless separately authorized.
## Reuse on a new passage
Start again from the new passage. Carry over a visual treatment or continuity decision only when the user requests continuity or the new passage belongs to the same approved project. Do not turn one story's period, characters, palette, or test facts into universal defaults.


## Executive PDF deliverable
When requested, create an actual downloadable executive-ready PDF brief before approval for new image generation. Preserve the approval gate above. For an existing approved board, embed that board without generating new images unless separately authorized. Include concise rationale, comparisons of proposed visual directions, recommendations, continuity and cultural-representation risks with mitigations. Use 2–4 labeled role-based analytical lenses (such as reader, director and designer) only where useful; never claim unspoken stakeholder opinions. Keep story facts, creative proposals and approved decisions distinct. Render and visually inspect every page, image, table and any Arabic shaping/RTL. State any generation or rendering limitation honestly; never claim a file or inspection that did not happen.
