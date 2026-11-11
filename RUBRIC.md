# Preference rubric — preference-v1

Read the prompt and both answers before choosing. The visible A/B order is
deterministically varied per task/reviewer and mapped back to canonical responses.
Model identity, prior labels, and other reviewers' rationales are not in the blind view.

Consider these dimensions in order:

1. Factual correctness and support: avoid unsupported claims; acknowledge missing evidence.
2. Instruction following: satisfy requested language, format, scope and constraints.
3. Completeness and relevance: provide what the prompt needs without irrelevant additions.
4. Clarity and responsible handling: explain clearly; avoid exposing confidential data.

Choose A/B when there is a defensible overall preference. Choose `tie` when the
answers are comparable under the rubric; the exporter excludes ties from binary
preference rows. Choose `skip` when you lack enough knowledge or context to judge.
A skipped task can receive a replacement reviewer. Do not choose the longer answer
by default. Preference is a comparative judgment, not proof that the chosen answer
is correct or safe in every respect.

Write at least ten characters of rationale, explaining the concrete difference.
The minimum length is a schema check, not an assessment of rationale quality.
Confidence (1 low, 2 medium, 3 high) is stored for review and does not weight votes.

Two usable independent judgments are required. Agreement on 0/1 yields a binary
pair; disagreement requires an adjudicator using canonical response_0/response_1
after inspecting both answers and rationales. The adjudicator may also choose a tie.
Roles cannot be changed in-place. Existing votes and decisions cannot be edited;
corrected data requires a new task/version and an explicit supersession policy
outside this prototype. Do not silently duplicate corrected pairs into training.

This local prototype checks registered CLI roles; it does not authenticate humans.
Running two aliases yourself is practice, not independent double annotation.
Do not use private Outlier tasks or client data as public training examples.
