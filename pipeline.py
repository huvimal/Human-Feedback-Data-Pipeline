"""Local human preference annotation and dataset release CLI."""
import argparse
import html
import json
import sqlite3
import sys
from pathlib import Path

from release import export_release, quality, verify_release
from store import Store, audit_chain

ROOT = Path(__file__).resolve().parent


def display(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def save_quality(snapshot):
    report = quality(snapshot)
    folder = ROOT/"reports"
    folder.mkdir(exist_ok=True)
    (folder/"quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    esc = lambda value: html.escape(str(value))
    rows = "".join(f"<tr><td>{esc(r['task_id'])}</td><td>{esc(r['status'])}</td><td>{r['usable_votes']}</td>"
                   f"<td>{esc(', '.join(r['reasons']))}</td></tr>" for r in report["rows"])
    page = f'''<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
    <title>Human Feedback Data Pipeline</title><style>body{{font:16px system-ui;margin:32px;color:#183345;line-height:1.6}}
    table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;border:1px solid #c4d0da;text-align:left}}
    pre{{white-space:pre-wrap;background:#edf3f7;padding:16px}}</style>
    <h1>Human Feedback Data Pipeline</h1><pre>{esc(json.dumps(report['counts'], ensure_ascii=False, indent=2))}</pre>
    <table><tr><th>Task</th><th>Status</th><th>Usable reviews</th><th>Quarantine reasons</th></tr>{rows}</table>
    <h2>Reviewer agreement</h2><pre>{esc(json.dumps(report['agreement'], ensure_ascii=False, indent=2))}</pre>
    <p>Local CLI identities only. Scripted demo labels are not independent human annotations.</p></html>'''
    (folder/"quality.html").write_text(page, encoding="utf-8")
    return report


def demo(store):
    tasks = json.loads((ROOT/"data"/"tasks.json").read_text(encoding="utf-8"))
    inserted = store.import_tasks(tasks)
    for name, role in [("mai", "reviewer"), ("nam", "reviewer"), ("lead", "adjudicator")]:
        store.add_reviewer(name, role)
    for task in tasks:
        tid = task["id"]
        if tid in {"t12", "t13"}:
            continue
        for reviewer in ["mai", "nam"]:
            if tid == "t11" and reviewer == "nam":
                continue
            if store.db.execute("SELECT 1 FROM votes WHERE task_id=? AND reviewer=?", (tid, reviewer)).fetchone():
                continue
            view = store.assign(tid, reviewer)
            if tid == "t10":
                choice = "tie"
            else:
                preferred = "1" if tid == "t15" or (tid == "t09" and reviewer == "nam") else "0"
                choice = "A" if view["A"] == task[f"response_{preferred}"] else "B"
            store.vote(tid, reviewer, choice, "Scripted fixture vote; not an independent human review.", 2)
    report = save_quality(store.snapshot())
    display({"new_tasks": inserted, "tasks": report["tasks"], "counts": report["counts"]})
    print("Scripted demo only. t09 needs adjudication; t10 is a tie; t11 needs another review.")
    print(f"Report: {ROOT/'reports'/'quality.html'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT/"private"/"feedback.db")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo")
    sub.add_parser("quality")
    sub.add_parser("audit")
    p = sub.add_parser("import")
    p.add_argument("--file", type=Path, required=True)
    p = sub.add_parser("register")
    p.add_argument("--name", required=True)
    p.add_argument("--role", choices=["reviewer", "adjudicator"], default="reviewer")
    for command in ["assign", "view", "vote", "label"]:
        p = sub.add_parser(command)
        p.add_argument("--task", required=True)
        p.add_argument("--reviewer", required=True)
        if command == "vote":
            p.add_argument("--choice", choices=["A", "B", "tie", "skip"], required=True)
            p.add_argument("--rationale", required=True)
            p.add_argument("--confidence", type=int, choices=[1, 2, 3], default=2)
    p = sub.add_parser("adjudicate")
    p.add_argument("--task", required=True)
    p.add_argument("--adjudicator", required=True)
    p.add_argument("--preference", choices=["0", "1", "tie"], required=True)
    p.add_argument("--rationale", required=True)
    p = sub.add_parser("inspect-dispute")
    p.add_argument("--task", required=True)
    p.add_argument("--adjudicator", required=True)
    p = sub.add_parser("export")
    p.add_argument("--version", required=True)
    p.add_argument("--output", type=Path, default=ROOT/"exports")
    p.add_argument("--min-pairs", type=int, default=3)
    p = sub.add_parser("verify")
    p.add_argument("--release", type=Path, required=True)
    args = parser.parse_args()
    store = None
    try:
        if args.command == "verify":
            display(verify_release(args.release))
            return 0
        store = Store(args.db)
        if args.command == "demo":
            demo(store)
        elif args.command == "import":
            display({"inserted": store.import_tasks(json.loads(args.file.read_text(encoding="utf-8")))})
        elif args.command == "register":
            store.add_reviewer(args.name, args.role)
            display({"registered": args.name, "role": args.role})
        elif args.command == "assign":
            display(store.assign(args.task, args.reviewer))
        elif args.command == "view":
            display(store.blind_view(args.task, args.reviewer))
        elif args.command == "label":
            display(store.assign(args.task, args.reviewer))
            choice = input("Choice (A/B/tie/skip): ").strip()
            rationale = input("Rationale (at least 10 characters): ").strip()
            store.vote(args.task, args.reviewer, choice, rationale)
            display({"saved": True, "task_id": args.task, "reviewer": args.reviewer})
        elif args.command == "vote":
            store.vote(args.task, args.reviewer, args.choice, args.rationale, args.confidence)
            display({"saved": True, "task_id": args.task, "reviewer": args.reviewer})
        elif args.command == "inspect-dispute":
            store.require_role(args.adjudicator, "adjudicator")
            display({"task": store.task(args.task), "reviews": [dict(r) for r in store.db.execute(
                "SELECT reviewer,preference,rationale,confidence FROM votes WHERE task_id=?", (args.task,))]})
        elif args.command == "adjudicate":
            store.adjudicate(args.task, args.adjudicator, args.preference, args.rationale)
            display({"adjudicated": args.task})
        elif args.command == "quality":
            report = save_quality(store.snapshot())
            display({"tasks": report["tasks"], "counts": report["counts"], "agreement": report["agreement"]})
            print(f"Report: {ROOT/'reports'/'quality.html'}")
        elif args.command == "audit":
            display(audit_chain(store.snapshot()))
        elif args.command == "export":
            target, manifest = export_release(store.snapshot(), args.output, args.version, args.min_pairs)
            display({"release": str(target.resolve()), "counts": manifest["counts"], "groups": manifest["groups"], "excluded": manifest["excluded"]})
        return 0
    except (ValueError, TypeError, KeyError, sqlite3.Error, OSError, EOFError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
