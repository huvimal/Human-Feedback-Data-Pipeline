# Human Feedback Data Pipeline

A local, standard-library Python pipeline for collecting pairwise preference labels
and releasing auditable training-data snapshots. It demonstrates annotation data
engineering; it does **not** train an LLM, reward model, or DPO model.

## What is implemented

- Immutable task import with content hashes and atomic, idempotent batches.
- Per-reviewer A/B presentation without model identity or prior labels in the view.
- Two usable reviews, explicit ties/skips, written rationales and confidence.
- A separate registered adjudicator role for conflicting labels.
- SQLite transactions, unique constraints, foreign keys, and append-only triggers.
- Chained audit events for ingestion, assignments, votes and adjudication.
- Source/training-permission declarations and limited regex-based quarantine.
- Descriptive pairwise raw agreement and Cohen's kappa before adjudication.
- Group-safe train/dev/test splits and reversed-pair deduplication.
- Explicit-prompt `prompt/chosen/rejected` JSONL exports, separate lineage, manifest,
  file SHA-256 checksums, and verification CLI.
- 22 unit tests and offline CI using synthetic fixtures.

## Quick start — Windows PowerShell

Python 3.11+; no package installation or API key required.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe pipeline.py demo
.\.venv\Scripts\python.exe pipeline.py inspect-dispute --task t09 --adjudicator lead
.\.venv\Scripts\python.exe pipeline.py adjudicate --task t09 --adjudicator lead --preference 0 --rationale "Test data must stay separate from model selection."
.\.venv\Scripts\python.exe pipeline.py quality
.\.venv\Scripts\python.exe pipeline.py audit
.\.venv\Scripts\python.exe pipeline.py export --version v1
.\.venv\Scripts\python.exe pipeline.py verify --release exports/v1
Start-Process reports/quality.html
```

The demo creates 15 original synthetic tasks and **scripted** labels from reviewer
aliases `mai` and `nam`, plus adjudicator alias `lead`. These are not real independent
human judgments, agreement findings, or evidence of model performance.
`make_demo.py` regenerates the committed task fixtures; it does not run models.

Before adjudication, counts are 10 ready-consensus, 1 disagreement, 1 tie,
1 pending, and 2 quarantined. After resolving t09 to response_0, export contains
**10 unique binary pairs** in **9 groups**, split **7 train / 1 dev / 2 test**.
Five tasks are excluded: tie, pending, two quarantines and one reversed duplicate.
This miniature split exercises the code; it is far too small for training claims.

Demo imports/reviews are idempotent on an existing demo database, but it will not
overwrite your manual votes. Repeating an adjudication is rejected. Repeating an
export version is rejected; use a new version name. `quality` regenerates the HTML
report from current state. Old exports remain frozen even after further labeling.

## Manual annotation exercise

Read `RUBRIC.md`, then import an additional original exercise:

```powershell
.\.venv\Scripts\python.exe pipeline.py import --file data/manual_task.json
.\.venv\Scripts\python.exe pipeline.py label --task manual01 --reviewer mai
.\.venv\Scripts\python.exe pipeline.py label --task manual01 --reviewer nam
.\.venv\Scripts\python.exe pipeline.py quality
```

Each `label` prints the question and blinded A/B responses, then prompts for a
choice and rationale. Select the answer based on its text; A/B can differ by reviewer.
Labels are canonicalized before agreement or export. Repeating two aliases alone is
a workflow exercise, **not independent human annotation**. Real data collection
requires separately recruited reviewers and authentication outside this prototype.

Register new aliases with `register --name reviewer3 --role reviewer`.
`assign` displays an assignment; `view` displays an existing one; noninteractive
`vote --task ... --reviewer ... --choice A --rationale "..." --confidence 2` records it.
`skip` does not count toward the required two usable reviews; another reviewer can
replace it. A third usable vote is rejected even if concurrent workers race.

## Task import schema

`import --file` accepts a JSON list of objects:

```json
[{
  "id": "example01",
  "group": "same-conversation-or-document-family",
  "prompt": "Question",
  "response_0": "One answer",
  "response_1": "Another answer",
  "source": "Original example authored for this collection",
  "permission": "original",
  "allowed_for_training": true
}]
```

Use `synthetic`, `original`, or `permitted` for declared training rights. Unknown
rights or false training permission lead to quarantine. Email, Vietnamese-style
phone, API-key-like patterns and identical normalized answers are also quarantined.
These are heuristic flags, **not a privacy classifier or license verification**.
No automatic redaction or permission acquisition occurs. Rationales are local
private data and are not included in training JSONL or release lineage.

IDs and records are immutable. Identical re-imports are no-ops; content changes to
an existing ID roll back the entire import batch. To correct an annotation, this
prototype requires a new task/version; it does not implement a full withdrawal,
supersession, or legal data-deletion workflow. It is unsuitable for regulated
personal-data collection without additional governance.

## Quality and adjudication

States: `quarantined`, `pending`, `disagreement`, `tie`, `ready_consensus`,
`ready_adjudicated`. Only the last two can yield a binary preference row.
Two matching ties are retained in the store and excluded from binary export.
Mixed tie/directional labels count as disagreement and can be adjudicated.
An adjudicator cannot override an existing consensus through this command.

Agreement uses canonical labels 0/1/tie on jointly reviewed tasks and excludes skips.
Cohen's kappa is `(observed agreement - chance agreement)/(1 - chance agreement)`.
It is undefined (`null`) when chance agreement is 1. Samples are tiny and labels in
the demo are scripted. Agreement does not establish correctness or fairness.
Confidence and rationale length do not automatically establish label quality.

## Splits, duplicates and releases

Before splitting, union tasks linked by a supplied `group` **or an identical NFC,
casefolded, whitespace-normalized prompt**, including transitive links. Products,
conversations, paraphrases and document families should receive appropriate supplied
groups. The tool does not detect semantic near-duplicates or train/test contamination
from external corpora. Normalization preserves punctuation and diacritics.

Dedup uses the normalized prompt plus an **unordered** pair of normalized responses.
If equivalent pairs disagree on the preferred answer, all conflicting copies are
excluded with `duplicate_conflicting_preferences`. Otherwise the first eligible task
in task-ID order is retained, without confidence weighting.
Export enforces at least three eligible pairs and three independent
groups, deterministically allocating approximately 70%/15%/15% of groups (not rows).
Tiny groups lead to uneven row counts. An expanded release can reshuffle prior groups
because grouping and allocation are recomputed; compare versions with their manifests,
and freeze a separate test registry before real training comparisons.

`exports/v1/` contains:

- `train.jsonl`, `dev.jsonl`, `test.jsonl`: prompt/chosen/rejected strings only.
- `lineage.jsonl`: row mapping, source, declared permission, group/content/vote hashes,
  rubric and resolution status. No reviewer names or raw rationales.
- `quality.json`: status counts, excluded task reasons, agreement and local aliases.
- `manifest.json`: snapshot hash, audit head, policy, split counts and file hashes.

The explicit-prompt preference shape follows the standard format documented by
[Hugging Face TRL](https://huggingface.co/docs/trl/dpo_trainer#expected-dataset-type-and-format).
This only provides compatible row fields; tokenizer/chat-template configuration,
model suitability, actual fine-tuning and downstream evaluation are outside scope.

Export reads a consistent SQLite snapshot, stages files, verifies them and renames
the staged directory into a new release. Existing versions are not overwritten.
Verification checks checksums, row schema/counts, duplicate pairs, lineage coverage
and prompt/group split consistency. It does not validate the truth of labels or
authenticate declared source permissions. File hashes and audit chains detect
accidental changes; a database/filesystem owner can rewrite history and hashes.
There is no cryptographic signature, remote append-only log, or multi-host release lock.

## Local boundaries

Reviewers and adjudicators are **CLI aliases**. Role checks and SQL constraints
enforce workflow rules inside this process, not real identity/access control.
Anyone able to run the CLI or read the DB can inspect canonical responses and labels.
A/B shuffling demonstrates presentation control but does not prove elimination of
position bias. The same person using two aliases is not a two-reviewer study.

SQLite records and audit events are append-only through database triggers. This
protects normal application operations, not a hostile DB owner. Do not claim enterprise
security, tamper-proof governance, real reviewer agreement or improved LLM quality.

The default DB is `private/feedback.db` under this project. Use `--db PATH` **before**
the subcommand for a separate collection. Private data, reports, exports and DB files
are gitignored. CI uses only the committed synthetic tasks and scripted labels.
Publishing real exports requires reviewing the contents and permissions separately.
