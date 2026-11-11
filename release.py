"""Quality summaries, group-safe splits, and verifiable preference exports."""
import itertools
import hashlib
import json
import math
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from store import RUBRIC_VERSION, audit_chain, canonical, normalize, quarantine, sha


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def quality(snapshot):
    votes = defaultdict(list)
    for vote in snapshot["votes"]:
        votes[vote["task_id"]].append(vote)
    decisions = {d["task_id"]: d for d in snapshot["decisions"]}
    rows = []
    for item in snapshot["tasks"]:
        task = json.loads(item["payload"])
        current = [v for v in votes[task["id"]] if v["preference"] != "skip"]
        labels = {v["preference"] for v in current}
        reasons, decision, preference = quarantine(task), decisions.get(task["id"]), None
        if reasons:
            status = "quarantined"
        elif len(current) < 2:
            status = "pending"
        elif decision:
            preference = decision["preference"]
            status = "tie" if preference == "tie" else "ready_adjudicated"
        elif len(labels) != 1:
            status = "disagreement"
        else:
            preference = next(iter(labels))
            status = "tie" if preference == "tie" else "ready_consensus"
        rows.append({"task_id": task["id"], "status": status, "reasons": reasons,
                     "usable_votes": len(current), "preference": preference})
    pair_rows = defaultdict(list)
    for current in votes.values():
        current = sorted((v for v in current if v["preference"] != "skip"), key=lambda v: v["reviewer"])
        for a, b in itertools.combinations(current, 2):
            pair_rows[(a["reviewer"], b["reviewer"])].append((a["preference"], b["preference"]))
    agreement = []
    for pair, labels in sorted(pair_rows.items()):
        n = len(labels)
        observed = sum(a == b for a, b in labels)/n
        a_counts, b_counts = Counter(a for a, _ in labels), Counter(b for _, b in labels)
        chance = sum(a_counts[label]*b_counts[label] for label in ["0", "1", "tie"])/(n*n)
        agreement.append({"reviewers": list(pair), "shared_tasks": n, "raw_agreement": observed,
                          "cohens_kappa": (observed-chance)/(1-chance) if chance < 1 else None,
                          "labels": "canonical response_0 / response_1 / tie; skips excluded"})
    return {"rubric": RUBRIC_VERSION, "tasks": len(rows), "counts": dict(Counter(r["status"] for r in rows)),
            "rows": rows, "agreement": agreement,
            "limits": ["Agreement is descriptive, not proof of annotation correctness.",
                       "Reviewers are local CLI aliases, not authenticated identities.",
                       "Quarantine uses declarations and a few regex rules, not comprehensive privacy detection."]}


def group_map(tasks):
    """Union supplied groups and identical normalized prompts, including transitive links."""
    parent = {t["id"]: t["id"] for t in tasks}
    def root(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item
    groups, prompts = {}, {}
    for task in tasks:
        for registry, key in [(groups, task["group"]), (prompts, normalize(task["prompt"]))]:
            if key in registry:
                parent[root(task["id"])] = root(registry[key])
            else:
                registry[key] = task["id"]
    members = defaultdict(list)
    for task in tasks:
        members[root(task["id"])].append(task["id"])
    return {tid: sha(sorted(members[root(tid)])) for tid in parent}


def export_release(snapshot, output, version, min_pairs=3, seed=42):
    if not version or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in version):
        raise ValueError("Version must use letters, digits, underscore or hyphen.")
    if min_pairs < 3:
        raise ValueError("This three-split exporter requires min_pairs >= 3.")
    head = audit_chain(snapshot)
    summary = quality(snapshot)
    tasks = [json.loads(row["payload"]) for row in snapshot["tasks"]]
    by_id = {t["id"]: t for t in tasks}
    grouped = group_map(tasks)
    selected, excluded, eligible = [], [], defaultdict(list)
    for row in summary["rows"]:
        task = by_id[row["task_id"]]
        if row["status"] not in {"ready_consensus", "ready_adjudicated"}:
            excluded.append({"task_id": task["id"], "reason": row["status"]})
            continue
        pair_hash = sha([normalize(task["prompt"]), sorted([normalize(task["response_0"]), normalize(task["response_1"])])])
        eligible[pair_hash].append({**row, "task": task, "pair_sha256": pair_hash})
    for pair_rows in eligible.values():
        chosen = {normalize(row["task"][f"response_{row['preference']}"]) for row in pair_rows}
        if len(chosen) > 1:
            excluded.extend({"task_id": row["task_id"], "reason": "duplicate_conflicting_preferences"} for row in pair_rows)
            continue
        selected.append(pair_rows[0])
        excluded.extend({"task_id": row["task_id"], "reason": "duplicate_pair"} for row in pair_rows[1:])
    if len(selected) < min_pairs:
        raise ValueError(f"Export blocked: {len(selected)} eligible unique pairs; require {min_pairs}.")
    group_ids = sorted({grouped[r["task_id"]] for r in selected}, key=lambda g: sha([seed, g]))
    if len(group_ids) < 3:
        raise ValueError("Export blocked: at least three independent groups are needed for train/dev/test.")
    train_n = min(len(group_ids)-2, max(1, math.floor(len(group_ids)*.7)))
    dev_n = min(len(group_ids)-train_n-1, max(1, math.floor(len(group_ids)*.15)))
    splits = {group: "train" if i < train_n else "dev" if i < train_n+dev_n else "test" for i, group in enumerate(group_ids)}
    records, lineage = {"train": [], "dev": [], "test": []}, []
    for row in selected:
        task, preference = row["task"], row["preference"]
        split = splits[grouped[task["id"]]]
        example = {"prompt": task["prompt"], "chosen": task[f"response_{preference}"],
                   "rejected": task[f"response_{1-int(preference)}"]}
        records[split].append(example)
        task_votes = [v for v in snapshot["votes"] if v["task_id"] == task["id"]]
        task_decisions = [d for d in snapshot["decisions"] if d["task_id"] == task["id"]]
        lineage.append({"task_id": task["id"], "split": split, "row": len(records[split])-1,
                        "group_sha256": grouped[task["id"]], "task_sha256": sha(task),
                        "example_sha256": sha(example), "pair_sha256": row["pair_sha256"],
                        "prompt_sha256": sha(normalize(task["prompt"])), "status": row["status"],
                        "source": task["source"], "permission": task["permission"], "rubric": RUBRIC_VERSION,
                        "votes_sha256": sha(task_votes), "adjudication_sha256": sha(task_decisions)})
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output/version
    if target.exists():
        raise ValueError("Release version already exists. Choose a new version; existing exports are not overwritten.")
    stage = Path(tempfile.mkdtemp(prefix=f".{version}-", dir=output))
    try:
        for split, rows in records.items():
            (stage/f"{split}.jsonl").write_text("".join(canonical(r)+"\n" for r in rows), encoding="utf-8")
        (stage/"lineage.jsonl").write_text("".join(canonical(r)+"\n" for r in lineage), encoding="utf-8")
        release_quality = {**summary, "exported_pairs": len(selected), "excluded": excluded}
        (stage/"quality.json").write_text(json.dumps(release_quality, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        files = {p.name: file_sha(p) for p in sorted(stage.iterdir())}
        manifest = {"schema": 1, "version": version, "rubric": RUBRIC_VERSION, "seed": seed,
                    "format": "standard explicit-prompt preference JSONL",
                    "snapshot_sha256": sha(snapshot), **head,
                    "implementation_sha256": sha({name: Path(__file__).with_name(name).read_text(encoding="utf-8")
                                                   for name in ["store.py", "release.py", "pipeline.py"]}),
                    "files": files, "counts": {s: len(rows) for s, rows in records.items()},
                    "groups": len(group_ids), "excluded": len(excluded),
                    "policy": {"min_pairs": min_pairs, "min_usable_reviews": 2,
                               "disagreements": "require adjudication", "tie": "exclude",
                               "dedup": "normalized prompt + unordered normalized response pair",
                               "grouping": "provided group OR identical normalized prompt, transitive union"}}
        (stage/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        verify_release(stage)
        os.rename(stage, target)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return target, manifest


def verify_release(path):
    path = Path(path)
    manifest = json.loads((path/"manifest.json").read_text(encoding="utf-8"))
    expected = {"train.jsonl", "dev.jsonl", "test.jsonl", "lineage.jsonl", "quality.json"}
    if set(manifest["files"]) != expected:
        raise ValueError("Manifest file list is invalid.")
    for name, digest in manifest["files"].items():
        if file_sha(path/name) != digest:
            raise ValueError(f"Release checksum mismatch: {name}")
    rows = {}
    seen_pairs = set()
    split_prompts = {}
    for split in ["train", "dev", "test"]:
        current = [json.loads(line) for line in (path/f"{split}.jsonl").read_text(encoding="utf-8").splitlines()]
        if not current or len(current) != manifest["counts"][split]:
            raise ValueError("Split count mismatch or empty split.")
        for i, example in enumerate(current):
            if set(example) != {"prompt", "chosen", "rejected"} or any(not isinstance(v, str) or not v.strip() for v in example.values()):
                raise ValueError("Invalid preference schema.")
            if normalize(example["chosen"]) == normalize(example["rejected"]):
                raise ValueError("Chosen and rejected are identical.")
            pair = sha([normalize(example["prompt"]), sorted([normalize(example["chosen"]), normalize(example["rejected"])])])
            if pair in seen_pairs:
                raise ValueError("Duplicate pair in release.")
            seen_pairs.add(pair)
            prompt = sha(normalize(example["prompt"]))
            if prompt in split_prompts and split_prompts[prompt] != split:
                raise ValueError("Prompt leakage between splits.")
            split_prompts[prompt] = split
            rows[(split, i)] = example
    lineage = [json.loads(line) for line in (path/"lineage.jsonl").read_text(encoding="utf-8").splitlines()]
    groups, seen, task_ids = {}, set(), set()
    for item in lineage:
        key = (item["split"], item["row"])
        if key not in rows or key in seen or sha(rows[key]) != item["example_sha256"]:
            raise ValueError("Invalid or duplicate row lineage.")
        example = rows[key]
        pair = sha([normalize(example["prompt"]), sorted([normalize(example["chosen"]), normalize(example["rejected"])])])
        if (item["task_id"] in task_ids or item["prompt_sha256"] != sha(normalize(example["prompt"]))
                or item["pair_sha256"] != pair or item["rubric"] != manifest["rubric"]):
            raise ValueError("Invalid lineage content hashes, rubric or task ID.")
        task_ids.add(item["task_id"])
        if item["status"] not in {"ready_consensus", "ready_adjudicated"} or item["permission"] not in {"synthetic", "original", "permitted"}:
            raise ValueError("Ineligible row in lineage.")
        seen.add(key)
        group = item["group_sha256"]
        if group in groups and groups[group] != item["split"]:
            raise ValueError("Group leakage between splits.")
        groups[group] = item["split"]
    if seen != set(rows):
        raise ValueError("Missing lineage rows.")
    release_quality = json.loads((path/"quality.json").read_text(encoding="utf-8"))
    if (len(groups) != manifest["groups"] or release_quality["exported_pairs"] != len(rows)
            or len(release_quality["excluded"]) != manifest["excluded"]):
        raise ValueError("Manifest/quality counts are inconsistent.")
    return {"decision": "verified", "pairs": len(rows), "groups": len(groups), "counts": manifest["counts"],
            "limits": "Checks integrity and internal consistency, not annotation truth, real permission, or malicious rewrites."}
