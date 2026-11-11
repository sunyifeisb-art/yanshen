import unittest

from gongkao.pdf_export import GRID_CELL_SIZE_MM, answer_grid_cell_count, safe_pdf_filename


class PdfExportTest(unittest.TestCase):
    def test_grid_uses_explicit_word_requirement(self):
        self.assertEqual(
            answer_grid_cell_count({"word_limit": "300字以内", "requirements": "", "question_type": "归纳概括"}),
            300,
        )
        self.assertEqual(
            answer_grid_cell_count({"word_limit": "800—1000字", "requirements": "", "question_type": "综合写作"}),
            1000,
        )
        self.assertEqual(
            answer_grid_cell_count({"word_limit": "", "requirements": "不超过250字。", "question_type": "综合分析"}),
            250,
        )

    def test_grid_has_safe_fallback_for_legacy_questions(self):
        self.assertEqual(
            answer_grid_cell_count({"word_limit": "", "requirements": "", "question_type": "综合写作"}),
            1200,
        )
        self.assertEqual(
            answer_grid_cell_count({"word_limit": "", "requirements": "", "question_type": "归纳概括"}),
            300,
        )

    def test_pdf_filename_removes_path_characters(self):
        self.assertEqual(safe_pdf_filename('2026/国考:试卷?A'), "2026-国考-试卷-A.pdf")

    def test_answer_grid_cell_is_writable_size(self):
        self.assertEqual(GRID_CELL_SIZE_MM, 7.0)


if __name__ == "__main__":
    unittest.main()
