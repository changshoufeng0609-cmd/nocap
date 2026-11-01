"""Narration and caption text for the v2.1 walkthrough (a re-edit of the v2 capture) and its X cut.

Each cue has the caption shown on screen and the text sent to the local Kokoro voice. They differ only
where pronunciation needs help: figures are spelled out, and NoCap, GAAP, SEC, UTC, GPU, API, 10-K, SAM and
Baseten are respelled. The respellings were checked against the phonemes Kokoro produces.
Every figure is a value visible in the recorded product or quoted from README.md's Results table,
eval/qa_results.md or docs/open-weights.md.

v2.1 changes from v2: c02 says the errors were planted; c17 and c18 describe all 32 slide readings; c23 is the
four-backends card that replaces the benchmark-page and Private-card lines; c24 narrates the Private-check shot
(the recorded Modal Glimmer run, replayed) and is used only when that capture is passed to the renderer; c25 is
the held-out results line; c26 is v2's end-card line, unchanged.
"""
from __future__ import annotations

VOICE = 'am_fenrir'           # chosen with select_voice_v2.py; reasons in narration-license.md
SPEED = 1.0
VERSION = 'v2.1'

# (cue id, scene, caption, spoken text)
CUES = [
    ('c01', 'cold-card',
     'One wrong number in an earnings story becomes a public correction.',
     'One wrong number in an earnings story becomes a public correction.'),
    ('c02', 'cold-footage',
     'This AI-drafted story has two planted errors. NoCap flags both, with the filed figures.',
     'This AI-drafted story has two planted errors. No Cap flags both, with the filed figures.'),
    ('c03', 'title-card',
     'NoCap checks every financial number in a draft against SEC filings.',
     'No Cap checks every financial number in a draft against S E C filings.'),
    ('c04', 'check-wide',
     'Muse Spark pulls out each claim. Code checks every one against the filings.',
     'Muse Spark pulls out each claim. Code checks every one against the filings.'),
    ('c05', 'check-tiles',
     "Seven verified. Two wrong. Two unverifiable, because NoCap won't guess.",
     "Seven verified. Two wrong. Two unverifiable, because No Cap won't guess."),
    ('c06', 'receipt-growth',
     "The draft says revenue grew 18% from a year ago. That's the quarterly rate.",
     "The draft says revenue grew eighteen percent from a year ago. That's the quarterly rate."),
    ('c07', 'receipt-math',
     'The receipt does the math from the filings: 105.85%.',
     'The receipt does the math from the filings: one hundred five point eight five percent.'),
    ('c08', 'receipt-sources',
     'Each receipt cites the filing, down to when EDGAR accepted it.',
     'Each receipt cites the filing, down to when Edgar accepted it.'),
    ('c09', 'receipt-eps',
     'And $2.22 is adjusted EPS. The GAAP figure was $2.46.',
     'And two twenty-two is adjusted earnings per share. The gap figure was two forty-six.'),
    ('c10', 'fix-click',
     'Fix with receipts: Muse Wonder proposes corrected numbers, and code lets only the digits change.',
     'Fix with receipts. Muse Wonder proposes corrected numbers, and code lets only the digits change.'),
    ('c11', 'fix-result',
     'Then the whole draft is checked again: nine verified, zero wrong.',
     'Then the whole draft is checked again. Nine verified, zero wrong.'),
    ('c12', 'fix-receipts',
     'Each correction keeps its receipt.',
     'Each correction keeps its receipt.'),
    ('c13', 'cutoff-check',
     'Time travel: check this roundup as of July 29, 4 p.m. UTC.',
     'Time travel. Check this roundup as of July twenty-ninth, four P M, U T C.'),
    ('c14', 'cutoff-receipt',
     "Microsoft's revenue figure is right, but its filing reached EDGAR hours later, so it's marked not public yet.",
     "Microsoft's revenue figure is right, but its filing reached Edgar hours later, so it's marked not public yet."),
    ('c15', 'slide-load',
     "Now a real SEC slide: Bank of America's earnings presentation.",
     "Now a real S E C slide. Bank of America's earnings presentation."),
    ('c16', 'slide-run',
     'Its recorded live run: three model calls, 21 seconds, about three cents.',
     'Its recorded live run: three model calls, twenty-one seconds, about three cents.'),
    ('c17', 'slide-regions',
     'SAM 3.1 finds both bar charts, and Muse Spark reads 32 printed labels.',
     'Sam three point one finds both bar charts, and Muse Spark reads thirty-two printed labels.'),
    # The 27 unverifiable readings: 11 FTE-qualified (non-GAAP), 15 untracked metrics (yields, NII excluding
    # Global Markets) and 1 ambiguous ($16.0B with no stated basis), per the recorded response.
    ('c18', 'slide-verified',
     'Only the five GAAP bars verify. The rest are non-GAAP, untracked or ambiguous, so they stay unverifiable.',
     'Only the five gap bars verify. The rest are non-gap, untracked or ambiguous, so they stay unverifiable.'),
    ('c19', 'slide-receipt',
     'Each reading opens its receipt.',
     'Each reading opens its receipt.'),
    ('c20', 'ask-setup',
     "Ask a question of Meta's entire 10-K: more than half a million characters.",
     "Ask a question of Meta's entire ten K. More than half a million characters."),
    ('c21', 'ask-tokens',
     'One Muse Spark request reads all of it in six seconds: 101,135 input tokens, no retrieval, no truncation.',
     'One Muse Spark request reads all of it in six seconds. A hundred and one thousand input tokens, no retrieval, no truncation.'),
    ('c22', 'ask-quotes',
     'Code keeps only exact quotes it can find in the filing.',
     'Code keeps only exact quotes it can find in the filing.'),
    # "backends" alone phonemizes as bækˈɛndz (stress on "ends"); "back ends" gives bˈæk ˈɛndz. Whisper small heard
    # "four back ends" and medium "four backends".
    ('c23', 'backends-card',
     'Same verifier, four backends: Spark Open found all 27 gold claims, and Glimmer reads unpublished drafts on our own GPU.',
     'Same verifier, four back ends. Spark Open found all twenty-seven gold claims, and Glimmer reads unpublished drafts on our own G P U.'),
    # c24 narrates the Private shot (renderer --private-take). Checked against the recording replayed in it:
    # data/snapshot/recordings/private, a live Modal Glimmer run on 2026-10-30 18:17 UTC (1 model call, 25.8 s) of a
    # public synthetic NVIDIA draft: revenue $96.2 billion verified; GAAP diluted EPS $2.22 wrong (the planted error;
    # $2.22 is non-GAAP, GAAP is $2.46). Not "Glimmer too": Whisper hears "Glimmer 2".
    ('c24', 'private-shot',
     'Private check runs on Glimmer: one real run on our own Modal GPU, one figure verified, one planted error caught.',
     'Private check runs on Glimmer: one real run on our own Modal G P U, one figure verified, one planted error caught.'),
    ('c25', 'results-card',
     'On held-out tests, NoCap caught 44 of 48 planted errors, with one false alarm in 47 clean sentences.',
     'On held-out tests, No Cap caught forty-four of forty-eight planted errors, with one false alarm in forty-seven clean sentences.'),
    ('c26', 'end-card',
     'NoCap, for financial journalists and analysts. Built during this hackathon by Robert Chang. Try it live through November 17.',
     'No Cap, for financial journalists and analysts. Built during this hackathon by Robert Chang. Try it live through November seventeenth.'),
]

OPTIONAL = {'c24'}            # cues that exist only when their footage exists
X_CUES = ['c01', 'c02', 'c03', 'c06', 'c07', 'c10', 'c11', 'c17', 'c26']   # the 45-60 s X cut


def cue(cue_id):
    return next(item for item in CUES if item[0] == cue_id)


SCENE_CUE = {scene: cue_id for cue_id, scene, _, _ in CUES}
