import re

from .contracts import GradingEvidence, GradingResult

CONTENT_WEIGHTS = {
    "归纳概括": 70,
    "综合分析": 55,
    "提出对策": 60,
    "公文写作": 50,
    "综合写作": 40,
}


def _format_score(value) -> str:
    return f"{round(float(value or 0), 1):g}"


def _safe_overall_summary(result):
    summary = result.get("summary")
    if isinstance(summary, dict):
        values = [summary.get("verdict")]
        values.extend(summary.get("strengths") or [])
        values.extend(summary.get("weaknesses") or [])
        text = "；".join(str(value).strip("；。 ") for value in values if str(value or "").strip())
    else:
        text = str(result.get("overall_summary") or "").strip()
    text = re.sub(r"(?:总评|总分|得分)\s*[：:].*?(?:。|$)", "", text).strip("；。 ")
    return text or "请结合维度得分与遗漏点分析查看。"


def render_grading_report(
    result: GradingResult | dict,
    rubric: dict,
    evidence: list[GradingEvidence] | list[dict],
) -> str:
    """Render the current persisted Markdown report."""
    del evidence  # Reserved for report formats that show evidence cards inline.
    score = float(result.get("score") or 0)
    display_max = float(
        result.get("display_max_score")
        or rubric.get("display_max_score")
        or 100
    )
    display_score = float(result.get("display_score") or 0)
    stale = result.get("score_status") == "stale"
    provisional = result.get("score_status") == "provisional"
    score_label = "原评分（已过期）" if stale else "总分"
    estimated = " · 百分制估分" if result.get("score_is_estimated") else ""
    if score >= 80:
        grade = "优秀"
    elif score >= 65:
        grade = "良好"
    elif score >= 50:
        grade = "一般"
    else:
        grade = "较弱"
    lines = [
        "## 总体评分",
        f"- {score_label}：{_format_score(display_score)}/{_format_score(display_max)}{estimated}",
        f"- 等级：{grade}",
        f"- 综合判断：{_safe_overall_summary(result)}",
    ]
    word_limit = str(result.get("word_limit") or rubric.get("word_limit") or "").strip()
    if word_limit:
        lines.append(f"- 字数要求：{word_limit}")
    if stale:
        lines.append("- 状态：采分点已人工纠正，总分待重新批改；该分数不计入统计。")
    elif provisional:
        lines.append("- 状态：存在尚未解析的关键证据，当前分数待复核且不计入统计。")

    display_scale = display_max / 100
    content_weight = CONTENT_WEIGHTS.get(rubric.get("question_type"), 70)
    lines.extend(
        [
            "",
            "## 采分点证据与整体校准",
            (
                f"- 加权踩点覆盖参考值："
                f"{_format_score(float(result.get('weighted_coverage_score') or 0) * display_scale)}"
                f"/{_format_score(content_weight * display_scale)}"
            ),
            (
                f"- 综合内容分："
                f"{_format_score(float(result.get('content_score') or 0) * display_scale)}"
            ),
        ]
    )
    if result.get("holistic_adjustment_reason"):
        lines.append(f"- 整体调整理由：{result['holistic_adjustment_reason']}")
    calibration = result.get("score_calibration") or {}
    if calibration.get("adjustment"):
        lines.append(
            "- 考场锚点校准："
            f"{_format_score(calibration.get('raw_score'))}→{_format_score(score)}"
            f"（{calibration.get('policy_version') or '未标注版本'}）"
        )
    reference_count = int(
        rubric.get("selected_reference_count")
        or len(rubric.get("selected_references") or [])
    )
    reference_label = "参考答案融合说明" if reference_count > 1 else "参考答案使用说明"
    lines.append(
        f"- {reference_label}："
        f"{result.get('reference_fusion') or '按材料依据核验采分点。'}"
    )

    point_by_key = {
        point.get("point_key"): point for point in rubric.get("points", [])
    }

    def omission_material_basis(point):
        items = []
        for item in point.get("material_evidence") or []:
            if not isinstance(item, dict):
                continue
            quote = str(item.get("quote") or "").strip()
            if not quote:
                continue
            material_number = item.get("material_number")
            prefix = f"材料{material_number}：" if material_number not in {None, ""} else "材料："
            items.append(f"{prefix}“{quote}”")
            if len(items) >= 2:
                break
        if items:
            return "；".join(items)
        fallback = str(point.get("canonical_expression") or point.get("minimum_expression") or "").strip()
        return fallback or "请回到评分基准对应材料定位该点。"

    omission_matches = [
        match
        for match in result.get("point_matches", [])
        if match.get("status") in {"partial", "miss"}
    ]
    lines.extend(
        [
            "",
            "## 遗漏点分析",
            "| 遗漏的采分点 | 材料依据 | 遗漏原因 | 整体建议 |",
            "| --- | --- | --- | --- |",
        ]
    )
    for match in omission_matches:
        point = point_by_key.get(match.get("point_key")) or {}
        label = point.get("label") or match.get("point_key") or ""
        if match.get("status") == "partial":
            coverage = float(match.get("coverage_ratio") or 0)
            ratio = round(coverage * 100)
            band = "基本命中" if coverage >= 0.75 else ("部分命中" if coverage >= 0.5 else "弱命中")
            label += f"（{band}，仍缺{100 - ratio}%核心语义）"
        missing_elements = [
            str(value).strip()
            for value in (match.get("missing_elements") or [])
            if str(value or "").strip()
        ]
        omission_reason = str(match.get("omission_reason") or "").strip()
        if not omission_reason:
            if match.get("status") == "partial" and missing_elements:
                omission_reason = f"已写到部分相关内容，但缺少：{'、'.join(missing_elements)}。"
            else:
                omission_reason = str(match.get("reason") or "原答案未体现该采分点。").strip()
        suggestion = str(match.get("omission_suggestion") or "").strip()
        if not suggestion:
            minimum_expression = str(point.get("minimum_expression") or "").strip()
            if minimum_expression:
                suggestion = f"建议补写：{minimum_expression}"
            elif missing_elements:
                suggestion = f"优先补充：{'、'.join(missing_elements)}。"
            else:
                suggestion = "按材料原词补足该点的关键动作、对象、限定和结果。"
        lines.append(
            "| {label} | {basis} | {reason} | {suggestion} |".format(
                label=str(label).replace("|", "／"),
                basis=omission_material_basis(point).replace("|", "／"),
                reason=omission_reason.replace("|", "／"),
                suggestion=suggestion.replace("|", "／"),
            )
        )
    if not omission_matches:
        lines.append("| 本次未发现需补充的采分点 | — | — | 保持当前要点完整度，继续检查结构与表达。 |")

    structure = result.get("structure_review") or {}
    lines.extend(["", "## 结构与顺序审查"])
    if structure:
        lines.append(f"- 审查结论：{structure.get('verdict') or '待核查'}")
        lines.append("- 题目与材料支持的结构：" + " → ".join(structure.get("expected_outline") or ["未提供"]))
        lines.append("- 你的答案实际结构：" + " → ".join(structure.get("answer_outline") or ["未提供"]))
        lines.append(f"- 与参考答案的比较：{structure.get('comparison') or '未提供'}")
        lines.append(f"- 结构分依据：{structure.get('score_reason') or '未提供'}")
        for index, issue in enumerate(structure.get("issues") or [], start=1):
            lines.append(
                f"{index}. 原文“{issue['answer_quote']}”；依据：{issue['reference_basis']}；"
                f"问题：{issue['problem']}；调整：{issue['revision']}"
            )
        if not structure.get("issues"):
            lines.append("- 具体调整：无须因参考答案排列不同而机械调整。")
    else:
        lines.append("- 本次报告未提供可核对的结构审查，请重新批改。")

    lines.extend(["", "## 材料领读"])
    lines.extend(f"- {item}" for item in result.get("material_reading", []))
    if not result.get("material_reading"):
        lines.append("- 请按重要采分点回到对应材料原文定位信息。")

    lines.extend(["", "## 优化建议"])
    for index, item in enumerate(
        result.get("optimization_suggestions", []),
        start=1,
    ):
        lines.append(f"{index}. {item}")
    for item in result.get("personalized_findings", []):
        prefix = "重复问题" if item.get("confidence") == "recurring" else "阶段性观察"
        evidence_ids = "、".join(item.get("evidence_ids") or [])
        lines.append(f"- {prefix}：{item.get('finding')}（依据：{evidence_ids}）")
        root_cause = item.get("root_cause")
        if root_cause:
            lines.append(f"  - 深层原因：{root_cause}")
        next_step = item.get("next_step")
        if next_step:
            lines.append(f"  - 下一步：{next_step}")
    if not result.get("optimization_suggestions") and not result.get(
        "personalized_findings"
    ):
        lines.append("1. 优先修复影响最大的核心问题，再优化结构与表达。")

    lines.extend(
        [
            "",
            "## 修改版答案",
            "",
            result.get("revised_answer") or "（未生成有效修改版答案）",
        ]
    )
    return "\n".join(lines)
