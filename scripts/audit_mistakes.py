#!/usr/bin/env python3
"""Audit mistake-notebook derivation against a real YanShen user database.

The source database is opened read-only and copied with SQLite's backup API to
an isolated temporary database before migrations or mistake derivation run.
No user record is modified.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gongkao.db import connect, init_db  # noqa: E402
from gongkao.mistakes import _annotation_is_typo_only, load_mistake_items, mistake_summary  # noqa: E402


def _normalize(value: object) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _snapshot_database(source: Path, target: Path) -> None:
    uri = f"file:{source.resolve()}?mode=ro"
    read_conn = sqlite3.connect(uri, uri=True, timeout=60)
    write_conn = sqlite3.connect(target, timeout=60)
    try:
        read_conn.backup(write_conn)
    finally:
        write_conn.close()
        read_conn.close()


def _material_sources(conn, question_id: int) -> dict[str, str]:
    question = conn.execute(
        "SELECT paper_id, materials FROM questions WHERE id = ?",
        (question_id,),
    ).fetchone()
    if not question:
        return {}
    if question["paper_id"]:
        return {
            str(row["material_number"]): str(row["content"] or "")
            for row in conn.execute(
                "SELECT material_number, content FROM paper_materials WHERE paper_id = ? ORDER BY material_number",
                (question["paper_id"],),
            )
        }
    return {"": str(question["materials"] or "")}


def _latest_structured_results(conn):
    return conn.execute(
        """
        SELECT gr.id AS report_id, gr.attempt_id, grc.result_json
          FROM grading_reports gr
          JOIN grading_report_contexts grc ON grc.report_id = gr.id
         WHERE gr.status = 'ok'
           AND grc.result_json <> '{}'
           AND NOT EXISTS (
               SELECT 1
                 FROM grading_reports newer
                 JOIN grading_report_contexts newer_context ON newer_context.report_id = newer.id
                WHERE newer.attempt_id = gr.attempt_id
                  AND newer.status = 'ok'
                  AND newer_context.result_json <> '{}'
                  AND newer.id > gr.id
           )
      ORDER BY gr.id
        """
    ).fetchall()


def audit(db_path: Path) -> tuple[dict, list[str]]:
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="yanshen-mistake-audit-") as directory:
        snapshot = Path(directory) / "snapshot.sqlite3"
        _snapshot_database(db_path, snapshot)
        init_db(snapshot)
        with connect(snapshot) as conn:
            inventory = conn.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM attempts) AS attempts,
                  (SELECT COUNT(*) FROM grading_reports) AS reports,
                  (SELECT COUNT(*) FROM grading_report_contexts) AS contexts,
                  (SELECT COUNT(DISTINCT question_id) FROM attempts) AS questions
                """
            ).fetchone()
            started = time.perf_counter()
            items = load_mistake_items(conn)
            elapsed = time.perf_counter() - started

            issue_keys = [item["issue_key"] for item in items]
            if len(issue_keys) != len(set(issue_keys)):
                errors.append("存在重复 issue_key")

            reports_by_attempt: dict[int, set[int]] = defaultdict(set)
            material_fragment_count = 0
            answer_fragment_count = 0
            legacy_count = 0
            structured_count = 0
            questions_with_sources: dict[int, dict[str, str]] = {}

            for item in items:
                reports_by_attempt[int(item["attempt_id"])].add(int(item["report_id"]))
                if str(item.get("point_key") or "").startswith("legacy-"):
                    legacy_count += 1
                else:
                    structured_count += 1

                material_evidence = item.get("material_evidence") or []
                if not material_evidence:
                    errors.append(f"{item['issue_key']} 没有真实材料节选")
                    continue
                if len(material_evidence) > 3:
                    errors.append(f"{item['issue_key']} 材料节选超过3段")

                sources = questions_with_sources.setdefault(
                    int(item["question_id"]),
                    _material_sources(conn, int(item["question_id"])),
                )
                for evidence in material_evidence:
                    material_fragment_count += 1
                    number = str(evidence.get("material_number") or "")
                    source = sources.get(number)
                    if source is None:
                        errors.append(
                            f"{item['issue_key']} 标记为材料{number or '?'}，但该材料不存在"
                        )
                        continue
                    quote = str(evidence.get("quote") or "").strip().strip("…")
                    if not quote or _normalize(quote) not in _normalize(source):
                        errors.append(
                            f"{item['issue_key']} 的材料节选无法在材料{number or '?'}原文中回查"
                        )
                    if len(str(evidence.get("quote") or "")) > 220:
                        errors.append(f"{item['issue_key']} 的材料节选超过220字符")
                    if len(source.strip()) > 260 and _normalize(quote) == _normalize(source):
                        errors.append(f"{item['issue_key']} 错把整则材料当作节选")

                answer_snapshot = str(item.get("answer_snapshot") or "")
                for excerpt in item.get("answer_excerpts") or []:
                    answer_fragment_count += 1
                    if str(excerpt) not in answer_snapshot:
                        errors.append(
                            f"{item['issue_key']} 的“我的原句”无法在作答快照中回查"
                        )
                if not str(item.get("revised_answer") or "").strip():
                    errors.append(f"{item['issue_key']} 缺少修改版答案")

            for attempt_id, report_ids in reports_by_attempt.items():
                if len(report_ids) > 1:
                    errors.append(
                        f"作答 {attempt_id} 同时使用了多份批改报告：{sorted(report_ids)}"
                    )

            item_keys = {
                (int(item["report_id"]), str(item.get("point_key") or ""))
                for item in items
            }
            critical_total = 0
            critical_typo_excluded = 0
            critical_missing = 0
            for row in _latest_structured_results(conn):
                result = json.loads(row["result_json"] or "{}")
                for annotation in result.get("annotations") or []:
                    if not isinstance(annotation, dict):
                        continue
                    if annotation.get("severity") != "critical" and annotation.get("kind") != "critical":
                        continue
                    critical_total += 1
                    if _annotation_is_typo_only(annotation):
                        critical_typo_excluded += 1
                        continue
                    point_key = str(annotation.get("point_key") or "")
                    if not point_key:
                        continue
                    if (int(row["report_id"]), point_key) not in item_keys:
                        critical_missing += 1
                        errors.append(
                            f"报告 {row['report_id']} 的 critical 批注 {point_key} 未进入错题本"
                        )

            graded_attempt_rows = conn.execute(
                """
                SELECT a.id, q.question_type, q.title, COUNT(gr.id) AS reports
                  FROM attempts a
                  JOIN questions q ON q.id = a.question_id
                  LEFT JOIN grading_reports gr ON gr.attempt_id = a.id AND gr.status = 'ok'
              GROUP BY a.id
                HAVING COUNT(gr.id) > 0
                """
            ).fetchall()
            attempts_with_items = {int(item["attempt_id"]) for item in items}
            graded_without_items = [
                int(row["id"])
                for row in graded_attempt_rows
                if int(row["id"]) not in attempts_with_items
            ]

            summary = {
                "source": str(db_path),
                "attempts": int(inventory["attempts"]),
                "reports": int(inventory["reports"]),
                "structured_contexts": int(inventory["contexts"]),
                "questions_done": int(inventory["questions"]),
                "mistakes": mistake_summary(items),
                "structured_items": structured_count,
                "legacy_items": legacy_count,
                "attempts_with_items": len(attempts_with_items),
                "graded_attempts_without_items": graded_without_items,
                "material_fragments_verified": material_fragment_count,
                "answer_fragments_verified": answer_fragment_count,
                "critical_annotations": critical_total,
                "critical_typo_excluded": critical_typo_excluded,
                "critical_annotations_missing": critical_missing,
                "severity": dict(Counter(item.get("severity") for item in items)),
                "tags": dict(Counter(tag for item in items for tag in item.get("tags") or [])),
                "derive_seconds": round(elapsed, 4),
            }
            return summary, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit YanShen mistake notebook against a real user DB")
    parser.add_argument("db", type=Path, help="path to gongkao.sqlite3")
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error(f"database not found: {args.db}")
    summary, errors = audit(args.db)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if errors:
        print("\nAUDIT FAILED", file=sys.stderr)
        for error in errors[:50]:
            print(f"- {error}", file=sys.stderr)
        if len(errors) > 50:
            print(f"- ...另有 {len(errors) - 50} 项", file=sys.stderr)
        return 1
    print("\nAUDIT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
