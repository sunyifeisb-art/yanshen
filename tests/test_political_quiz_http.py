import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from gongkao.db import connect, init_db
from gongkao.political_quiz import (
    complete_quiz_attempt,
    create_quiz_attempt,
    get_quiz_attempt,
    get_quiz_pack,
    save_quiz_pack,
)
from gongkao.web.application import create_server
from tests.test_political_quiz import sample_pack


class PoliticalQuizHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "quiz-http.sqlite3"
        init_db(self.db_path)
        with connect(self.db_path) as conn:
            self.pack_id = save_quiz_pack(conn, sample_pack(), verification_status="verified")
        self.server = create_server(port=0, db_path=self.db_path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def test_home_and_pack_pages_render_native_quiz_ui(self):
        with urlopen(f"{self.base}/political-quiz", timeout=10) as response:
            html = response.read().decode("utf-8")
        self.assertIn("时政测验", html)
        self.assertIn("开始本期核验题", html)
        self.assertIn("生成最新测验", html)
        self.assertIn("练习模式", html)
        self.assertIn("考试模式", html)
        self.assertIn('data-political-period-preset', html)
        self.assertIn('type="month" name="period_start"', html)
        self.assertIn('type="month" name="period_end"', html)
        self.assertIn("今年上半年", html)
        self.assertIn("自定义", html)
        self.assertIn('data-political-quiz-page="home"', html)

        with urlopen(f"{self.base}/political-quiz/packs/{self.pack_id}?mode=practice", timeout=10) as response:
            pack_html = response.read().decode("utf-8")
        self.assertIn('data-political-quiz-page="pack"', pack_html)
        self.assertIn('data-quiz-mode="practice"', pack_html)
        self.assertIn('data-quiz-pack-json', pack_html)
        self.assertIn("组合题题干", pack_html)
        self.assertIn("逐条解析", pack_html)

    def test_attempt_save_and_submit_returns_analysis(self):
        with connect(self.db_path) as conn:
            attempt_id = create_quiz_attempt(conn, self.pack_id, "exam")
        payload = json.dumps({"answers": {"0": 1, "1": 0}, "elapsed_seconds": 45}).encode("utf-8")
        request = Request(
            f"{self.base}/political-quiz/attempts/{attempt_id}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
        self.assertTrue(result["ok"])

        submit_payload = json.dumps({"answers": {"0": 1, "1": 0, "2": 2, "3": 3, "4": 2}, "elapsed_seconds": 90}).encode("utf-8")
        submit_request = Request(
            f"{self.base}/political-quiz/attempts/{attempt_id}/submit",
            data=submit_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(submit_request, timeout=10) as response:
            submitted = json.loads(response.read().decode("utf-8"))
        self.assertTrue(submitted["ok"])
        self.assertEqual(submitted["analysis"]["correct_count"], 3)
        self.assertEqual(submitted["analysis"]["question_count"], 5)
        with connect(self.db_path) as conn:
            attempt = get_quiz_attempt(conn, attempt_id)
        self.assertIsNotNone(attempt["completed_at"])
        self.assertEqual(attempt["correct_count"], 3)

    def test_completed_attempt_reopens_as_review_and_cannot_be_overwritten(self):
        with connect(self.db_path) as conn:
            attempt_id = create_quiz_attempt(conn, self.pack_id, "exam")
            complete_quiz_attempt(
                conn,
                attempt_id,
                sample_pack(),
                {"0": 1, "1": 0, "2": 2, "3": 3, "4": 2},
                elapsed_seconds=88,
            )

        with urlopen(
            f"{self.base}/political-quiz/packs/{self.pack_id}?mode=exam&attempt={attempt_id}",
            timeout=10,
        ) as response:
            html = response.read().decode("utf-8")
        self.assertIn('data-attempt-completed="true"', html)
        self.assertIn("REVIEW MODE", html)
        self.assertIn("data-quiz-analysis-json", html)

        overwrite = Request(
            f"{self.base}/political-quiz/attempts/{attempt_id}",
            data=json.dumps({"answers": {"0": 0}, "elapsed_seconds": 1}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(overwrite, timeout=10)
        self.assertEqual(caught.exception.code, 409)
        with connect(self.db_path) as conn:
            attempt = get_quiz_attempt(conn, attempt_id)
        self.assertEqual(json.loads(attempt["answers_json"])["0"], 1)
        self.assertEqual(attempt["elapsed_seconds"], 88)

    def test_home_lists_previous_ready_packs_for_direct_reuse(self):
        other = sample_pack()
        other["meta"]["title"] = "2026年8月政治理论回炉题"
        other["meta"]["period_label"] = "2026年8月"
        with connect(self.db_path) as conn:
            other_id = save_quiz_pack(conn, other, verification_status="generated")

        with urlopen(f"{self.base}/political-quiz", timeout=10) as response:
            html = response.read().decode("utf-8")
        self.assertIn("历史题包", html)
        self.assertIn("2026年8月政治理论回炉题", html)
        self.assertIn(f'/political-quiz/packs/{other_id}?mode=practice', html)
        self.assertIn(f'/political-quiz/packs/{other_id}?mode=exam', html)

    def test_generated_pack_can_be_marked_verified_after_manual_review(self):
        generated = sample_pack()
        generated["meta"]["title"] = "待核验联网题包"
        with connect(self.db_path) as conn:
            pack_id = save_quiz_pack(conn, generated, verification_status="generated")
            attempt_id = create_quiz_attempt(conn, pack_id, "practice")

        with urlopen(
            f"{self.base}/political-quiz/packs/{pack_id}?mode=practice&attempt={attempt_id}",
            timeout=10,
        ) as response:
            html = response.read().decode("utf-8")
        self.assertIn("人工核验闭环", html)
        self.assertIn(f'/political-quiz/packs/{pack_id}/verify', html)
        self.assertIn("我已逐题核对来源，标记已核验", html)

        verify_form = urlencode({"mode": "practice", "attempt": str(attempt_id)}).encode("utf-8")
        request = Request(
            f"{self.base}/political-quiz/packs/{pack_id}/verify",
            data=verify_form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            verified_html = response.read().decode("utf-8")
        self.assertIn("✓ 已核验", verified_html)
        self.assertNotIn("我已逐题核对来源，标记已核验", verified_html)
        with connect(self.db_path) as conn:
            row = get_quiz_pack(conn, pack_id)
        self.assertEqual(row["verification_status"], "verified")
        self.assertEqual(json.loads(row["data_json"])["meta"]["verification_status"], "verified")

    def test_latest_generation_refuses_non_chatgpt_mode_instead_of_faking_freshness(self):
        form = urlencode(
            {
                "exam_scope": "national",
                "province": "",
                "period": "month",
                "question_count": "20",
            }
        ).encode("utf-8")
        request = Request(
            f"{self.base}/political-quiz/generate",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 400)
        body = json.loads(caught.exception.read().decode("utf-8"))
        self.assertFalse(body["ok"])
        self.assertIn("ChatGPT", body["error"])
        self.assertIn("联网", body["error"])


if __name__ == "__main__":
    unittest.main()
