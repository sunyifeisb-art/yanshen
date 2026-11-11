import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen

from gongkao.db import connect, init_db
from gongkao.normative_expressions import (
    add_custom_normative_expression,
    export_normative_expressions_markdown,
    infer_category,
    ingest_report_normative_expressions,
    list_normative_expressions,
    normative_expression_summary,
    top_normative_verbs,
    update_normative_expression,
)
from gongkao.services.personal_records import export_personal_data, import_personal_data
from gongkao.web.application import create_server


def seed_normative_report(conn):
    paper_id = conn.execute(
        """
        INSERT INTO papers (paper_code, paper_name, exam_type, year, region)
        VALUES ('P-NORM', '规范词测试卷', '国考', 2026, '全国')
        """
    ).lastrowid
    question_id = conn.execute(
        """
        INSERT INTO questions (
            question_code, paper_id, paper_name, exam_type, year, region,
            question_type, title, prompt, materials, requirements
        ) VALUES (
            'Q-NORM', ?, '规范词测试卷', '国考', 2026, '全国',
            '提出对策', '道路治理', '请提出对策', '材料内容', '准确、具体'
        )
        """,
        (paper_id,),
    ).lastrowid
    attempt_id = conn.execute(
        "INSERT INTO attempts (question_id, answer_text, word_count) VALUES (?, ?, ?)",
        (question_id, '各部门加强协调，政府派专人负责。', 16),
    ).lastrowid
    report_id = conn.execute(
        """
        INSERT INTO grading_reports (attempt_id, provider, model, report_text, status)
        VALUES (?, 'test', 'test', '# 批改报告', 'ok')
        """,
        (attempt_id,),
    ).lastrowid
    rubric = {
        "points": [
            {
                "point_key": "point-collaboration",
                "label": "部门协同",
                "canonical_expression": "建立部门联动机制",
                "minimum_expression": "明确责任分工，联动解决",
                "material_evidence": [
                    {"material_number": 1, "quote": "涉及多个部门，缺乏沟通配合机制"}
                ],
            }
        ]
    }
    result = {
        "point_matches": [
            {"point_key": "point-collaboration", "status": "partial", "coverage_ratio": 0.5}
        ],
        "annotations": [
            {
                "kind": "change",
                "quote": "各部门加强协调",
                "replacement": "建立部门联动机制",
                "reason": "表述需要具体化",
            }
        ],
    }
    conn.execute(
        """
        INSERT INTO grading_report_contexts (
            report_id, pipeline_version, rubric_snapshot_json, result_json
        ) VALUES (?, 'test-v1', ?, ?)
        """,
        (report_id, json.dumps(rubric, ensure_ascii=False), json.dumps(result, ensure_ascii=False)),
    )
    return report_id


class NormativeExpressionsTests(unittest.TestCase):
    def test_structured_report_builds_idempotent_traceable_library(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "normative.sqlite3"
            init_db(db_path)
            with connect(db_path) as conn:
                report_id = seed_normative_report(conn)
                first = ingest_report_normative_expressions(conn, report_id)
                second = ingest_report_normative_expressions(conn, report_id)
                entries = list_normative_expressions(conn, {})
                summary = normative_expression_summary(conn)
                needs_work = list_normative_expressions(conn, {"coverage": "needs_work"})
                partial = list_normative_expressions(conn, {"coverage": "partial"})
                hit = list_normative_expressions(conn, {"coverage": "hit"})

            self.assertGreaterEqual(first, 2)
            self.assertEqual(second, 0)
            self.assertEqual(len(entries), 2)
            primary = next(item for item in entries if item["expression"] == "建立部门联动机制")
            self.assertEqual(primary["category"], "责任主体与协同机制")
            self.assertIn("各部门加强协调", primary["wrong_expressions"])
            self.assertIn("涉及多个部门，缺乏沟通配合机制", primary["trigger_signals"][0])
            self.assertEqual(primary["partial_count"], 1)
            self.assertEqual(summary["reports"], 1)
            self.assertEqual(summary["questions"], 1)
            self.assertEqual(len(needs_work), 2)
            self.assertEqual(len(partial), 2)
            self.assertEqual(hit, [])

    def test_reference_corpus_dimensions_and_advanced_verbs_are_supported(self):
        self.assertEqual(infer_category("简化抵押贷款审批程序"), "资金金融与产权")
        self.assertEqual(infer_category("预留临时通道，规范占道施工"), "施工与城市管理")
        self.assertEqual(infer_category("搭建再就业信息平台，匹配适老岗位"), "劳动与社会保障")
        entries = [
            {"expression": "盘活存量资产"},
            {"expression": "打通部门数据壁垒"},
            {"expression": "倒逼商户规范经营"},
        ]
        self.assertEqual(dict(top_normative_verbs(entries, 10)), {"盘活": 1, "打通": 1, "倒逼": 1})

    def test_markdown_export_and_page_render_include_core_features(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "normative-web.sqlite3"
            init_db(db_path)
            with connect(db_path) as conn:
                report_id = seed_normative_report(conn)
                ingest_report_normative_expressions(conn, report_id)
                markdown = export_normative_expressions_markdown(conn)
            self.assertIn("申论规范表达 · 个人整理库", markdown)
            self.assertIn("建立部门联动机制", markdown)
            self.assertIn("材料触发信号", markdown)
            self.assertIn("各部门加强协调", markdown)

            server = create_server(port=0, db_path=db_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"
                with urlopen(f"{base}/normative-expressions", timeout=10) as response:
                    html = response.read().decode("utf-8")
                self.assertIn("规范词整理", html)
                self.assertIn("建立部门联动机制", html)
                self.assertIn("不规范 → 规范", html)
                self.assertIn("来源追溯", html)
                self.assertIn("高频动词", html)
                self.assertIn("待补漏（遗漏/部分）", html)
                self.assertIn("自动整理已开启", html)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_server_startup_automatically_syncs_historical_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "normative-auto-sync.sqlite3"
            init_db(db_path)
            with connect(db_path) as conn:
                seed_normative_report(conn)
                self.assertEqual(normative_expression_summary(conn)["total"], 0)

            server = create_server(port=0, db_path=db_path)
            try:
                with connect(db_path) as conn:
                    summary = normative_expression_summary(conn)
                    entries = list_normative_expressions(conn, {})
                self.assertGreaterEqual(summary["total"], 2)
                self.assertTrue(any(item["expression"] == "建立部门联动机制" for item in entries))
            finally:
                server.server_close()

    def test_personal_backup_preserves_custom_entries_and_learning_state(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.sqlite3"
            target_path = Path(directory) / "target.sqlite3"
            init_db(source_path)
            init_db(target_path)
            with connect(source_path) as conn:
                entry_id = add_custom_normative_expression(
                    conn,
                    "畅通群众反馈渠道",
                    "监督、检查与群众参与",
                    "看到群众反映困难时优先想到反馈闭环。",
                )
                update_normative_expression(conn, entry_id, action="star")
                update_normative_expression(conn, entry_id, action="status", value="mastered")
                payload = export_personal_data(conn)
            with connect(target_path) as conn:
                counts = import_personal_data(conn, payload)
                entries = list_normative_expressions(conn, {})

            self.assertEqual(counts["normative_expressions"], 1)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["expression"], "畅通群众反馈渠道")
            self.assertEqual(entries[0]["status"], "mastered")
            self.assertEqual(entries[0]["is_starred"], 1)
            self.assertIn("反馈闭环", entries[0]["note"])


if __name__ == "__main__":
    unittest.main()
