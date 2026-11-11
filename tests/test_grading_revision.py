import unittest

from gongkao.web.runtime import render_revision_diff


class GradingRevisionTest(unittest.TestCase):
    def test_revision_diff_keeps_original_text_and_marks_changes_inline(self):
        html = render_revision_diff(
            "成效：改革惠及群众，拓宽融资渠道。",
            "成效：盘活林权价值，拓宽融资渠道，支持林票抵押贷款。",
        )
        self.assertIn("grading-revision-document", html)
        self.assertIn("<del>改革惠及群众</del>", html)
        self.assertIn("<ins>盘活林权价值</ins>", html)
        self.assertIn("<ins>，支持林票抵押贷款</ins>", html)
        self.assertIn("查看最终清洁版", html)

    def test_rewrite_does_not_strike_through_most_of_the_original(self):
        original = "1、守住乡村特色。" + "保留原有表述，" * 20
        rewrite = "1、守住乡村特色。" + "完全不同的范文。" * 20
        html = render_revision_diff(original, rewrite)
        self.assertIn(original, html)
        self.assertIn("查看 AI 改写稿", html)
        self.assertNotIn("<del>", html)


if __name__ == "__main__":
    unittest.main()
