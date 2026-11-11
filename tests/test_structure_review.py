import unittest

from gongkao.grading_pipeline.report import render_grading_report
from gongkao.grading_pipeline.validation import validate_grading_result


class StructureReviewTest(unittest.TestCase):
    def test_supported_reorganization_limits_structure_score_and_appears_in_report(self):
        answer = "先谈结果，再谈原因。"
        rubric = {
            "question_type": "综合写作",
            "scoring_mode": "holistic_essay",
            "dimensions": [
                {"dimension": "content", "weight": 40},
                {"dimension": "reasoning", "weight": 25},
                {"dimension": "structure", "weight": 20},
                {"dimension": "expression", "weight": 10},
                {"dimension": "format", "weight": 5},
            ],
            "points": [],
        }
        raw = {
            "dimension_scores": [
                {"dimension": "content", "score": 10, "reason": "主题基本相关。"},
                {"dimension": "reasoning", "score": 10, "reason": "论证不足。"},
                {"dimension": "structure", "score": 19, "reason": "分段清楚。"},
                {"dimension": "expression", "score": 7, "reason": "表达通顺。"},
                {"dimension": "format", "score": 4, "reason": "格式基本正确。"},
            ],
            "structure_review": {
                "expected_outline": ["原因", "结果"],
                "answer_outline": ["结果", "原因"],
                "comparison": "材料先交代原因，再推出结果；原答案倒置且没有过渡。",
                "verdict": "需重组",
                "issues": [{
                    "answer_quote": "先谈结果",
                    "reference_basis": "材料中的原因先于结果，参考答案也先分析原因。",
                    "problem": "结果先行使后文原因无法支撑前文判断。",
                    "revision": "先写原因，再写由此产生的结果。",
                }],
                "score_reason": "因果顺序倒置，主要论证线未衔接。",
            },
        }
        result = validate_grading_result(
            raw, rubric, answer, [], calibration_policy={"enabled": False},
            require_structure_review=True,
        )
        structure_score = next(item["score"] for item in result["dimension_scores"] if item["dimension"] == "structure")
        self.assertEqual(structure_score, 10)
        self.assertEqual(result["structure_score_cap"]["verdict"], "需重组")
        report = render_grading_report(result, rubric, [])
        self.assertIn("## 结构与顺序审查", report)
        self.assertIn("先谈结果", report)
        self.assertIn("先写原因，再写由此产生的结果", report)

    def test_new_grading_requires_evidence_backed_structure_review(self):
        with self.assertRaisesRegex(ValueError, "缺少独立的答案结构审查"):
            validate_grading_result({}, {"points": []}, "有内容", [], require_structure_review=True)


if __name__ == "__main__":
    unittest.main()
