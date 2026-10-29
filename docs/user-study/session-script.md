# Fifteen-minute moderated session

Use the current tested build and record its commit. The facilitator may load the required example before each task but should not point at a result or explain the answer until the participant has tried. Record every hint. If time runs out, mark the task **not attempted**, not failed or passed.

| Time | Activity |
|---|---|
| 0:00–1:30 | Consent, role and usual review workflow |
| 1:30–5:00 | Task 1: check the draft and trace a correction |
| 5:00–8:00 | Task 2: review Fix and unresolved guidance |
| 8:00–10:30 | Task 3: explain the historical cutoff |
| 10:30–12:30 | Task 4: optional validated slide flow, or source-link review |
| 12:30–15:00 | Five closing questions and permission check |

## Opening, read verbatim

“Thank you for trying NoCap. I'm testing the tool, not you. Please think aloud: what are you looking for, and what would you do next? These are supplied public examples with planted mistakes. Please do not paste unpublished or confidential work. You may skip a question or stop at any time. May I take anonymous notes? Separately, may I record this session? Declining recording is fine.”

Ask their role in broad terms and how they usually verify one earnings number. Avoid collecting employer or client information unless they volunteer it and it is necessary; the repository notes need only a role category.

## Task 1 — evidence, not a green badge

Start at the NVIDIA example in the editor.

**Prompt:** “Imagine you are reviewing this earnings draft before publication. Use NoCap to decide whether its GAAP earnings-per-share statement is ready to publish. Show me what you would use to support your decision.”

**Observe:** finds Check; recognizes `$2.22` is the wrong basis; finds filed GAAP `$2.46`; locates the filing link/accession. Do not count merely reading the corrected number as source verification. The growth-rate flag can be an additional observation, not a hinted prerequisite.

**Neutral prompts if needed:** “What are you looking for?” “What does this result mean to you?” If stuck, wait, then offer one action hint and mark assisted.

## Task 2 — corrections and remaining uncertainty

**Prompt:** “Try the correction feature. Review what it returns and decide what you would copy into your draft. Is there anything you would still investigate?”

Use a live Fix only within the session's model budget. If using a saved response for a guided session, say so before the task and label it in the recording; do not count it as a live performance test.

**Observe:** finds Fix with receipts; compares original and revised text; recognizes only numeric changes; inspects a receipt; notices both guidance figures remain `?`; understands that resolving red flags is not verification of the whole story. Note which copy option they choose and whether they expect citation numbering in the text.

## Task 3 — historical availability

Load **June-quarter roundup, as of July 29** with its supplied cutoff.

**Prompt:** “You are preparing this roundup at the time shown. Can you publish the Microsoft figures on the basis of NoCap's evidence? Explain what the result tells you.”

**Observe:** distinguishes not-public-yet from a wrong value; finds `2026-07-29T20:04:53Z` versus the `16:00Z` cutoff; understands the cutoff concerns EDGAR acceptance. Ask them to predict what will happen with **Now**, then let them check. Alphabet's entity error remains wrong.

## Task 4 — slide or source review

Use the slide task only after the integrated upload flow is validated and participants have a visible file chooser and results. Otherwise use the fallback for every participant in this round and record that choice.

**Slide prompt:** “Check this supplied earnings slide. Choose one printed value and show where you would look for evidence. What, if anything, would you trust about a value read from the chart axes?”

**Observe:** can upload the provided BAC image; connects the overlay to the claim/receipt; understands uploaded pixels are not independent evidence; does not treat an axis estimate as exact.

**Fallback prompt:** “Open a source link for one checked figure. What extra information would you need before citing this in your own work?”

## Five closing questions

1. “Where did you hesitate or lose track of what the tool was doing?”
2. “What did you take the checkmark, question mark and not-public-yet label to mean?”
3. “At what point, if any, would this fit into your usual review process?”
4. “What information would make you reject or investigate a correction?”
5. “If we could improve one thing before you tried it again, what should it be?”

Ask separately: “May I use this exact quote [read it back] in the project demo, anonymously / attributed as [agreed description]?” Record the exact permission scope. If permission is absent, keep the quote out of the demo. Thank the participant; do not ask for a positive testimonial.
