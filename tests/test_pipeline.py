import copy
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from make_demo import build
from release import export_release, file_sha, group_map, quality, verify_release
from store import Store, audit_chain, canonical, quarantine, sha


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.path = self.folder/"feedback.db"
        self.store = Store(self.path)
        self.tasks = build()
        self.store.import_tasks(self.tasks)
        for name, role in [("mai", "reviewer"), ("nam", "reviewer"), ("third", "reviewer"), ("lead", "adjudicator")]:
            self.store.add_reviewer(name, role)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def vote(self, tid, name, preference="0"):
        view = self.store.assign(tid, name)
        task = self.store.task(tid)
        choice = preference if preference in {"tie", "skip"} else "A" if view["A"] == task[f"response_{preference}"] else "B"
        self.store.vote(tid, name, choice, "A sufficient original test rationale.")

    def ready(self, duplicate_preference="1"):
        for task in self.tasks:
            tid = task["id"]
            if tid in {"t12", "t13"}:
                continue
            for name in ["mai", "nam"]:
                if tid == "t11" and name == "nam":
                    continue
                self.vote(tid, name, "tie" if tid == "t10" else duplicate_preference if tid == "t15" else "1" if tid == "t09" and name == "nam" else "0")
        self.store.adjudicate("t09", "lead", "0", "Hold out test data from model selection.")

    def release(self, version="v1"):
        self.ready()
        return export_release(self.store.snapshot(), self.folder/"exports", version)[0]

    def test_idempotent_import(self):
        before = len(self.store.snapshot()["events"])
        self.assertEqual(self.store.import_tasks(self.tasks), 0)
        self.assertEqual(len(self.store.snapshot()["events"]), before)

    def test_conflicting_import_rolls_back_batch(self):
        first = {**self.tasks[0], "id": "new"}
        conflicting = {**self.tasks[1], "prompt": "changed"}
        with self.assertRaises(ValueError):
            self.store.import_tasks([first, conflicting])
        with self.assertRaises(ValueError):
            self.store.task("new")

    def test_blind_view_and_canonical_mapping(self):
        view = self.store.assign("t01", "mai")
        self.assertEqual(view, self.store.assign("t01", "mai"))
        self.assertNotIn("flipped", view)
        self.assertNotIn("response_0", view)
        self.assertNotIn("source", view)
        self.vote("t01", "mai", "1")
        self.assertEqual(self.store.snapshot()["votes"][0]["preference"], "1")

    def test_duplicate_vote_rejected_without_extra_event(self):
        self.vote("t01", "mai")
        before = len(self.store.snapshot()["events"])
        with self.assertRaises(ValueError):
            self.store.vote("t01", "mai", "A", "Trying to submit the same task again.")
        self.assertEqual(len(self.store.snapshot()["events"]), before)

    def test_assignment_required(self):
        with self.assertRaises(ValueError):
            self.store.vote("t01", "mai", "A", "No assignment was requested first.")

    def test_role_separation(self):
        with self.assertRaises(ValueError):
            self.store.assign("t01", "lead")
        self.vote("t01", "mai", "0")
        self.vote("t01", "nam", "1")
        with self.assertRaises(ValueError):
            self.store.adjudicate("t01", "mai", "0", "Reviewer cannot adjudicate this case.")

    def test_skip_can_be_replaced(self):
        self.vote("t01", "mai", "skip")
        self.vote("t01", "nam", "0")
        status = next(r for r in quality(self.store.snapshot())["rows"] if r["task_id"] == "t01")
        self.assertEqual(status["status"], "pending")
        self.vote("t01", "third", "0")
        status = next(r for r in quality(self.store.snapshot())["rows"] if r["task_id"] == "t01")
        self.assertEqual(status["status"], "ready_consensus")

    def test_disagreement_requires_adjudication(self):
        self.vote("t01", "mai", "0")
        self.vote("t01", "nam", "1")
        self.assertEqual(quality(self.store.snapshot())["counts"]["disagreement"], 1)
        self.store.adjudicate("t01", "lead", "0", "Evidence supports the first canonical response.")
        self.assertEqual(quality(self.store.snapshot())["counts"]["ready_adjudicated"], 1)

    def test_consensus_cannot_be_adjudicated(self):
        self.vote("t01", "mai")
        self.vote("t01", "nam")
        with self.assertRaises(ValueError):
            self.store.adjudicate("t01", "lead", "1", "Attempt to override a consensus vote.")

    def test_append_only_database(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute("DELETE FROM tasks WHERE id='t01'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute("UPDATE reviewers SET role='adjudicator' WHERE name='mai'")

    def test_quarantine_before_assignment(self):
        self.assertIn("possible_email", quarantine(self.tasks[11]))
        self.assertIn("training_permission_not_declared", quarantine(self.tasks[12]))
        for tid in ["t12", "t13"]:
            with self.assertRaises(ValueError):
                self.store.assign(tid, "mai")

    def test_transitive_grouping(self):
        tasks = [{"id": "a", "group": "x", "prompt": "same"},
                 {"id": "b", "group": "y", "prompt": " SAME "},
                 {"id": "c", "group": "y", "prompt": "other"}]
        mapping = group_map(tasks)
        self.assertEqual(len(set(mapping.values())), 1)

    def test_release_schema_dedup_and_group_splits(self):
        target = self.release()
        result = verify_release(target)
        self.assertEqual(result["pairs"], 10)
        self.assertEqual(result["groups"], 9)
        lines = [json.loads(l) for l in (target/"lineage.jsonl").read_text(encoding="utf-8").splitlines()]
        by_id = {line["task_id"]: line for line in lines}
        self.assertEqual(by_id["t01"]["split"], by_id["t14"]["split"])
        self.assertNotIn("t15", by_id)
        quality_report = json.loads((target/"quality.json").read_text(encoding="utf-8"))
        self.assertEqual(CounterReasons(quality_report["excluded"])["duplicate_pair"], 1)

    def test_no_rationale_in_training_jsonl(self):
        target = self.release()
        for split in ["train", "dev", "test"]:
            for line in (target/f"{split}.jsonl").read_text(encoding="utf-8").splitlines():
                self.assertEqual(set(json.loads(line)), {"prompt", "chosen", "rejected"})

    def test_contradictory_duplicates_exclude_both(self):
        self.ready(duplicate_preference="0")
        target, _ = export_release(self.store.snapshot(), self.folder/"exports", "conflict")
        report = json.loads((target/"quality.json").read_text(encoding="utf-8"))
        rejected = {r["task_id"] for r in report["excluded"] if r["reason"] == "duplicate_conflicting_preferences"}
        self.assertEqual(rejected, {"t01", "t15"})
        self.assertEqual(verify_release(target)["pairs"], 9)

    def test_release_cannot_be_overwritten(self):
        self.release()
        with self.assertRaises(ValueError):
            export_release(self.store.snapshot(), self.folder/"exports", "v1")

    def test_tampered_file_detected(self):
        target = self.release()
        with (target/"train.jsonl").open("a", encoding="utf-8") as f:
            f.write("{}\n")
        with self.assertRaisesRegex(ValueError, "checksum"):
            verify_release(target)

    def test_tampered_group_detected_even_after_rehash(self):
        target = self.release()
        path = target/"lineage.jsonl"
        items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        a = items[0]
        b = next(i for i in items if i["split"] != a["split"])
        b["group_sha256"] = a["group_sha256"]
        path.write_text("".join(canonical(i)+"\n" for i in items), encoding="utf-8")
        manifest_path = target/"manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"]["lineage.jsonl"] = file_sha(path)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Group leakage"):
            verify_release(target)

    def test_tampered_event_chain(self):
        snapshot = self.store.snapshot()
        snapshot["events"][0]["payload"] += "changed"
        with self.assertRaisesRegex(ValueError, "Event-chain"):
            audit_chain(snapshot)

    def test_minimum_export_gate(self):
        with self.assertRaisesRegex(ValueError, "eligible"):
            export_release(self.store.snapshot(), self.folder/"exports", "empty")
        self.ready()
        with self.assertRaises(ValueError):
            export_release(self.store.snapshot(), self.folder/"exports", "too_many", min_pairs=50)

    def test_kappa_known_value(self):
        for tid, a, b in [("t01", "0", "0"), ("t02", "1", "0")]:
            self.vote(tid, "mai", a)
            self.vote(tid, "nam", b)
        agreement = quality(self.store.snapshot())["agreement"][0]
        self.assertEqual(agreement["raw_agreement"], .5)
        self.assertEqual(agreement["cohens_kappa"], 0)

    def test_two_workers_cannot_add_third_usable_vote(self):
        self.vote("t01", "mai", "0")
        for name in ["nam", "third"]:
            self.store.assign("t01", name)
        def worker(name):
            store = Store(self.path)
            try:
                store.vote("t01", name, "A", "Concurrent original test review rationale.")
                return "saved"
            except ValueError:
                return "blocked"
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, ["nam", "third"]))
        self.assertEqual(sorted(results), ["blocked", "saved"])
        self.assertEqual(len(self.store.snapshot()["votes"]), 2)
        audit_chain(self.store.snapshot())


def CounterReasons(rows):
    from collections import Counter
    return Counter(row["reason"] for row in rows)


if __name__ == "__main__":
    unittest.main()
