import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from gongkao.db import connect, prepare_user_database
from gongkao.web.application import create_server

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "gongkao_seed.sqlite3"


class ReportImportTest(unittest.TestCase):
    def test_markdown_report_import_adds_switchable_external_report(self):
        with tempfile.TemporaryDirectory() as directory:
            user_db = Path(directory) / "report-import.sqlite3"
            prepare_user_database(user_db, SEED)
            with connect(user_db) as conn:
                question = conn.execute("SELECT * FROM questions ORDER BY id LIMIT 1").fetchone()
                attempt_id = conn.execute(
                    "INSERT INTO attempts (question_id, answer_text, word_count) VALUES (?, '我的原答案', 5)",
                    (question["id"],),
                ).lastrowid
                software_report_id = conn.execute(
                    """
                    INSERT INTO grading_reports (attempt_id, provider, model, report_text, status)
                    VALUES (?, 'ChatGPT', 'gpt-test', '## 总体评分\n- 总分：6/10\n\n## 修改版答案\n\n软件修改稿。', 'ok')
                    """,
                    (attempt_id,),
                ).lastrowid
                question_code = question["question_code"]

            imported_report = f"""<!-- YANSHEN_GRADING_REPORT_V1 -->
<!-- QUESTION_CODE: {question_code} -->
# 研申批改报告
## 评分基准
| 采分点 | 材料依据 | 重要程度 | 建议权重 | 命中关键词 |
| --- | --- | --- | --- | --- |
| 要点 | 材料原文 | critical | 10 | 关键词 |
## 总体评分
- 总分：8/10
## 得分点清单
- 标准采分点：要点
## 遗漏点分析
| 遗漏的采分点 | 材料依据 | 遗漏原因 | 整体建议 |
| --- | --- | --- | --- |
| 补充点 | 材料原文 | 原答案未覆盖 | 补写要点 |
## 材料领读
- 材料 -> 要点 -> 表达
## 优化建议
1. 补足遗漏。
## 修改版答案
导入修改稿。
"""
            boundary = "----yanshen-report-import"
            multipart = (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="report_file"; filename="external-report.md"\r\n'
                "Content-Type: text/markdown; charset=utf-8\r\n\r\n"
                + imported_report
                + f"\r\n--{boundary}--\r\n"
            ).encode("utf-8")

            server = create_server(port=0, db_path=user_db)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                request = Request(
                    f"{base_url}/attempts/{attempt_id}/reports/import",
                    data=multipart,
                    method="POST",
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                )
                with urlopen(request, timeout=10) as response:
                    html = response.read().decode("utf-8")
                with connect(user_db) as conn:
                    imported = conn.execute(
                        "SELECT * FROM grading_reports WHERE attempt_id = ? AND provider = '外部导入' ORDER BY id DESC LIMIT 1",
                        (attempt_id,),
                    ).fetchone()
                self.assertIsNotNone(imported)
                self.assertEqual(imported["model"], "imported-md")
                self.assertNotIn("YANSHEN_GRADING_REPORT_V1", imported["report_text"])
                self.assertIn("导入修改稿", imported["report_text"])
                self.assertIn("导入批改数据", html)
                self.assertIn("软件批改报告", html)
                self.assertIn("导入批改报告", html)
                self.assertIn("data-report-switcher", html)
                self.assertIn(f'id="report-{imported["id"]}" data-report-card>', html)
                self.assertIn(f'id="report-{software_report_id}" data-report-card hidden>', html)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
