import re

from .calibration import apply_score_calibration
from .common import (
    _NO_REFERENCE_CLAIMS,
    CRITERION_LABELS,
    QUESTION_TYPE_PROFILES,
    RESULT_VERSION,
    _clean,
    _round_half,
)
from .contracts import GradingResult
from .evidence_resolution import resolve_answer_evidence
from .rubric import _default_criteria

PARTIAL_COVERAGE_BANDS = (0.25, 0.5, 0.75)


def _nearest_partial_band(value):
    value = max(0.0, min(1.0, float(value)))
    if value < 0.375:
        return 0.25
    if value < 0.625:
        return 0.5
    return 0.75


_MISSING_BLOCK_SPLIT = re.compile(r"[、，,；;：:（）()“”\"'《》【】\[\] ]+")
_MISSING_INNER_SPLIT = re.compile(r"的|了|等|或|与|和|及|并|以|从|到")


def _missing_blocks(value):
    """Split a phrase into comparable semantic blocks."""
    blocks = []
    for part in _MISSING_BLOCK_SPLIT.split(str(value or "")):
        for piece in _MISSING_INNER_SPLIT.split(part):
            piece = piece.strip()
            if len(piece) >= 2:
                blocks.append(piece)
    return blocks


def _element_match_score(element, value):
    """Blended similarity between one rubric element and one omission phrase."""
    blocks = _missing_blocks(element)
    if not blocks:
        return 0.0
    value_blob = str(value or "")
    covered = 0
    for block in blocks:
        if block in value_blob:
            covered += 1
            continue
        if any(block in piece or piece in block for piece in _missing_blocks(value_blob)):
            covered += 1
    return covered / len(blocks)


def _missing_required_indexes(required, missing):
    """Map each omission phrase onto the rubric element it actually reports.

    The model may describe one element's omission with several phrases (its
    canonical wording plus the material detail it wants added) or describe an
    element with a finer phrase. Counting raw phrase totals therefore
    understates coverage; each element counts as omitted at most once.
    """
    hit_indexes = set()
    for value in missing:
        best_index = -1
        best_score = 0.0
        for index, element in enumerate(required):
            score = _element_match_score(element, value)
            if score > best_score:
                best_score = score
                best_index = index
        if best_index >= 0 and best_score >= 0.5:
            hit_indexes.add(best_index)
    return hit_indexes


def _coverage_factor(candidate, status, point=None):
    """Resolve a point to stable exam-scoring bands instead of model confidence.

    A model may describe a partial hit as 0.9 merely because the answer is
    semantically close.  For scoring, partial credit is intentionally discrete:
    weak 25%, partial 50%, basic 75%.  When the rubric exposes required
    elements, explicit missing elements take precedence over the model ratio.
    """
    if status == "hit":
        return 1.0
    if status == "miss":
        return 0.0

    point = point or {}
    required = [str(value).strip() for value in (point.get("required_elements") or []) if str(value or "").strip()]
    missing = [str(value).strip() for value in (candidate.get("missing_elements") or []) if str(value or "").strip()]
    if required and missing:
        # missing_elements 不保证与 required_elements 一一对应：模型常把
        # canonical_expression 里的细节（具体数据来源、个案名称）写成缺失项，
        # 一条细节可能只是一条必答语义的下位写法。直接按条数相减会把
        # 「只缺一处细节」夸大成「缺两点」，必须先做语义配对、每个点只计一次。
        omitted = _missing_required_indexes(required, missing)
        if omitted:
            value = max(0.0, (len(required) - len(omitted)) / len(required))
            return _nearest_partial_band(value)
    raw = candidate.get("coverage_ratio", candidate.get("coverage", 0.5))
    try:
        if isinstance(raw, str) and raw.strip().endswith("%"):
            value = float(raw.strip()[:-1]) / 100
        else:
            value = float(raw)
            if value > 1:
                value /= 100
    except (TypeError, ValueError):
        value = 0.5
    return _nearest_partial_band(value)


def _point_based_content_cap(matches, content_weight):
    """Apply a high-score ceiling only when important required points are absent."""
    important = [
        match
        for match in matches
        if match.get("coverage_role") == "required"
        and match.get("importance") in {"critical", "major"}
    ]
    critical_misses = [
        match for match in important
        if match.get("importance") == "critical" and match.get("status") == "miss"
    ]
    severe_omissions = [
        match for match in important
        if match.get("status") == "miss"
        or (match.get("status") == "partial" and float(match.get("coverage_ratio") or 0) <= 0.25)
    ]
    ratio = 1.0
    reasons = []
    if critical_misses:
        ratio = min(ratio, 0.85)
        reasons.append("critical_point_missed")
    if len(severe_omissions) >= 2:
        ratio = min(ratio, 0.80)
        reasons.append("multiple_major_or_critical_omissions")
    if ratio >= 1.0:
        return {}
    return {
        "ratio": ratio,
        "max_score": round(float(content_weight) * ratio, 1),
        "reasons": reasons,
    }


def _consistent_point_reason(status, reason, point):
    """Keep the displayed judgement about the user's answer aligned with status."""
    reason = _clean(reason, 300)
    label = _clean(point.get("label") or point.get("canonical_expression"), 80) or "该采分点"
    positive_claims = (
        "完整覆盖",
        "充分覆盖",
        "完全覆盖",
        "已经覆盖",
        "已覆盖",
        "明确体现",
        "准确体现",
        "完整体现",
        "已命中",
        "完全命中",
        "符合采分点",
        "与采分点一致",
    )
    if status == "miss" and any(claim in reason for claim in positive_claims):
        return f"原答案未提供能够证明“{label}”的明确表述；判断仅依据本次作答原文。"
    if status == "miss" and not reason:
        return f"原答案未出现“{label}”对应的明确表述。"
    return reason


def _validated_reference_fusion(value, rubric):
    text = _clean(value, 600)
    references = rubric.get("selected_references") or []
    count = int(rubric.get("selected_reference_count") or len(references))
    if count <= 0:
        return text
    organizations = [str(item.get("organization") or "").strip() for item in references if item.get("organization")]
    organization_text = "、".join(organizations)
    if count == 1:
        source = f"（{organization_text}）" if organization_text else ""
        return (
            f"本题仅有 1 份机构参考答案{source}，用于辅助核对采分点；"
            "评分同时依据题干任务与材料原文，不把单份答案视为唯一标准。"
        )
    prefix = f"本题已纳入 {count} 份机构参考答案"
    if organization_text:
        prefix += f"（{organization_text}）"
    prefix += "进行融合。"
    if any(claim in text for claim in _NO_REFERENCE_CLAIMS):
        text = "系统已结合机构答案全文与本题材料核验共性核心点和差异补充点。"
    if text.startswith(("本题已纳入", "本题纳入")):
        _, separator, remainder = text.partition("。")
        text = remainder.strip() if separator else ""
    return prefix + (text or "系统已结合机构答案全文与本题材料核验采分点。")


def _validated_structure_review(value, answer_text, required=False):
    if not isinstance(value, dict):
        if required and str(answer_text or "").strip():
            raise ValueError("批改结果缺少独立的答案结构审查")
        return {}
    verdict = str(value.get("verdict") or "").strip()
    if verdict not in {"合理", "需局部调整", "需重组"}:
        raise ValueError("结构审查结论无效")
    expected = [_clean(item, 180) for item in (value.get("expected_outline") or []) if _clean(item)][:8]
    actual = [_clean(item, 180) for item in (value.get("answer_outline") or []) if _clean(item)][:8]
    comparison = _clean(value.get("comparison"), 500)
    score_reason = _clean(value.get("score_reason"), 300)
    issues = []
    for item in value.get("issues") or []:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("answer_quote") or "").strip()
        basis = _clean(item.get("reference_basis"), 240)
        problem = _clean(item.get("problem"), 240)
        revision = _clean(item.get("revision"), 240)
        if quote and quote in answer_text and basis and problem and revision:
            issues.append({
                "answer_quote": quote[:180],
                "reference_basis": basis,
                "problem": problem,
                "revision": revision,
            })
    if required and str(answer_text or "").strip():
        if not expected or not actual or not comparison or not score_reason:
            raise ValueError("结构审查缺少参考结构、原答案结构或比较依据")
        if verdict != "合理" and not issues:
            raise ValueError("结构问题缺少可核对的答案原文与修改动作")
    return {
        "expected_outline": expected,
        "answer_outline": actual,
        "comparison": comparison,
        "verdict": verdict,
        "issues": issues[:6],
        "score_reason": score_reason,
    }


def validate_grading_result(
    raw,
    rubric,
    answer_text,
    evidence,
    report_feedback=None,
    calibration_policy=None,
    reviewed=False,
    require_structure_review=False,
) -> GradingResult:
    """Validate model evidence while preserving its holistic dimension scores."""
    if isinstance(raw, dict) and isinstance(raw.get("evaluation"), dict):
        raw = raw["evaluation"]
    if not isinstance(raw, dict):
        raise ValueError("批改结果不是 JSON 对象")
    structure_review = _validated_structure_review(
        raw.get("structure_review"), answer_text, required=require_structure_review
    )

    points = sorted(
        [point for point in rubric.get("points", []) if isinstance(point, dict) and point.get("point_key")],
        key=lambda point: (-float(point.get("weight") or 0), point.get("point_key") or ""),
    )
    point_by_key = {point["point_key"]: point for point in points}
    candidates = {
        str(item.get("point_key") or ""): item
        for item in (raw.get("point_matches") or [])
        if isinstance(item, dict) and item.get("point_key")
    }
    matches = []
    for point in points:
        candidate = candidates.get(point["point_key"])
        if candidate is None and point.get("source_point_key"):
            candidate = candidates.get(point["source_point_key"])
        candidate = candidate or {}
        status = candidate.get("status")
        if status not in {"hit", "partial", "miss"}:
            status = "miss"
        quote = _clean(candidate.get("answer_quote"), 240)
        resolution = (
            resolve_answer_evidence(quote, answer_text)
            if status in {"hit", "partial"}
            else {"status": "not_required", "quote": "", "spans": []}
        )
        if resolution["status"] == "resolved":
            quote = _clean(resolution.get("quote"), 240)
        coverage = _coverage_factor(candidate, status, point)
        coverage_role = point.get("coverage_role") or (
            "required" if point.get("required_for_full_score", True) else "bonus"
        )
        missing_elements = [
            _clean(value, 100)
            for value in (candidate.get("missing_elements") or [])
            if _clean(value)
        ][:6]
        point_reason = _consistent_point_reason(status, candidate.get("reason"), point)
        omission_reason = _clean(candidate.get("omission_reason"), 300)
        omission_suggestion = _clean(candidate.get("omission_suggestion"), 300)
        if status in {"partial", "miss"}:
            if not omission_reason:
                if status == "partial" and missing_elements:
                    omission_reason = f"已写到部分相关内容，但缺少：{'、'.join(missing_elements)}。"
                else:
                    omission_reason = point_reason
            if not omission_suggestion:
                minimum_expression = _clean(point.get("minimum_expression"), 220)
                if minimum_expression:
                    omission_suggestion = f"建议补写：{minimum_expression}"
                elif missing_elements:
                    omission_suggestion = f"优先补充：{'、'.join(missing_elements)}。"
                else:
                    omission_suggestion = f"围绕“{_clean(point.get('label') or point.get('canonical_expression'), 80)}”补足材料中的关键动作、对象和结果。"
        else:
            omission_reason = ""
            omission_suggestion = ""
        matches.append(
            {
                "point_key": point["point_key"],
                "status": status,
                "coverage_ratio": coverage,
                "answer_quote": quote,
                "reason": point_reason,
                "weight": round(float(point.get("weight") or 0), 3),
                "importance": point.get("importance") or "supporting",
                "coverage_role": coverage_role,
                "evidence_status": resolution["status"],
                "evidence_spans": resolution.get("spans") or [],
                "confidence": max(0.0, min(1.0, float(candidate.get("confidence") or 0.7))),
                "missing_elements": missing_elements,
                "omission_reason": omission_reason,
                "omission_suggestion": omission_suggestion,
            }
        )

    content_weight = float(
        (QUESTION_TYPE_PROFILES.get(rubric.get("question_type")) or QUESTION_TYPE_PROFILES["归纳概括"])["content"]
    )
    scoring_mode = rubric.get("scoring_mode") or (
        "holistic_essay" if rubric.get("question_type") == "综合写作" else "point_based"
    )
    weighted_coverage = round(
        sum(
            match["weight"] * match["coverage_ratio"]
            for match in matches
            if match.get("coverage_role") in {"required", "alternative"}
            and not (
                scoring_mode == "point_based"
                and match.get("status") in {"hit", "partial"}
                and match.get("evidence_status") == "unresolved"
            )
        ),
        1,
    )
    dimensions = rubric.get("dimensions") or rubric.get("criteria") or _default_criteria(rubric.get("question_type"))
    dimension_by_name = {
        item["dimension"]: {
            "dimension": item["dimension"],
            "label": CRITERION_LABELS.get(item["dimension"], item["dimension"]),
            "max_score": float(item.get("weight") or 0),
        }
        for item in dimensions
        if isinstance(item, dict) and item.get("dimension")
    }
    if not dimension_by_name or round(sum(item["max_score"] for item in dimension_by_name.values()), 4) != 100:
        raise ValueError("评分维度满分未闭合到100分")

    raw_dimensions = {
        str(item.get("dimension") or ""): item
        for item in (raw.get("dimension_scores") or [])
        if isinstance(item, dict) and item.get("dimension")
    }
    normalized_dimensions = []
    blank_answer = not str(answer_text or "").strip()
    for dimension, definition in dimension_by_name.items():
        candidate = raw_dimensions.get(dimension)
        if candidate is None and not blank_answer:
            raise ValueError(f"批改结果缺少 {dimension} 维度得分")
        try:
            score = 0.0 if blank_answer else float(candidate.get("score"))
        except (TypeError, ValueError):
            raise ValueError(f"{dimension} 维度得分不是有效数字")
        if score != score or score < 0 or score > definition["max_score"]:
            raise ValueError(f"{dimension} 维度得分超出有效范围")
        normalized_dimensions.append(
            {
                **definition,
                "score": round(score, 1),
                "reason": "空白答案。" if blank_answer else _clean(candidate.get("reason"), 300),
            }
        )

    content_score = next(
        (item["score"] for item in normalized_dimensions if item["dimension"] == "content"),
        0.0,
    )
    adjustment_reason = _clean(raw.get("holistic_adjustment_reason"), 360)
    content_score_cap = {}
    if not blank_answer and scoring_mode == "point_based":
        content_score_cap = _point_based_content_cap(matches, content_weight)
        content_score = min(content_weight, weighted_coverage)
        if content_score_cap:
            content_score = min(content_score, float(content_score_cap["max_score"]))
        content_reason = (
            "内容分按固定采分点加权：命中100%、基本命中75%、部分命中50%、弱命中25%、未命中0%。"
        )
        if content_score_cap:
            content_reason += "存在关键遗漏，已触发高分上限。"
        for dimension in normalized_dimensions:
            if dimension["dimension"] == "content":
                dimension["score"] = round(content_score, 1)
                dimension["reason"] = _clean(content_reason, 300)
                break

    structure_score_cap = {}
    if structure_review.get("score_reason"):
        for dimension in normalized_dimensions:
            if dimension["dimension"] == "structure":
                dimension["reason"] = structure_review["score_reason"]
                break
    if structure_review.get("issues") and structure_review.get("verdict") in {"需局部调整", "需重组"}:
        ratio = 0.7 if structure_review["verdict"] == "需局部调整" else 0.5
        for dimension in normalized_dimensions:
            if dimension["dimension"] == "structure":
                cap = round(dimension["max_score"] * ratio, 1)
                if dimension["score"] > cap:
                    dimension["score"] = cap
                    dimension["reason"] = _clean(
                        f"{structure_review['score_reason']}；结构审查判定{structure_review['verdict']}，结构分按证据上限校正。",
                        300,
                    )
                structure_score_cap = {"max_score": cap, "verdict": structure_review["verdict"]}
                break

    valid_evidence_ids = {card.get("evidence_id") for card in evidence if card.get("role") == "personalization"}
    history_stable = (
        len(
            {
                card.get("attempt_id")
                for card in evidence
                if card.get("role") == "personalization" and card.get("attempt_id")
            }
        )
        >= 2
    )
    personalized = []
    for finding in raw.get("personalized_findings") or []:
        if not isinstance(finding, dict):
            continue
        evidence_ids = [value for value in (finding.get("evidence_ids") or []) if value in valid_evidence_ids]
        if not evidence_ids:
            continue
        personalized.append(
            {
                "finding": _clean(finding.get("finding"), 220),
                "root_cause": _clean(finding.get("root_cause"), 220),
                "next_step": _clean(finding.get("next_step"), 220),
                "evidence_ids": evidence_ids,
                "confidence": ("recurring" if finding.get("confidence") == "recurring" and history_stable else "stage"),
            }
        )

    annotation_severities = {
        "good": "positive",
        "polish": "low",
        "change": "medium",
        "delete": "high",
        "add": "high",
        "critical": "critical",
    }
    valid_severities = {"positive", "low", "medium", "high", "critical"}
    annotations = []
    for item in raw.get("annotations") or []:
        if not isinstance(item, dict):
            continue
        kind = (
            item.get("kind")
            if item.get("kind") in {"good", "polish", "change", "delete", "add", "critical"}
            else "change"
        )
        quote = _clean(item.get("quote"), 180)
        anchor = _clean(item.get("anchor"), 180)
        if kind != "add" and (not quote or quote not in answer_text):
            continue
        if kind == "add" and (not anchor or anchor not in answer_text):
            continue
        severity = item.get("severity")
        if severity not in valid_severities:
            severity = annotation_severities[kind]
        annotations.append(
            {
                "kind": kind,
                "quote": quote,
                "replacement": _clean(item.get("replacement"), 200),
                "reason": _clean(item.get("reason"), 240),
                "point_key": item.get("point_key") if item.get("point_key") in point_by_key else "",
                "severity": severity,
                "anchor": anchor,
            }
        )

    raw_score = round(sum(item["score"] for item in normalized_dimensions), 1)
    score, score_calibration = apply_score_calibration(raw_score, calibration_policy)
    if blank_answer:
        score = 0.0
        score_calibration["adjustment"] = 0.0
    if raw_score > 0 and score != raw_score:
        remaining = round(score - raw_score, 1)
        if remaining < 0:
            total = sum(item["score"] for item in normalized_dimensions) or 1
            for dimension in normalized_dimensions:
                share = remaining * dimension["score"] / total
                dimension["score"] = round(max(0.0, dimension["score"] + share), 1)
        else:
            positive_targets = [
                item for item in normalized_dimensions
                if not (content_score_cap and item["dimension"] == "content")
            ]
            headroom = sum(item["max_score"] - item["score"] for item in positive_targets) or 1
            for dimension in positive_targets:
                share = remaining * (dimension["max_score"] - dimension["score"]) / headroom
                dimension["score"] = round(min(dimension["max_score"], dimension["score"] + share), 1)
        difference = round(score - sum(item["score"] for item in normalized_dimensions), 1)
        if difference:
            reconciliation_targets = [
                dimension
                for dimension in normalized_dimensions
                if not (difference > 0 and content_score_cap and dimension["dimension"] == "content")
            ]
            reconciled = False
            for dimension in reconciliation_targets:
                candidate = round(dimension["score"] + difference, 1)
                if 0 <= candidate <= dimension["max_score"]:
                    dimension["score"] = candidate
                    reconciled = True
                    break
            if not reconciled:
                # Never violate a deterministic content ceiling merely to match
                # a positive calibration offset.  The dimensions remain the
                # auditable source of truth, so collapse the final score to them.
                score = round(sum(item["score"] for item in normalized_dimensions), 1)
                score_calibration["adjustment"] = round(score - raw_score, 1)
        content_score = next(
            (item["score"] for item in normalized_dimensions if item["dimension"] == "content"),
            0.0,
        )
    if structure_score_cap:
        for dimension in normalized_dimensions:
            if dimension["dimension"] == "structure" and dimension["score"] > structure_score_cap["max_score"]:
                dimension["score"] = structure_score_cap["max_score"]
                score = round(sum(item["score"] for item in normalized_dimensions), 1)
                score_calibration["adjustment"] = round(score - raw_score, 1)
                break

    review_reasons = []
    for match in matches:
        if (
            match.get("status") in {"hit", "partial"}
            and match.get("evidence_status") == "unresolved"
            and match.get("coverage_role") == "required"
        ):
            review_reasons.append(f"unresolved_required_evidence:{match['point_key']}")
    if scoring_mode == "holistic_essay" and abs(content_score - weighted_coverage) > content_weight * 0.35:
        review_reasons.append("essay_content_diagnostic_divergence")
    essay_diagnostic = " ".join(item.get("reason") or "" for item in normalized_dimensions)
    if (
        scoring_mode == "holistic_essay"
        and raw_score >= 70
        and re.search(
            r"(?:关键)?事实(?:偏差|错误)|材料误读|论证(?:空泛|薄弱|不足)|未能结合.{0,12}(?:材料|案例)|主要(?:部分|段落).{0,8}(?:缺少|不足)",
            essay_diagnostic,
        )
    ):
        review_reasons.append("essay_high_band_diagnostic_conflict")
    score_status = "provisional" if review_reasons else "valid"
    review = {
        "triggered": bool(review_reasons),
        "reasons": review_reasons,
        "decision": "confirmed" if reviewed and not review_reasons else ("required" if review_reasons else "not_needed"),
    }
    display_max_score = float(rubric.get("display_max_score") or 100)
    display_score = _round_half(score * display_max_score / 100)
    display_scale = display_max_score / 100
    for dimension in normalized_dimensions:
        dimension["display_max_score"] = round(dimension["max_score"] * display_scale, 2)
        dimension["display_score"] = round(dimension["score"] * display_scale, 2)

    return {
        "schema_version": RESULT_VERSION,
        "score_status": score_status,
        "point_matches": matches,
        "dimension_scores": normalized_dimensions,
        # Kept for readers of older result payloads.
        "criteria_matches": [
            {
                "criterion_id": f"{item['dimension']}-1",
                "dimension": item["dimension"],
                "weight": item["max_score"],
                "awarded": item["score"],
                "reason": item["reason"],
            }
            for item in normalized_dimensions
        ],
        "weighted_coverage_score": weighted_coverage,
        "holistic_adjustment_reason": adjustment_reason,
        "annotations": annotations[:12],
        "reference_fusion": _validated_reference_fusion(raw.get("reference_fusion"), rubric),
        "structure_review": structure_review,
        "structure_score_cap": structure_score_cap,
        "material_reading": [_clean(value, 360) for value in (raw.get("material_reading") or []) if _clean(value)][:12],
        "optimization_suggestions": [
            _clean(value, 300) for value in (raw.get("optimization_suggestions") or []) if _clean(value)
        ][:10],
        "personalized_findings": personalized[:6],
        "overall_summary": _clean(raw.get("overall_summary"), 360)
        or ("空白答案，未完成作答。" if blank_answer else "请结合维度得分与遗漏点分析查看。"),
        "summary": raw.get("summary") if isinstance(raw.get("summary"), dict) else {},
        "revised_answer": str(raw.get("revised_answer") or "").strip(),
        "score": score,
        "display_score": display_score,
        "display_max_score": int(display_max_score) if display_max_score.is_integer() else display_max_score,
        "score_is_estimated": bool(rubric.get("score_is_estimated")),
        "content_score": round(content_score, 1),
        "content_score_cap": content_score_cap,
        "score_calibration": score_calibration,
        "review": review,
        "validation_errors": review_reasons,
    }
