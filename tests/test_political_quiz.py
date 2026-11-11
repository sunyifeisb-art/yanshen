import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from gongkao.ai import (
    CODEX_EXEC_TIMEOUT_SECONDS,
    CODEX_RESEARCH_TIMEOUT_SECONDS,
    build_codex_exec_command,
    codex_research_completion,
)
from gongkao.db import init_db
from gongkao.political_quiz import (
    _merge_quiz_batch,
    _planned_question_blueprints,
    build_attempt_analysis,
    build_generation_prompt,
    create_quiz_attempt,
    generate_quiz_pack,
    get_quiz_attempt,
    list_quiz_packs,
    parse_quiz_json_response,
    record_quiz_generation_failure,
    save_quiz_pack,
    update_quiz_attempt,
    validate_quiz_pack,
    validate_quiz_quality,
)


@contextmanager
def open_database(path):
    """提交并关闭连接：Windows 上未关闭的句柄会让临时目录清理报 WinError 32。"""
    conn = sqlite3.connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def sample_pack():
    return {
        "meta": {
            "title": "2026年9月政治理论5题",
            "exam_scope": "national",
            "province": "",
            "period_label": "2026年9月",
            "verification_status": "verified",
        },
        "cards": [
            {"tag": "中央文件", "title": "测试材料", "body": "测试正文", "source": "中国政府网", "date": "2026-09-01"}
        ],
        "questions": [
            {
                "type": "combo",
                "stem": "组合题题干",
                "ask": "正确的是",
                "statements": ["甲", "乙", "丙", "丁"],
                "truth": [1, 1, 0, 1],
                "options": ["①②③", "①②④", "①③④", "②③④"],
                "answer_index": 1,
                "explanations": ["①正确。", "②正确。", "③错误。", "④正确。"],
                "source": {"name": "中国政府网", "date": "2026-09-01", "url": "https://www.gov.cn/example1"},
                "topic": "中央文件",
                "trap_type": "偷换关键词",
                "key_words": ["关键词甲"],
            },
            {
                "type": "count",
                "stem": "计数题题干",
                "ask": "正确的有几项",
                "statements": ["甲", "乙", "丙", "丁"],
                "truth": [1, 0, 1, 1],
                "options": ["1项", "2项", "3项", "4项"],
                "answer_index": 2,
                "explanations": ["①正确。", "②错误。", "③正确。", "④正确。"],
                "source": {"name": "新华社", "date": "2026-09-02", "url": "https://www.news.cn/example2"},
                "topic": "重要会议",
                "trap_type": "方向颠倒",
                "key_words": ["从A到B"],
            },
            {
                "type": "count5",
                "stem": "五条计数题",
                "ask": "正确的有几项",
                "statements": ["甲", "乙", "丙", "丁", "戊"],
                "truth": [1, 1, 1, 0, 1],
                "options": ["2项", "3项", "4项", "5项"],
                "answer_index": 2,
                "explanations": ["①正确。", "②正确。", "③正确。", "④错误。", "⑤正确。"],
                "source": {"name": "求是网", "date": "2026-09-03", "url": "https://www.qstheory.cn/example3"},
                "topic": "马克思主义基本原理",
                "trap_type": "绝对化",
                "key_words": ["必然"],
            },
            {
                "type": "pick",
                "stem": "四句式题干",
                "ask": "下列说法正确的是",
                "options": ["A表述", "B表述", "C表述", "D表述"],
                "answer_index": 3,
                "explanations": ["A错误。", "B错误。", "C错误。", "D正确。"],
                "source": {"name": "人民日报", "date": "2026-09-04", "url": "https://paper.people.com.cn/example4"},
                "topic": "民生",
                "trap_type": "张冠李戴",
                "key_words": ["主体错位"],
            },
            {
                "type": "map",
                "stem": "对应关系题干",
                "ask": "对应正确的是",
                "options": ["甲—一", "乙—二", "丙—三", "丁—四"],
                "answer_index": 0,
                "explanations": ["A正确。", "B错误。", "C错误。", "D错误。"],
                "source": {"name": "最高人民法院", "date": "2026-09-05", "url": "https://www.court.gov.cn/example5"},
                "topic": "法治",
                "trap_type": "无中生有",
                "key_words": ["对应关系"],
            },
        ],
        "traps": [
            {"topic": "中央文件", "qs": [1], "set": [{"q": 1, "wrong": "错误", "right": "正确", "how": "偷换关键词"}], "miss": ["易混点"]},
            {"topic": "重要会议", "qs": [2], "set": [{"q": 2, "wrong": "错误", "right": "正确", "how": "方向颠倒"}], "miss": ["易混点"]},
            {"topic": "马原", "qs": [3], "set": [{"q": 3, "wrong": "错误", "right": "正确", "how": "绝对化"}], "miss": ["易混点"]},
            {"topic": "民生", "qs": [4], "set": [{"q": 4, "wrong": "错误", "right": "正确", "how": "张冠李戴"}], "miss": ["易混点"]},
            {"topic": "法治", "qs": [5], "set": [{"q": 5, "wrong": "错误", "right": "正确", "how": "无中生有"}], "miss": ["易混点"]},
        ],
        "cheat": {
            "trap_methods": ["方向颠倒", "绝对化", "偷换关键词", "无中生有", "张冠李戴"],
            "numbers": [],
        },
    }


def exam_quality_pack_10():
    pack = sample_pack()
    base_questions = pack["questions"]
    traps = [
        "主体替换",
        "定位词错位",
        "时间节点混装",
        "方向反转",
        "绝对化和过度化",
        "政策目标与工具错配",
        "近邻概念归属错位",
        "半句正确半句错误",
        "条件偷换",
        "真知识放错语境",
    ]
    relations = [
        "主体关系",
        "定位关系",
        "时间层级",
        "方向关系",
        "条件范围",
        "概念归属",
        "目标工具",
        "理论对应",
        "制度机制",
        "法治主体程序",
    ]
    layers = [
        "马克思主义基本原理",
        "党的创新理论与党的建设",
        "党的创新理论与党的建设",
        "改革与国家治理",
        "改革与国家治理",
        "改革与国家治理",
        "发展与安全",
        "改革与国家治理",
        "发展与安全",
        "党的创新理论与党的建设",
    ]
    types = ["combo", "combo", "count", "pick", "pick", "pick", "pick", "pick", "pick", "pick"]
    answers = [0, 1, 2, 3, 0, 1, 2, 3, 0, 1]
    questions = []
    trap_groups = []
    for index in range(10):
        qtype = types[index]
        template = deepcopy(base_questions[0 if qtype == "combo" else 1 if qtype == "count" else 3])
        template["type"] = qtype
        template["stem"] = f"第{index + 1}题围绕规范关系进行辨析"
        template["ask"] = "下列说法正确的是" if qtype == "pick" else template["ask"]
        template["source"] = {
            "name": "中国政府网",
            "date": "2026-09-01",
            "url": f"https://www.gov.cn/zhengce/example-{index % 5}",
        }
        template["topic"] = layers[index]
        template["trap_type"] = traps[index]
        template["knowledge_layer"] = layers[index]
        template["relation_type"] = relations[index]
        template["trap_point"] = {
            "wrong": f"错误片段{index}",
            "right": f"规范片段{index}",
            "reason": "把同一政策体系中的相邻关系进行了微调替换",
        }
        template["key_words"] = [f"辨析词{index}"]
        if qtype == "combo":
            combo_truth = ([1, 1, 0, 0], [1, 0, 1, 0])[index]
            template["truth"] = list(combo_truth)
            correct = "".join("①②③④"[i] for i, flag in enumerate(combo_truth) if flag)
            template["options"] = ["①②", "①③", "①④", "②③"]
            template["answer_index"] = template["options"].index(correct)
            template["explanations"] = [
                "①正确，主体与规范关系一致。",
                "②正确，定位与政策文本一致。" if combo_truth[1] else "②错误，定位词被替换，应回到规范关系。",
                "③正确，阶段关系一致。" if combo_truth[2] else "③错误，时间层级被混装，应区分阶段目标。",
                "④错误，政策对象发生替换，应按权威文本理解。",
            ]
        elif qtype == "count":
            template["truth"] = [1, 0, 1, 1]
            template["options"] = ["1项", "2项", "3项", "4项"]
            template["answer_index"] = 2
            template["explanations"] = [
                "①正确，主体关系与规范表述一致。",
                "②错误，方向发生反转，应按原有政策关系理解。",
                "③正确，目标与工具对应准确。",
                "④正确，概念归属没有发生错位。",
            ]
        else:
            template["answer_index"] = answers[index]
            template["options"] = [
                f"A项规范表述{index}",
                f"B项相邻关系表述{index}",
                f"C项政策工具表述{index}",
                f"D项制度定位表述{index}",
            ]
            template["explanations"] = [
                "A项需要核对主体和定位，错误处为微调后的关系。",
                "B项需要核对概念归属，不能因表述熟悉直接判断。",
                "C项需要核对政策目标与工具是否直接对应。",
                "D项需要核对制度层级和规范对象是否一致。",
            ]
        questions.append(template)
        trap_groups.append(
            {
                "topic": template["topic"],
                "qs": [index + 1],
                "set": [{"q": index + 1, "wrong": "错误片段", "right": "规范片段", "how": traps[index]}],
                "miss": ["相邻概念"],
            }
        )
    pack["questions"] = questions
    pack["traps"] = trap_groups
    pack["cheat"]["trap_methods"] = traps
    pack["meta"]["title"] = "10题国考拟真题包"
    return pack


def batch_pack_from_blueprints(blueprints):
    base = sample_pack()
    templates = {question["type"]: question for question in base["questions"]}
    questions = []
    for local_index, blueprint in enumerate(blueprints, start=1):
        question = deepcopy(templates[blueprint["type"]])
        number = int(blueprint["number"])
        question["stem"] = f"批次测试第{number}题规范关系辨析"
        question["source"] = {
            "name": "中国政府网",
            "date": "2026-03-01",
            "url": f"https://www.gov.cn/zhengce/batch-test-{number}",
        }
        question["topic"] = blueprint["knowledge_layer"]
        question["trap_type"] = blueprint["trap_type"]
        question["knowledge_layer"] = blueprint["knowledge_layer"]
        question["relation_type"] = blueprint["relation_type"]
        question["trap_point"] = {
            "wrong": f"错误片段{number}",
            "right": f"规范片段{number}",
            "reason": "用于测试分批生成与合并",
        }
        question["key_words"] = [f"关键词{number}"]
        if question["type"] in {"pick", "map"}:
            question["answer_index"] = int(blueprint["answer_index"])
        questions.append(question)
    base["meta"]["title"] = f"批次测试{len(questions)}题"
    base["cards"] = []
    base["questions"] = questions
    base["traps"] = []
    return base


class PoliticalQuizValidationTests(unittest.TestCase):
    def test_research_uses_longer_timeout_without_changing_reasoning_policy(self):
        self.assertGreater(CODEX_RESEARCH_TIMEOUT_SECONDS, CODEX_EXEC_TIMEOUT_SECONDS)
        with patch("gongkao.ai._codex_completion", return_value=("ok", "ok")) as completion:
            result = codex_research_completion(
                {"mode": "chatgpt"},
                "research",
                {"thinking": "enabled"},
            )
        self.assertEqual(result, ("ok", "ok"))
        self.assertTrue(completion.call_args.kwargs["enable_search"])
        self.assertEqual(completion.call_args.kwargs["timeout_seconds"], CODEX_RESEARCH_TIMEOUT_SECONDS)

    def test_codex_research_command_enables_global_search_before_exec(self):
        command = build_codex_exec_command(
            "/tmp/codex",
            model="gpt-5.6-sol",
            reasoning="high",
            enable_search=True,
        )
        self.assertEqual(command[:3], ["/tmp/codex", "--search", "exec"])
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--skip-git-repo-check", command)
        self.assertIn("--ephemeral", command)
        self.assertIn("read-only", command)
        self.assertIn("gpt-5.6-sol", command)

    def test_valid_pack_passes(self):
        self.assertEqual(validate_quiz_pack(sample_pack()), [])

    def test_combo_answer_accepts_equivalent_option_formats(self):
        """A cosmetic difference in the combo option label must not fail the pack."""
        for option in ("①②④", "A．①②④", "①、②、④", "（1）（2）（4）", "124", " ①②④ "):
            with self.subTest(option=option):
                pack = sample_pack()
                pack["questions"][0]["options"][1] = option
                self.assertEqual(validate_quiz_pack(pack), [])

    def test_combo_answer_still_rejects_a_wrong_statement_set(self):
        pack = sample_pack()
        pack["questions"][0]["options"][1] = "①②③"
        self.assertIn("第1题组合答案错位", validate_quiz_pack(pack))

    def test_count_answer_accepts_circled_and_chinese_numerals(self):
        for option in ("3项", "共3项", "③", "三项", "3"):
            with self.subTest(option=option):
                pack = sample_pack()
                pack["questions"][1]["options"][2] = option
                self.assertEqual(validate_quiz_pack(pack), [])

    def test_shanghai_multi_question_uses_truth_vector_instead_of_single_answer_index(self):
        pack = sample_pack()
        pack["questions"][3] = {
            "type": "multi",
            "stem": "上海不定项题干",
            "ask": "下列说法正确的有",
            "options": ["甲", "乙", "丙", "丁"],
            "truth": [1, 0, 1, 0],
            "explanations": ["甲正确。", "乙错误。", "丙正确。", "丁错误。"],
            "source": {"name": "中国政府网", "date": "2026-09-05", "url": "https://www.gov.cn/multi"},
            "topic": "上海省情",
            "trap_type": "张冠李戴",
            "key_words": ["甲", "丙"],
        }
        self.assertEqual(validate_quiz_pack(pack), [])
        analysis = build_attempt_analysis(pack, [1, 2, 2, [0, 2], 0])
        self.assertEqual(analysis["correct_count"], 5)
        wrong = build_attempt_analysis(pack, [1, 2, 2, [0], 0])
        self.assertEqual(wrong["correct_count"], 4)

    def test_shanghai_generation_prompt_requires_multi_select(self):
        prompt = build_generation_prompt(
            exam_scope="province",
            province="上海",
            period="month",
            question_count=20,
        )
        self.assertIn("不定项选择", prompt)
        self.assertIn("multi", prompt)
        self.assertIn("多选、少选、错选均不得分", prompt)

    def test_invalid_pack_reports_structural_failures(self):
        pack = sample_pack()
        pack["questions"][0]["source"]["url"] = ""
        pack["questions"][1]["answer_index"] = 1
        pack["questions"][2]["explanations"] = pack["questions"][2]["explanations"][:4]
        pack["traps"][-1]["qs"] = []
        errors = validate_quiz_pack(pack)
        joined = "\n".join(errors)
        self.assertIn("第1题缺少来源", joined)
        self.assertIn("第2题计数答案错位", joined)
        self.assertIn("第3题解析条数异常", joined)
        self.assertIn("易错清单未覆盖第5题", joined)

    def test_parse_quiz_json_response_accepts_fenced_json_only(self):
        payload = sample_pack()
        raw = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        parsed = parse_quiz_json_response(raw)
        self.assertEqual(parsed["meta"]["title"], payload["meta"]["title"])
        with self.assertRaises(ValueError):
            parse_quiz_json_response("不是 JSON")

    def test_parse_quiz_json_response_recovers_embedded_complete_object(self):
        payload = sample_pack()
        payload["meta"]["title"] = '含有 {括号} 和 "引号" 的题包'
        encoded = json.dumps(payload, ensure_ascii=False)
        cases = [
            f"生成完成，结果如下：\n{encoded}\n请查收。",
            f"说明文字\n```json\n{encoded}\n```\n结束说明",
            f"```text\n这不是结果\n```\n```json\n{encoded}\n```",
        ]
        for raw in cases:
            with self.subTest(raw=raw[:30]):
                parsed = parse_quiz_json_response(raw)
                self.assertEqual(parsed["meta"]["title"], payload["meta"]["title"])

    def test_parse_quiz_json_response_does_not_accept_truncated_outer_pack(self):
        payload = sample_pack()
        encoded = json.dumps(payload, ensure_ascii=False)
        truncated = encoded[: encoded.index('"questions"') + len('"questions"') + 5]
        with self.assertRaisesRegex(ValueError, "不是合法 JSON"):
            parse_quiz_json_response(truncated)

    def test_generation_prompt_preserves_skill_hard_rules(self):
        prompt = build_generation_prompt(
            exam_scope="national",
            province="",
            period="month",
            question_count=20,
        )
        self.assertIn("新华社", prompt)
        self.assertIn("中国政府网", prompt)
        self.assertIn("网友回忆版", prompt)
        self.assertIn("不得作为政治表述来源", prompt)
        self.assertIn("组合3", prompt)
        self.assertIn("计数3", prompt)
        self.assertIn("至少1道5条", prompt)
        self.assertIn("单个合法 JSON 对象", prompt)
        self.assertIn("逐字核对", prompt)

    def test_generation_prompt_encodes_recent_national_exam_construction(self):
        prompt = build_generation_prompt(question_count=20)
        self.assertIn("不要把“时政”理解成每日新闻问答", prompt)
        self.assertIn("关系素材池", prompt)
        self.assertIn("主体替换", prompt)
        self.assertIn("定位词错位", prompt)
        self.assertIn("时间节点混装", prompt)
        self.assertIn("政策目标与工具错配", prompt)
        self.assertIn("近邻概念归属错位", prompt)
        self.assertIn("半句正确半句错误", prompt)
        self.assertIn("九成正确感", prompt)
        self.assertIn("马克思主义基本原理", prompt)
        self.assertIn("knowledge_layer", prompt)
        self.assertIn("relation_type", prompt)
        self.assertIn("trap_point", prompt)

    def test_exam_quality_gate_accepts_realistic_pack_and_rejects_news_trivia(self):
        pack = exam_quality_pack_10()
        self.assertEqual(validate_quiz_pack(pack), [])
        self.assertEqual(validate_quiz_quality(pack, question_count=10, exam_scope="national"), [])

        weak = deepcopy(pack)
        weak["questions"][0]["stem"] = "某重要会议召开时间是哪一天召开"
        weak["questions"][0].pop("trap_point")
        weak["questions"][1]["source"]["url"] = "https://example.com/news"
        joined = "\n".join(validate_quiz_quality(weak, question_count=10, exam_scope="national"))
        self.assertIn("新闻日期/地点记忆题", joined)
        self.assertIn("缺少可核查的微改设误点", joined)
        self.assertIn("来源域名不在官方白名单", joined)

    def test_exam_quality_gate_accepts_semantic_trap_type_aliases(self):
        pack = exam_quality_pack_10()
        pack["questions"][0]["trap_type"] = "主体错位"
        pack["questions"][1]["trap_type"] = "阶段目标错层"
        pack["questions"][2]["trap_type"] = "范围扩大"
        self.assertEqual(validate_quiz_quality(pack, question_count=10, exam_scope="national"), [])

    def test_real_generation_runs_mandatory_exam_quality_review(self):
        payload = exam_quality_pack_10()
        raw = json.dumps(payload, ensure_ascii=False)
        with patch("gongkao.political_quiz.codex_research_completion", return_value=(raw, raw)) as research:
            with patch("gongkao.political_quiz.codex_chat_completion", return_value=(raw, raw)) as review:
                pack, _prompt, _raw = generate_quiz_pack({"mode": "chatgpt"}, question_count=10)
        self.assertEqual(research.call_count, 1)
        self.assertEqual(review.call_count, 1)
        self.assertTrue(pack["meta"]["quality_reviewed"])
        self.assertEqual(pack["meta"]["quality_profile"], "national-exam-2016-2025-v2")
        self.assertEqual(validate_quiz_quality(pack, question_count=10, exam_scope="national"), [])

    def test_generation_prompt_accepts_single_month_and_flexible_month_range(self):
        single = build_generation_prompt(period="custom", period_start="2026-05", period_end="2026-05")
        half_year = build_generation_prompt(period="custom", period_start="2026-01", period_end="2026-06")
        cross_year = build_generation_prompt(period="custom", period_start="2025-11", period_end="2026-02")
        self.assertIn("时间范围：2026年5月", single)
        self.assertIn("2026年1月至6月", half_year)
        self.assertIn("2025年11月至2026年2月", cross_year)
        self.assertIn("发生或发布日期必须落在该区间内", half_year)

    def test_generation_prompt_rejects_reversed_month_range(self):
        with self.assertRaisesRegex(ValueError, "起始月份不能晚于结束月份"):
            build_generation_prompt(period="custom", period_start="2026-08", period_end="2026-03")

    def test_generation_prompt_scales_type_mix_for_10_and_30_questions(self):
        ten = build_generation_prompt(question_count=10)
        thirty = build_generation_prompt(question_count=30)
        self.assertIn("组合2、计数1（含1道5条）、选非2、四句式选是5", ten)
        self.assertIn("组合5、计数4（至少1道5条）、选非7、四句式选是14", thirty)
        self.assertNotIn("组合3、计数3（至少1道5条）、选非5、四句式选是9", ten)
        self.assertNotIn("组合3、计数3（至少1道5条）、选非5、四句式选是9", thirty)

    def test_generated_pack_must_match_requested_question_count(self):
        raw = json.dumps(sample_pack(), ensure_ascii=False)
        with patch("gongkao.political_quiz.codex_research_completion", return_value=(raw, {"ok": True})):
            with self.assertRaisesRegex(ValueError, "题数异常"):
                generate_quiz_pack({"mode": "chatgpt"}, question_count=10)

    def test_generated_pack_repairs_structural_errors_without_second_web_search(self):
        broken = sample_pack()
        broken["questions"][0]["statements"] = broken["questions"][0]["statements"][:3]
        broken_raw = json.dumps(broken, ensure_ascii=False)
        repaired_raw = json.dumps(sample_pack(), ensure_ascii=False)
        progress = []
        with patch("gongkao.political_quiz.codex_research_completion", return_value=(broken_raw, broken_raw)) as research:
            with patch("gongkao.political_quiz.codex_chat_completion", return_value=(repaired_raw, repaired_raw)) as repair:
                pack, _prompt, _raw = generate_quiz_pack(
                    {"mode": "chatgpt"},
                    question_count=5,
                    progress_callback=progress.append,
                )
        self.assertEqual(validate_quiz_pack(pack), [])
        self.assertEqual(research.call_count, 1)
        self.assertEqual(repair.call_count, 1)
        self.assertTrue(any(item.get("phase") == "repair" for item in progress))

    def test_failed_repair_can_resume_without_repeating_web_research(self):
        broken = sample_pack()
        broken["questions"][0]["statements"] = broken["questions"][0]["statements"][:3]
        resume_state = {
            "phase": "repair",
            "progress": 94,
            "prompt": "cached generation prompt",
            "raw": "cached research response",
            "data": broken,
        }
        repaired_raw = json.dumps(sample_pack(), ensure_ascii=False)
        checkpoints = []
        progress = []
        with patch("gongkao.political_quiz.codex_research_completion") as research:
            with patch("gongkao.political_quiz.codex_chat_completion", return_value=(repaired_raw, repaired_raw)) as repair:
                pack, prompt, raw = generate_quiz_pack(
                    {"mode": "chatgpt"},
                    question_count=5,
                    progress_callback=progress.append,
                    resume_state=resume_state,
                    checkpoint_callback=checkpoints.append,
                )
        self.assertEqual(validate_quiz_pack(pack), [])
        self.assertEqual(prompt, "cached generation prompt")
        self.assertEqual(raw, "cached research response")
        self.assertEqual(research.call_count, 0)
        self.assertEqual(repair.call_count, 1)
        self.assertTrue(any(item.get("phase") == "repair" and item.get("progress") >= 94 for item in progress))
        self.assertTrue(any(item.get("phase") == "save" and item.get("progress") == 97 for item in checkpoints))

    def test_failed_parse_can_resume_from_cached_content_without_repeating_web_research(self):
        payload = sample_pack()
        cached_content = "生成结果如下：\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n以上。"
        resume_state = {
            "phase": "parse",
            "progress": 82,
            "prompt": "cached generation prompt",
            "raw": "cached research response",
            "content": cached_content,
        }
        progress = []
        with patch("gongkao.political_quiz.codex_research_completion") as research:
            pack, prompt, raw = generate_quiz_pack(
                {"mode": "chatgpt"},
                question_count=5,
                progress_callback=progress.append,
                resume_state=resume_state,
            )
        self.assertEqual(validate_quiz_pack(pack), [])
        self.assertEqual(prompt, "cached generation prompt")
        self.assertEqual(raw, "cached research response")
        self.assertEqual(research.call_count, 0)
        self.assertTrue(any(item.get("phase") == "parse" and item.get("progress") >= 82 for item in progress))

    def test_malformed_parse_uses_non_web_json_repair(self):
        payload = sample_pack()
        repaired_raw = json.dumps(payload, ensure_ascii=False)
        checkpoints = []
        with patch("gongkao.political_quiz.codex_research_completion", return_value=("{broken", "cached raw")) as research:
            with patch("gongkao.political_quiz.codex_chat_completion", return_value=(repaired_raw, repaired_raw)) as repair:
                pack, _prompt, _raw = generate_quiz_pack(
                    {"mode": "chatgpt"},
                    question_count=5,
                    checkpoint_callback=checkpoints.append,
                )
        self.assertEqual(validate_quiz_pack(pack), [])
        self.assertEqual(research.call_count, 1)
        self.assertEqual(repair.call_count, 1)
        self.assertTrue(any(item.get("phase") == "parse_repair" and item.get("progress") == 85 for item in checkpoints))

    def test_twenty_question_generation_uses_four_bounded_batches(self):
        blueprints = _planned_question_blueprints("national", "", 20)
        batch_payloads = [
            batch_pack_from_blueprints(blueprints[index:index + 5])
            for index in range(0, 20, 5)
        ]
        responses = [
            (json.dumps(payload, ensure_ascii=False), json.dumps(payload, ensure_ascii=False))
            for payload in batch_payloads
        ]
        checkpoints = []
        with patch("gongkao.political_quiz.codex_research_completion", side_effect=responses) as research:
            with patch("gongkao.political_quiz.codex_chat_completion") as chat:
                with patch("gongkao.political_quiz.validate_quiz_quality", return_value=[]):
                    pack, _prompt, _raw = generate_quiz_pack(
                        {"mode": "chatgpt"},
                        question_count=20,
                        checkpoint_callback=checkpoints.append,
                    )
        self.assertEqual(research.call_count, 4)
        self.assertEqual(chat.call_count, 0)
        self.assertEqual(len(pack["questions"]), 20)
        self.assertEqual(validate_quiz_pack(pack), [])
        self.assertTrue(pack["meta"]["batched_generation"])
        self.assertTrue(any(item.get("phase") == "batch_generate" and item.get("batch_index") == 4 for item in checkpoints))

    def test_batched_generation_retries_only_the_incomplete_batch(self):
        blueprints = _planned_question_blueprints("national", "", 20)
        batch_payloads = [
            batch_pack_from_blueprints(blueprints[index:index + 5])
            for index in range(0, 20, 5)
        ]
        incomplete = deepcopy(batch_payloads[0])
        incomplete["questions"] = incomplete["questions"][:3]
        responses = [incomplete, batch_payloads[0], *batch_payloads[1:]]
        side_effect = [
            (json.dumps(payload, ensure_ascii=False), json.dumps(payload, ensure_ascii=False))
            for payload in responses
        ]
        with patch("gongkao.political_quiz.codex_research_completion", side_effect=side_effect) as research:
            with patch("gongkao.political_quiz.codex_chat_completion") as chat:
                with patch("gongkao.political_quiz.validate_quiz_quality", return_value=[]):
                    pack, _prompt, _raw = generate_quiz_pack({"mode": "chatgpt"}, question_count=20)
        self.assertEqual(research.call_count, 5)
        self.assertEqual(chat.call_count, 0)
        self.assertEqual(len(pack["questions"]), 20)
        self.assertEqual(validate_quiz_pack(pack), [])

    def test_batched_generation_resumes_from_last_completed_batch(self):
        blueprints = _planned_question_blueprints("national", "", 20)
        batch_payloads = [
            batch_pack_from_blueprints(blueprints[index:index + 5])
            for index in range(0, 20, 5)
        ]
        partial = _merge_quiz_batch(None, batch_payloads[0])
        partial = _merge_quiz_batch(partial, batch_payloads[1])
        resume_state = {
            "phase": "batch_generate",
            "progress": 48,
            "prompt": "cached 20-question prompt",
            "raw": "cached second batch",
            "batch_data": partial,
            "batch_index": 2,
        }
        remaining = [
            (json.dumps(payload, ensure_ascii=False), json.dumps(payload, ensure_ascii=False))
            for payload in batch_payloads[2:]
        ]
        with patch("gongkao.political_quiz.codex_research_completion", side_effect=remaining) as research:
            with patch("gongkao.political_quiz.codex_chat_completion") as chat:
                with patch("gongkao.political_quiz.validate_quiz_quality", return_value=[]):
                    pack, prompt, _raw = generate_quiz_pack(
                        {"mode": "chatgpt"},
                        question_count=20,
                        resume_state=resume_state,
                    )
        self.assertEqual(research.call_count, 2)
        self.assertEqual(chat.call_count, 0)
        self.assertEqual(prompt, "cached 20-question prompt")
        self.assertEqual(len(pack["questions"]), 20)
        self.assertEqual(validate_quiz_pack(pack), [])


class PoliticalQuizPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "quiz.sqlite3"
        init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_existing_current_version_database_gets_quiz_tables_additively(self):
        seed = Path(__file__).resolve().parent.parent / "data" / "gongkao_seed.sqlite3"
        migrated = Path(self.tmp.name) / "existing.sqlite3"
        shutil.copy2(seed, migrated)
        init_db(migrated)
        with open_database(migrated) as conn:
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        self.assertIn("political_quiz_packs", tables)
        self.assertIn("political_quiz_attempts", tables)

    def test_pack_and_attempt_round_trip(self):
        with open_database(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            pack_id = save_quiz_pack(conn, sample_pack(), verification_status="verified")
            packs = list_quiz_packs(conn)
            self.assertEqual(len(packs), 1)
            self.assertEqual(packs[0]["id"], pack_id)
            attempt_id = create_quiz_attempt(conn, pack_id, "practice")
            update_quiz_attempt(conn, attempt_id, {"0": 1, "1": 0}, elapsed_seconds=63)
            attempt = get_quiz_attempt(conn, attempt_id)
            self.assertEqual(json.loads(attempt["answers_json"]), {"0": 1, "1": 0})
            self.assertEqual(attempt["elapsed_seconds"], 63)

    def test_attempt_analysis_groups_topics_and_traps(self):
        pack = sample_pack()
        analysis = build_attempt_analysis(pack, [1, 0, 2, 3, 2])
        self.assertEqual(analysis["correct_count"], 3)
        self.assertEqual(analysis["question_count"], 5)
        self.assertEqual(analysis["topic_stats"]["中央文件"]["correct"], 1)
        self.assertEqual(analysis["trap_errors"]["方向颠倒"], 1)
        self.assertEqual(analysis["trap_errors"]["无中生有"], 1)
        self.assertIn("从A到B", analysis["key_words"])
        self.assertIn("对应关系", analysis["key_words"])
        self.assertLessEqual(len(analysis["key_words"]), 3)

    def test_failed_generation_is_persisted_and_hidden_from_the_pack_library(self):
        options = {
            "exam_scope": "national",
            "province": "",
            "period_start": "2026-09",
            "period_end": "2026-09",
            "question_count": 20,
        }
        with open_database(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            save_quiz_pack(conn, sample_pack(), verification_status="verified")
            record_quiz_generation_failure(
                conn,
                error="第 3/4 批生成不完整：第1题组合答案错位。",
                options=options,
            )
            row = conn.execute(
                "SELECT status, generation_error FROM political_quiz_packs WHERE pack_key LIKE 'failed:%'"
            ).fetchone()
            self.assertEqual(row["status"], "failed")
            self.assertIn("组合答案错位", row["generation_error"])
            # a failure must never surface as a reusable pack
            self.assertEqual([pack["status"] for pack in list_quiz_packs(conn)], ["ready"])

    def test_repeated_failures_keep_only_the_latest_per_configuration(self):
        options = {
            "exam_scope": "national",
            "province": "",
            "period_start": "2026-09",
            "period_end": "2026-09",
            "question_count": 20,
        }
        with open_database(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            for message in ("第一次失败", "第二次失败"):
                record_quiz_generation_failure(conn, error=message, options=options)
            rows = conn.execute(
                "SELECT generation_error FROM political_quiz_packs WHERE status = 'failed'"
            ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertIn("第二次失败", rows[0]["generation_error"])


if __name__ == "__main__":
    unittest.main()
