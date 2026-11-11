import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from gongkao.ai import chat_completion
from gongkao.db import connect, init_db
from gongkao.grading import build_grading_package, build_grading_package_zip, parse_imported_grading_report
from gongkao.grading_pipeline.essay_outline import hide_legacy_essay_scores
from gongkao.grading_pipeline.essay_review import (
    ESSAY_REPORT_HEADINGS,
    build_essay_review_prompt,
    render_essay_review,
    validate_essay_review,
)
from gongkao.grading_pipeline.orchestration import create_grading_job, run_grading_job
from gongkao.statistics import build_module_score_statistics
from gongkao.web.application import create_server


class EssayOutlineReviewTest(unittest.TestCase):
    def test_essay_job_reads_all_sources_and_persists_review_without_score(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "essay.sqlite3"
            init_db(db_path)
            with connect(db_path) as conn:
                paper_id = conn.execute(
                    "INSERT INTO papers (paper_code, paper_name, exam_type, year, region) VALUES ('P-ESSAY', '测试卷', '国考', 2026, '全国')"
                ).lastrowid
                question_id = conn.execute(
                    """INSERT INTO questions
                    (question_code, paper_id, paper_name, exam_type, year, region,
                     question_type, title, prompt, materials, requirements, word_limit)
                    VALUES ('Q-ESSAY', ?, '测试卷', '国考', 2026, '全国',
                            '综合写作', '融合与融活', '以融合·融活为话题写文章。', '',
                            '联系实际，自拟题目。', '1000—1200字')""",
                    (paper_id,),
                ).lastrowid
                conn.execute(
                    "INSERT INTO paper_materials (paper_id, material_number, content) VALUES (?, 1, '材料甲讲城乡融合。')",
                    (paper_id,),
                )
                conn.execute(
                    "INSERT INTO paper_materials (paper_id, material_number, content) VALUES (?, 2, '材料乙讲文化融活。')",
                    (paper_id,),
                )
                reference_id = conn.execute(
                    "INSERT INTO reference_answers (question_id, organization, answer_text) VALUES (?, '机构甲', '参考答案强调要素流动与文化活化。')",
                    (question_id,),
                ).lastrowid
                answer = "标题：融通发展\n中心论点：融合促进发展\n分论点一：城乡要素流动"
                attempt_id = conn.execute(
                    "INSERT INTO attempts (question_id, answer_text, word_count) VALUES (?, ?, 35)",
                    (question_id, answer),
                ).lastrowid
                job, _ = create_grading_job(
                    conn, conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone(),
                    conn.execute("SELECT * FROM ai_settings WHERE id = 1").fetchone(),
                    [reference_id], "补充参考强调机制。", {"deep_thinking": False},
                )
                question = conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
                attempt = conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
                refs = conn.execute("SELECT * FROM reference_answers WHERE id = ?", (reference_id,)).fetchall()
                materials = conn.execute("SELECT * FROM paper_materials WHERE paper_id = ?", (paper_id,)).fetchall()

            prompts = []
            review = {
                "research_status": "当前接口未提供联网检索",
                "web_sources": [],
                "task_reading": "要写融合与融活的关系，联系实际。",
                "essay_summary": "有标题、中心论点和一个分论点，未呈现正文与结尾。",
                "overall_judgment": "方向相关，但分论点不足以统领全部材料。",
                "strengths": ["中心论点回应融合。"],
                "thesis_review": "融合促进发展扣题，但尚未写出融活关系。",
                "subpoint_review": [{"quote": "城乡要素流动", "judgment": "尚未覆盖文化融活", "revision": "增设文化活化分论点"}],
                "issues": [{"location": "分论点", "answer_quote": "城乡要素流动",
                            "basis": "材料乙讲文化融活", "problem": "尚未覆盖融活机制",
                            "revision": "增设文化活化分论点"}],
                "paragraph_revisions": [],
                "material_mapping": [{"material": "材料2：文化融活", "use": "支撑文化活化段"}],
                "suggested_outline": ["标题：融通发展", "中心论点：融合与融活相互促进", "分论点一：要素流动", "分论点二：文化活化"],
                "expressions": [{"text": "以融合促融活，以融活增活力", "source": "原创", "use": "可用于结尾，但须接材料论证"}],
                "next_steps": ["补出第二个分论点并写明与中心论点的关系"],
            }

            def fake_chat(settings, prompt, options):
                prompts.append(prompt)
                value = json.dumps(review, ensure_ascii=False)
                return value, value

            report_id = run_grading_job(db_path, job["id"], fake_chat)
            self.assertIsNotNone(report_id)
            self.assertEqual(len(prompts), 1)
            for source in ("融合·融活", "联系实际", "材料甲讲城乡融合", "材料乙讲文化融活",
                           "城乡要素流动"):
                self.assertIn(source, prompts[0])
            self.assertNotIn("参考答案强调要素流动", prompts[0])
            self.assertNotIn("补充参考强调机制", prompts[0])
            with connect(db_path) as conn:
                row = conn.execute(
                    """SELECT gr.report_text, c.result_json FROM grading_reports gr
                       JOIN grading_report_contexts c ON c.report_id = gr.id WHERE gr.id = ?""",
                    (report_id,),
                ).fetchone()
                self.assertEqual(json.loads(row["result_json"])["score_status"], "not_scored")
                self.assertNotIn("score", json.loads(row["result_json"]))
                self.assertIn("## 材料运用与建议架构", row["report_text"])
                self.assertIn("## 主题表达积累", row["report_text"])
                self.assertNotIn("总分", row["report_text"])
                self.assertEqual(build_module_score_statistics(conn)["scored_attempts"], 0)
                conn.execute(
                    """INSERT INTO grading_reports (attempt_id, provider, model, report_text, status)
                       VALUES (?, '旧版', 'legacy', '## 总体评分\n- 总分：25/35\n## 结构与顺序审查\n- 分论点需调整。', 'ok')""",
                    (attempt_id,),
                )
                self.assertEqual(build_module_score_statistics(conn)["scored_attempts"], 0)

            with connect(db_path) as conn:
                conn.execute("UPDATE ai_settings SET mode = 'chatgpt' WHERE id = 1")
                online_job, _ = create_grading_job(
                    conn, conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone(),
                    conn.execute("SELECT * FROM ai_settings WHERE id = 1").fetchone(),
                    [], "", {"deep_thinking": False},
                )
            online_review = dict(review)
            online_review["research_status"] = "已联网检索并核对同题范文"
            online_review["web_sources"] = [{"title": "同题范文", "url": "https://example.com/essay", "relevance": "同题同卷；可比较论证顺序"}]
            with patch("gongkao.ai.codex_research_completion", return_value=(
                json.dumps(online_review, ensure_ascii=False), json.dumps(online_review, ensure_ascii=False),
            )) as research:
                self.assertIsNotNone(run_grading_job(db_path, online_job["id"], chat_completion))
                self.assertIn("必须检索题目原句", research.call_args.args[1])

            server = create_server(port=0, db_path=db_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_address[1]}/attempts/{attempt_id}", timeout=10) as response:
                    html = response.read().decode("utf-8")
                self.assertIn("大作文审阅报告", html)
                self.assertIn("重新审阅作文", html)
                self.assertNotIn("25/35", html)
                self.assertNotIn("grading-score-overview", html)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

            package = build_grading_package(question, refs, attempt, materials, "补充参考强调机制。")
            self.assertIn("不评分", package)
            self.assertNotIn("## 总体评分", package)
            self.assertIn("自行联网搜索", package)
            self.assertNotIn("参考答案强调要素流动", package)
            self.assertNotIn("补充参考强调机制", package)
            with zipfile.ZipFile(io.BytesIO(build_grading_package_zip(package))) as archive:
                self.assertIn("大作文深度审阅.md", archive.namelist())
                self.assertIn("03-我的作答.md", archive.namelist())
                self.assertNotIn("03-参考答案.md", archive.namelist())
            imported = "\n".join([
                "<!-- YANSHEN_GRADING_REPORT_V1 -->", "<!-- QUESTION_CODE: Q-ESSAY -->",
                "# 研申批改报告", *(f"## {heading}\n内容" for heading in ESSAY_REPORT_HEADINGS),
            ])
            self.assertEqual(parse_imported_grading_report(imported, "Q-ESSAY", "综合写作")[1], [])

    def test_unconnected_api_cannot_claim_web_sources_or_invent_quotes(self):
        review = {
            "research_status": "已联网检索", "web_sources": [{"title": "例文", "url": "https://example.com", "relevance": "同题"}],
            "task_reading": "题意", "essay_summary": "文章", "overall_judgment": "需改", "strengths": [],
            "thesis_review": "论点", "subpoint_review": [], "issues": [], "paragraph_revisions": [],
            "material_mapping": [], "suggested_outline": ["总论点：..."], "expressions": [], "next_steps": [],
        }
        with self.assertRaisesRegex(ValueError, "未联网"):
            validate_essay_review(review, "原文", can_search=False)
        review["research_status"] = "当前接口未提供联网检索"
        review["web_sources"] = []
        review["paragraph_revisions"] = [{"answer_quote": "虚构原文", "problem": "问题", "revision": "改句", "reason": "原因"}]
        with self.assertRaisesRegex(ValueError, "不存在"):
            validate_essay_review(review, "原文", can_search=False)

    def test_full_essay_review_includes_line_edit_and_verified_source(self):
        answer = "融合带来发展。文化需要融活。"
        prompt = build_essay_review_prompt(
            {"prompt": "以融合为题写文章", "year": 2026, "region": "浙江", "paper_name": "A卷"},
            [{"material_number": 1, "content": "城乡资源互通。"}], answer, can_search=True,
        )
        self.assertIn("小红书", prompt)
        self.assertIn("卷别", prompt)
        review = {
            "research_status": "已联网检索，核实一篇同题范文",
            "web_sources": [{"title": "同题范文", "url": "https://example.com/essay", "relevance": "同题；结构可参考"}],
            "task_reading": "讨论融合", "essay_summary": "两句正文", "overall_judgment": "论证不足",
            "strengths": ["扣题"], "thesis_review": "融合带来发展扣题",
            "subpoint_review": [{"quote": "文化需要融活", "judgment": "需要与总论点相连", "revision": "解释文化融活如何促进发展"}],
            "issues": [],
            "paragraph_revisions": [{"answer_quote": "融合带来发展", "problem": "因果未展开",
                                     "revision": "融合让城乡资源互通，进而拓展发展空间", "reason": "补足机制"}],
            "material_mapping": [{"material": "材料1：城乡资源互通", "use": "支撑融合机制"}],
            "suggested_outline": ["总论点：融合推动发展"],
            "expressions": [{"text": "融通资源，激活发展", "source": "原创", "use": "接在机制论证之后"}],
            "next_steps": ["展开融合到发展的因果链"],
        }
        result = render_essay_review(validate_essay_review(review, answer, can_search=True))
        self.assertIn("https://example.com/essay", result)
        self.assertIn("融合让城乡资源互通", result)
        self.assertIn("## 主题表达积累", result)

    def test_old_essay_report_hides_score_sections(self):
        original = "## 总体评分\n- 总分：25/35\n## 结构与顺序审查\n- 结构分依据：15/20\n- 分论点需调整。\n## 修改版答案\n整篇范文"
        visible = hide_legacy_essay_scores(original)
        self.assertIn("分论点需调整", visible)
        self.assertNotIn("25/35", visible)
        self.assertNotIn("整篇范文", visible)


if __name__ == "__main__":
    unittest.main()
