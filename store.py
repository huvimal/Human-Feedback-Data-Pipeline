"""Local SQLite annotation store. CLI identities are not authenticated users."""
import hashlib
import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

RUBRIC_VERSION = "preference-v1"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def normalize(value):
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def quarantine(task):
    reasons = []
    if task["permission"] not in {"synthetic", "original", "permitted"} or not task["allowed_for_training"]:
        reasons.append("training_permission_not_declared")
    text = " ".join(task[key] for key in ["prompt", "response_0", "response_1"])
    if re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", text):
        reasons.append("possible_email")
    if re.search(r"(?<!\w)(?:\+84|0)(?:[ .-]?\d){9,10}(?!\d)", text):
        reasons.append("possible_phone")
    if re.search(r"\b(?:gsk_|sk-)[A-Za-z0-9_-]{12,}", text):
        reasons.append("possible_api_secret")
    if normalize(task["response_0"]) == normalize(task["response_1"]):
        reasons.append("identical_responses")
    return reasons


def validate_task(task):
    fields = ["id", "group", "prompt", "response_0", "response_1", "source", "permission"]
    if not isinstance(task, dict) or any(not isinstance(task.get(k), str) or not task[k].strip() for k in fields):
        raise ValueError("Task requires nonempty string id/group/prompt/responses/source/permission.")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", task["id"]):
        raise ValueError("Task ID must use letters, digits, underscore or hyphen, up to 80 characters.")
    if type(task.get("allowed_for_training")) is not bool:
        raise ValueError("allowed_for_training must be a JSON boolean.")
    return {key: task[key] for key in fields + ["allowed_for_training"]}


class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=15, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS reviewers(name TEXT PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('reviewer','adjudicator')));
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS assignments(task_id TEXT NOT NULL REFERENCES tasks(id), reviewer TEXT NOT NULL REFERENCES reviewers(name),
          flipped INTEGER NOT NULL CHECK(flipped IN (0,1)), PRIMARY KEY(task_id,reviewer));
        CREATE TABLE IF NOT EXISTS votes(task_id TEXT NOT NULL, reviewer TEXT NOT NULL, preference TEXT NOT NULL CHECK(preference IN ('0','1','tie','skip')),
          rationale TEXT NOT NULL, confidence INTEGER NOT NULL CHECK(confidence BETWEEN 1 AND 3), rubric TEXT NOT NULL,
          PRIMARY KEY(task_id,reviewer), FOREIGN KEY(task_id,reviewer) REFERENCES assignments(task_id,reviewer));
        CREATE TABLE IF NOT EXISTS decisions(task_id TEXT PRIMARY KEY REFERENCES tasks(id), adjudicator TEXT NOT NULL REFERENCES reviewers(name),
          preference TEXT NOT NULL CHECK(preference IN ('0','1','tie')), rationale TEXT NOT NULL, rubric TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL);
        ''')
        for table in ["reviewers", "tasks", "assignments", "votes", "decisions", "events"]:
            # Fixed internal identifiers only; user values always use parameter binding.
            for operation in ["UPDATE", "DELETE"]:
                self.db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation} BEFORE {operation} ON {table} "
                                "BEGIN SELECT RAISE(ABORT, 'append-only table'); END")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self, write=True):
        self.db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def event(self, action, data):
        previous = self.db.execute("SELECT digest FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        previous = previous[0] if previous else "0"*64
        payload = canonical({"action": action, "data": data, "utc": datetime.now(timezone.utc).isoformat()})
        digest = sha({"previous": previous, "payload": payload})
        self.db.execute("INSERT INTO events(payload,previous,digest) VALUES(?,?,?)", (payload, previous, digest))

    def add_reviewer(self, name, role="reviewer"):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", name) or role not in {"reviewer", "adjudicator"}:
            raise ValueError("Use a short ASCII reviewer alias and reviewer/adjudicator role.")
        with self.transaction():
            old = self.db.execute("SELECT role FROM reviewers WHERE name=?", (name,)).fetchone()
            if old:
                if old[0] != role:
                    raise ValueError("Reviewer already exists with a different immutable role.")
                return
            self.db.execute("INSERT INTO reviewers VALUES(?,?)", (name, role))
            self.event("register_reviewer", {"name": name, "role": role})

    def import_tasks(self, tasks):
        checked = [validate_task(task) for task in tasks]
        inserted = 0
        with self.transaction():
            for task in checked:
                digest = sha(task)
                old = self.db.execute("SELECT digest FROM tasks WHERE id=?", (task["id"],)).fetchone()
                if old:
                    if old[0] != digest:
                        raise ValueError(f"Task {task['id']} already exists with different content; import rolled back.")
                    continue
                self.db.execute("INSERT INTO tasks VALUES(?,?,?)", (task["id"], canonical(task), digest))
                self.event("import_task", {"task_id": task["id"], "content_sha256": digest})
                inserted += 1
        return inserted

    def task(self, task_id):
        row = self.db.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise ValueError("Unknown task ID.")
        return json.loads(row[0])

    def require_role(self, name, role):
        row = self.db.execute("SELECT role FROM reviewers WHERE name=?", (name,)).fetchone()
        if not row or row[0] != role:
            raise ValueError(f"Alias must be registered as {role}.")

    def assign(self, task_id, reviewer):
        with self.transaction():
            self.require_role(reviewer, "reviewer")
            task = self.task(task_id)
            if quarantine(task):
                raise ValueError("Task is quarantined; it cannot be assigned.")
            existing = self.db.execute("SELECT flipped FROM assignments WHERE task_id=? AND reviewer=?", (task_id, reviewer)).fetchone()
            if existing:
                return self.blind_view(task_id, reviewer)
            if self.db.execute("SELECT COUNT(*) FROM votes WHERE task_id=? AND preference!='skip'", (task_id,)).fetchone()[0] >= 2:
                raise ValueError("Task already has two usable reviews.")
            flipped = int(sha([task_id, reviewer, RUBRIC_VERSION])[-1], 16) % 2
            self.db.execute("INSERT INTO assignments VALUES(?,?,?)", (task_id, reviewer, flipped))
            self.event("assign", {"task_id": task_id, "reviewer": reviewer, "flipped": flipped})
            return self.blind_view(task_id, reviewer)

    def blind_view(self, task_id, reviewer):
        row = self.db.execute("SELECT flipped FROM assignments WHERE task_id=? AND reviewer=?", (task_id, reviewer)).fetchone()
        if not row:
            raise ValueError("Request an assignment first.")
        task = self.task(task_id)
        flip = row[0]
        return {"task_id": task_id, "reviewer": reviewer, "rubric": RUBRIC_VERSION, "prompt": task["prompt"],
                "A": task[f"response_{flip}"], "B": task[f"response_{1-flip}"],
                "choices": ["A", "B", "tie", "skip"]}

    def vote(self, task_id, reviewer, choice, rationale, confidence=2):
        if choice not in {"A", "B", "tie", "skip"} or not 1 <= confidence <= 3 or len(rationale.strip()) < 10:
            raise ValueError("Use A/B/tie/skip, confidence 1..3, and a rationale of at least 10 characters.")
        with self.transaction():
            self.require_role(reviewer, "reviewer")
            row = self.db.execute("SELECT flipped FROM assignments WHERE task_id=? AND reviewer=?", (task_id, reviewer)).fetchone()
            if not row:
                raise ValueError("Request an assignment first.")
            if self.db.execute("SELECT 1 FROM votes WHERE task_id=? AND reviewer=?", (task_id, reviewer)).fetchone():
                raise ValueError("A review is immutable; duplicate vote rejected.")
            if self.db.execute("SELECT COUNT(*) FROM votes WHERE task_id=? AND preference!='skip'", (task_id,)).fetchone()[0] >= 2:
                raise ValueError("Task already has two usable reviews.")
            preference = str(row[0] if choice == "A" else 1-row[0]) if choice in {"A", "B"} else choice
            self.db.execute("INSERT INTO votes VALUES(?,?,?,?,?,?)", (task_id, reviewer, preference, rationale, confidence, RUBRIC_VERSION))
            self.event("vote", {"task_id": task_id, "reviewer": reviewer, "preference": preference,
                                "rationale_sha256": sha(rationale), "confidence": confidence, "rubric": RUBRIC_VERSION})

    def adjudicate(self, task_id, adjudicator, preference, rationale):
        if preference not in {"0", "1", "tie"} or len(rationale.strip()) < 10:
            raise ValueError("Use canonical 0/1/tie and a rationale of at least 10 characters.")
        with self.transaction():
            self.require_role(adjudicator, "adjudicator")
            task = self.task(task_id)
            if quarantine(task):
                raise ValueError("Cannot adjudicate quarantined content.")
            votes = [r[0] for r in self.db.execute("SELECT preference FROM votes WHERE task_id=? AND preference!='skip'", (task_id,))]
            if len(votes) != 2 or len(set(votes)) < 2:
                raise ValueError("Adjudication requires exactly two conflicting usable reviews.")
            self.db.execute("INSERT INTO decisions VALUES(?,?,?,?,?)", (task_id, adjudicator, preference, rationale, RUBRIC_VERSION))
            self.event("adjudicate", {"task_id": task_id, "adjudicator": adjudicator, "preference": preference,
                                      "rationale_sha256": sha(rationale), "rubric": RUBRIC_VERSION})

    def snapshot(self):
        with self.transaction(write=False):
            return {table: [dict(r) for r in self.db.execute(f"SELECT * FROM {table} ORDER BY " +
                    ("seq" if table == "events" else "name" if table == "reviewers" else "id" if table == "tasks" else
                     "task_id,reviewer" if table in {"assignments", "votes"} else "task_id"))]
                    for table in ["reviewers", "tasks", "assignments", "votes", "decisions", "events"]}


def audit_chain(snapshot):
    previous = "0"*64
    for expected_seq, row in enumerate(snapshot["events"], 1):
        if row["seq"] != expected_seq or row["previous"] != previous or row["digest"] != sha({"previous": previous, "payload": row["payload"]}):
            raise ValueError("Event-chain verification failed.")
        previous = row["digest"]
    return {"events": len(snapshot["events"]), "event_head_sha256": previous}
