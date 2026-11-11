import unittest

from gongkao.web import dispatch_get, dispatch_post
from gongkao.web.controllers import political_quiz as political_controller
from gongkao.web.runtime import layout


class DummyPoliticalQuizHandler:
    def __init__(self):
        self.calls = []

    def page_political_quiz(self, query):
        self.calls.append(("home", query))

    def page_political_quiz_pack(self, path, query):
        self.calls.append(("pack", path, query))

    def handle_political_quiz_generation_status(self, path):
        self.calls.append(("generation_status", path))

    def handle_political_quiz_generate(self):
        self.calls.append(("generate",))

    def handle_political_quiz_attempt(self, path):
        self.calls.append(("attempt", path))

    def handle_political_quiz_submit(self, path):
        self.calls.append(("submit", path))

    def handle_political_quiz_verify(self, path):
        self.calls.append(("verify", path))


class PoliticalQuizGenerationResumeTests(unittest.TestCase):
    def setUp(self):
        with political_controller._GENERATION_JOBS_LOCK:
            political_controller._GENERATION_JOBS.clear()

    def tearDown(self):
        with political_controller._GENERATION_JOBS_LOCK:
            political_controller._GENERATION_JOBS.clear()

    def test_failed_generation_job_reuses_same_id_and_checkpoint(self):
        options = {
            "exam_scope": "national",
            "province": "",
            "period": "custom",
            "period_start": "2026-09",
            "period_end": "2026-09",
            "question_count": 20,
            "mode": "practice",
        }
        job = political_controller._create_generation_job(options)
        political_controller._set_generation_checkpoint(
            job["job_id"],
            {"phase": "repair", "progress": 94, "data": {"meta": {}, "questions": []}},
        )
        political_controller._update_generation_job(
            job["job_id"],
            status="failed",
            progress=94,
            phase="failed",
            message="Codex 请求超时",
            error="Codex 请求超时",
        )

        resumed, checkpoint = political_controller._resume_generation_job(job["job_id"], options)

        self.assertIsNotNone(resumed)
        self.assertEqual(resumed["job_id"], job["job_id"])
        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(resumed["progress"], 94)
        self.assertEqual(resumed["phase"], "repair")
        self.assertEqual(checkpoint["phase"], "repair")
        self.assertEqual(checkpoint["progress"], 94)


class PoliticalQuizWebTests(unittest.TestCase):
    def test_sidebar_contains_top_level_political_quiz_entry(self):
        html = layout("时政测验", "<p>内容</p>", "political_quiz")
        self.assertIn('href="/political-quiz"', html)
        self.assertIn(">时政测验</span>", html)
        self.assertRegex(html, r'href="/political-quiz" class="active"')
        self.assertLess(html.index(">错题本</span>"), html.index(">时政测验</span>"))
        self.assertLess(html.index(">时政测验</span>"), html.index(">训练分析</span>"))

    def test_political_quiz_get_routes_dispatch(self):
        handler = DummyPoliticalQuizHandler()
        query = {"mode": ["practice"]}
        self.assertTrue(dispatch_get(handler, "/political-quiz", query))
        self.assertEqual(handler.calls[-1], ("home", query))
        self.assertTrue(dispatch_get(handler, "/political-quiz/packs/12", query))
        self.assertEqual(handler.calls[-1], ("pack", "/political-quiz/packs/12", query))
        self.assertTrue(dispatch_get(handler, "/political-quiz/generation-jobs/abc/status", query))
        self.assertEqual(handler.calls[-1], ("generation_status", "/political-quiz/generation-jobs/abc/status"))

    def test_political_quiz_post_routes_dispatch(self):
        handler = DummyPoliticalQuizHandler()
        self.assertTrue(dispatch_post(handler, "/political-quiz/generate"))
        self.assertEqual(handler.calls[-1], ("generate",))
        self.assertTrue(dispatch_post(handler, "/political-quiz/attempts/7"))
        self.assertEqual(handler.calls[-1], ("attempt", "/political-quiz/attempts/7"))
        self.assertTrue(dispatch_post(handler, "/political-quiz/attempts/7/submit"))
        self.assertEqual(handler.calls[-1], ("submit", "/political-quiz/attempts/7/submit"))
        self.assertTrue(dispatch_post(handler, "/political-quiz/packs/12/verify"))
        self.assertEqual(handler.calls[-1], ("verify", "/political-quiz/packs/12/verify"))


if __name__ == "__main__":
    unittest.main()
