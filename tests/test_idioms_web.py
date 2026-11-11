import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen

from gongkao.db import connect, init_db
from gongkao.idioms import comparison_groups, core_categories, core_entries, review_queue, update_progress
from gongkao.web import dispatch_get, dispatch_post
from gongkao.web.application import create_server
from gongkao.web.runtime import layout


class DummyIdiomsHandler:
    def __init__(self):
        self.calls = []

    def page_idioms(self, query):
        self.calls.append(("home", query))

    def page_idiom_data(self, query):
        self.calls.append(("data", query))

    def page_idiom_quiz_data(self, query):
        self.calls.append(("quiz", query))

    def handle_idiom_progress(self):
        self.calls.append(("progress",))


class IdiomsWebTests(unittest.TestCase):
    def test_core_corpus_is_complete_and_loadable(self):
        entries = core_entries()
        categories = core_categories()
        groups = comparison_groups()

        self.assertEqual(len(entries), 700)
        self.assertEqual(len(categories), 65)
        self.assertFalse([entry for entry in entries if not entry["definition"].strip()])
        self.assertEqual(entries[0]["term"], "源远流长")
        self.assertIn("历史悠久", entries[0]["explanation"])
        self.assertTrue(entries[0]["pinyin"])
        self.assertGreaterEqual(sum(bool(entry.get("explanation")) for entry in entries), 600)
        self.assertEqual(entries[-1]["term"], "太阿倒持")
        self.assertEqual(len(groups), 161)
        self.assertFalse([
            entry
            for group in groups
            for entry in group["entries"]
            if not entry.get("usage_analysis", {}).get("focus")
        ])
        self.assertTrue(all(group.get("common_ground") for group in groups))
        self.assertTrue(all(group.get("comparison_tip") for group in groups))

    def test_sidebar_contains_top_level_idiom_entry(self):
        html = layout("成语学习", "<p>内容</p>", "idioms")
        self.assertIn('href="/idioms"', html)
        self.assertIn(">成语学习</span>", html)
        self.assertRegex(html, r'href="/idioms" class="active"')
        self.assertLess(html.index(">时政测验</span>"), html.index(">成语学习</span>"))
        self.assertLess(html.index(">成语学习</span>"), html.index(">训练分析</span>"))

    def test_idiom_routes_dispatch(self):
        handler = DummyIdiomsHandler()
        query = {"view": ["library"]}
        self.assertTrue(dispatch_get(handler, "/idioms", query))
        self.assertEqual(handler.calls[-1], ("home", query))
        self.assertTrue(dispatch_get(handler, "/idioms/data", query))
        self.assertEqual(handler.calls[-1], ("data", query))
        self.assertTrue(dispatch_get(handler, "/idioms/quiz-data", query))
        self.assertEqual(handler.calls[-1], ("quiz", query))
        self.assertTrue(dispatch_post(handler, "/idioms/progress"))
        self.assertEqual(handler.calls[-1], ("progress",))

    def test_schema_contains_local_idiom_progress_table(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "idioms.sqlite3"
            init_db(db_path)
            with connect(db_path) as conn:
                names = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(idiom_progress)")}
            self.assertIn("idiom_progress", names)
            self.assertTrue({"entry_id", "status", "is_starred", "correct_count", "wrong_count"}.issubset(columns))

    def test_idiom_page_shell_renders_even_when_corpus_is_temporarily_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "idioms-http.sqlite3"
            init_db(db_path)
            server = create_server(port=0, db_path=db_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"
                with urlopen(f"{base}/idioms", timeout=10) as response:
                    html = response.read().decode("utf-8")
                self.assertIn('data-idiom-page', html)
                self.assertIn("成语学习", html)
                self.assertIn("成语辨析", html)
                self.assertIn("错题积累", html)
                self.assertIn("《言语理解》全部词汇", html)
                self.assertIn("成语小测", html)
                self.assertIn("今日回炉", html)
                self.assertIn("闪卡回炉", html)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_review_queue_prioritizes_wrong_learning_and_starred_words(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "idioms-review.sqlite3"
            init_db(db_path)
            first, second, third = core_entries()[:3]
            with connect(db_path) as conn:
                update_progress(conn, first["id"], status="learning", quiz_result="wrong")
                update_progress(conn, second["id"], status="learning")
                update_progress(conn, third["id"], is_starred=True)
                from gongkao.idioms import load_progress
                progress = load_progress(conn)

            queue = review_queue(progress=progress)
            self.assertEqual([row["id"] for row in queue[:3]], [first["id"], second["id"], third["id"]])

            server = create_server(port=0, db_path=db_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"
                with urlopen(f"{base}/idioms/data?view=review", timeout=10) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["summary"]["review_due"], 3)
                self.assertEqual(payload["entries"][0]["id"], first["id"])
                self.assertIn("错 1 次", payload["entries"][0]["review_reason"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_independent_misuse_corpus_remains_available(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "idioms-misuse.sqlite3"
            init_db(db_path)
            server = create_server(port=0, db_path=db_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"
                with urlopen(f"{base}/idioms/data?view=mistakes", timeout=10) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["total"], 420)
                self.assertEqual(payload["pages"], 11)
                self.assertEqual(len(payload["mistakes"]), 40)

                with urlopen(f"{base}/idioms/data?view=mistakes&page=11", timeout=10) as response:
                    last_page = json.loads(response.read().decode("utf-8"))
                self.assertTrue(last_page["ok"])
                self.assertEqual(last_page["page"], 11)
                self.assertEqual(len(last_page["mistakes"]), 20)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_core_library_contrast_and_quiz_endpoints_return_real_data(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "idioms-core-http.sqlite3"
            init_db(db_path)
            server = create_server(port=0, db_path=db_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"
                with urlopen(f"{base}/idioms/data?view=library", timeout=10) as response:
                    library = json.loads(response.read().decode("utf-8"))
                self.assertTrue(library["ok"])
                self.assertEqual(library["total"], 700)
                self.assertEqual(len(library["categories"]), 65)
                self.assertTrue(library["entries"])
                self.assertTrue(library["entries"][0]["explanation"])
                self.assertTrue(library["entries"][0]["pinyin"])

                with urlopen(f"{base}/idioms/data?view=contrast", timeout=10) as response:
                    contrast = json.loads(response.read().decode("utf-8"))
                self.assertTrue(contrast["ok"])
                self.assertEqual(contrast["total"], 161)
                self.assertTrue(contrast["groups"])
                first_group = contrast["groups"][0]
                self.assertTrue(first_group["common_ground"])
                self.assertTrue(first_group["comparison_tip"])
                self.assertTrue(first_group["entries"][0]["usage_analysis"]["focus"])
                self.assertTrue(first_group["entries"][0]["usage_analysis"]["usage"])

                category = first_group["category"]
                from urllib.parse import quote
                with urlopen(f"{base}/idioms/data?view=contrast&category={quote(category)}", timeout=10) as response:
                    filtered = json.loads(response.read().decode("utf-8"))
                self.assertTrue(filtered["ok"])
                self.assertGreater(filtered["total"], 0)
                self.assertTrue(all(group["category"] == category for group in filtered["groups"]))

                for kind in ("meaning", "context", "contrast", "usage"):
                    with urlopen(
                        f"{base}/idioms/quiz-data?kind={kind}&count=5&seed=regression",
                        timeout=10,
                    ) as response:
                        quiz = json.loads(response.read().decode("utf-8"))
                    self.assertTrue(quiz["ok"])
                    self.assertEqual(len(quiz["questions"]), 5)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
