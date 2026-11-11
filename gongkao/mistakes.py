from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from difflib import SequenceMatcher

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}
ERROR_REASON_RE = re.compile(
    r"错|误|纠正|不准确|不当|不规范|语病|语义不通|语义不完整|无法衔接|"
    r"主体.{0,8}(混|错|不清)|事实|偏离|颠倒|混淆|混乱|混入|错位|搭配|"
    r"漏字|漏词|多写|写成|误列|误归|应改为|应将.{0,20}改为|不成立|越权|"
    r"归类|分类.{0,8}(不准|错误|交叉)|存在交叉|材料未明确|明显用词|"
    r"规范用词|表述错误|地点.{0,8}(错误|不准)|施动关系"
)
TYPO_REASON_RE = re.compile(
    r"错别字|明显错字|严重错字|错字|打字|笔误|漏字|漏词|大小写|拼写|"
    r"错词|明显错词|严重错词|用词笔误|明显用词错误"
)
SUBSTANTIVE_ERROR_RE = re.compile(
    r"主体|对象|地点|数量|事实|无依据|增写|误列|误归|颠倒|混淆|错位|"
    r"因果|关系|机制|越权|归类|分类|范围|材料未明确|材料原意"
)


def _json_value(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed


def _clip(value, limit=260):
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _material_evidence(point):
    evidence = _json_value((point or {}).get("material_evidence"), [])
    rows = []
    for item in evidence if isinstance(evidence, list) else []:
        if not isinstance(item, dict):
            continue
        quote = _clip(item.get("quote"), 220)
        if not quote:
            continue
        rows.append(
            {
                "material_number": item.get("material_number") or "",
                "quote": quote,
            }
        )
        if len(rows) >= 3:
            break
    return rows


def _minor_text_edit(quote, replacement):
    left = re.sub(r"\s+", "", str(quote or ""))
    right = re.sub(r"\s+", "", str(replacement or ""))
    if not left or not right or max(len(left), len(right)) > 48:
        return False
    if abs(len(left) - len(right)) > 2:
        return False
    return SequenceMatcher(None, left, right).ratio() >= 0.86


def _annotation_is_typo_only(annotation):
    if not isinstance(annotation, dict):
        return False
    reason = str(annotation.get("reason") or "")
    if SUBSTANTIVE_ERROR_RE.search(reason):
        return False
    if TYPO_REASON_RE.search(reason):
        return True
    if _minor_text_edit(annotation.get("quote"), annotation.get("replacement")):
        return bool(re.search(r"搭配错误|语义不完整|表述不通|用词|规范表述|应改为", reason))
    return False


def _annotation_is_error(annotation):
    if not isinstance(annotation, dict):
        return False
    if annotation.get("severity") in {"positive", ""} or _annotation_is_typo_only(annotation):
        return False
    kind = str(annotation.get("kind") or "")
    severity = str(annotation.get("severity") or "low")
    reason = str(annotation.get("reason") or "")
    if severity in {"medium", "high", "critical"} or kind == "critical":
        if kind not in {"change", "polish", "critical"}:
            return False
        return bool(annotation.get("quote") or annotation.get("replacement")) and bool(
            ERROR_REASON_RE.search(reason)
        )
    return False


def _severity_for_point(match, point, annotations):
    annotation_level = max(
        (SEVERITY_ORDER.get(str(item.get("severity") or "low"), 1) for item in annotations if _annotation_is_error(item)),
        default=0,
    )
    if annotation_level >= 3:
        return "high"

    status = str(match.get("status") or "")
    coverage = _as_float(match.get("coverage_ratio"), 0.0)
    importance = str(match.get("importance") or (point or {}).get("importance") or "major")
    weight = _as_float(match.get("weight"), _as_float((point or {}).get("weight"), 0.0))
    gap = max(0.0, weight * (1.0 - coverage))

    if status == "miss" and importance in {"critical", "major"}:
        return "high"
    if status == "miss":
        return "medium"
    if importance == "critical" and coverage <= 0.6:
        return "high"
    if gap >= 4.5:
        return "high"
    return "medium"


def _is_severe(match, point, annotations, severity):
    # “严重扣分”是错题本里的优先级标签，不应和“完全遗漏”画等号。
    # 支撑性/补充性要点即使 miss 也仍会作为遗漏收录，但只有达到 high
    # 影响等级时才进入“严重扣分”筛选，避免把结尾展望、作品举例等补充点
    # 和核心任务失败混在一起。
    return severity == "high"


def _best_replacement(annotations):
    candidates = []
    for item in annotations:
        if _annotation_is_typo_only(item):
            continue
        replacement = _clip(item.get("replacement"), 320)
        if not replacement:
            continue
        candidates.append(
            (
                SEVERITY_ORDER.get(str(item.get("severity") or "low"), 1),
                1 if item.get("kind") == "change" else 0,
                replacement,
            )
        )
    if not candidates:
        return ""
    return max(candidates)[2]


def _point_lookup(rubric):
    points = rubric.get("points") if isinstance(rubric, dict) else []
    mapping = {}
    for point in points if isinstance(points, list) else []:
        if not isinstance(point, dict):
            continue
        key = str(point.get("point_key") or "").strip()
        if key:
            mapping[key] = point
    return mapping


def _row_dict(row):
    return {key: row[key] for key in row.keys()}


def _section(markdown, heading):
    pattern = re.compile(
        rf"(?ms)^##\s*{re.escape(heading)}\s*$\n?(.*?)(?=^##\s+|\Z)"
    )
    match = pattern.search(str(markdown or ""))
    return match.group(1).strip() if match else ""


def _legacy_revised_answer(report_text):
    text = _section(report_text, "修改版答案")
    lines = [line for line in text.splitlines() if line.strip()]
    while lines and re.match(r"^\s*(?:[-*]\s*)?(?:估算|实际)?字数\s*[:：]", lines[0]):
        lines.pop(0)
    return "\n".join(lines).strip()


def _legacy_scoring_points(report_text):
    section = _section(report_text, "得分点清单")
    points = []
    for line in section.splitlines():
        match = re.match(r"^\s*(?:[-*]\s*)?(\d+)\.\s*(.+?)\s*[:：]\s*(.+)$", line)
        if not match:
            continue
        label = match.group(2).strip()
        content = match.group(3).strip()
        points.append({"label": label, "content": content})
    return points


def _legacy_table_rows(report_text):
    section = _section(report_text, "踩点对比")
    rows = []
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5 or cells[0] in {"采分点", "---"} or set(cells[0]) <= {"-", ":"}:
            continue
        rows.append(
            {
                "label": cells[0],
                "status": cells[1],
                "answer_quote": cells[2],
                "judgement": cells[3],
                "suggestion": cells[4],
            }
        )
    return rows


def _normalize_legacy_label(value):
    text = re.sub(r"[（(].*?[）)]", "", str(value or ""))
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", text).lower()


def _match_legacy_point(label, points):
    target = _normalize_legacy_label(label)
    if not target:
        return None
    best = None
    best_score = 0.0
    for point in points:
        candidate = _normalize_legacy_label(point.get("label"))
        if not candidate:
            continue
        if target in candidate or candidate in target:
            score = 1.0 + min(len(target), len(candidate)) / 1000
        else:
            score = SequenceMatcher(None, target, candidate).ratio()
        if score > best_score:
            best, best_score = point, score
    return best if best_score >= 0.5 else None


def _legacy_evidence_basis(point):
    content = str((point or {}).get("content") or "")
    basis_match = re.search(
        r"材料依据(?:为|[:：])\s*(.+?)(?=；\s*规范表达|。\s*规范表达|$)",
        content,
    )
    return basis_match.group(1).strip() if basis_match else content


def _candidate_evidence_phrases(basis):
    text = str(basis or "")
    quoted = [
        value.strip()
        for value in re.findall(r"[“\"]([^”\"]{2,120})[”\"]|[‘']([^’']{2,120})[’']", text)
        for value in (value if isinstance(value, tuple) else (value,))
        if value and value.strip()
    ]
    # The regex above yields tuples because it supports both Chinese quote
    # pairs. Flatten defensively and retain only meaningful source phrases.
    flat = []
    for value in quoted:
        if isinstance(value, tuple):
            flat.extend(part.strip() for part in value if part and part.strip())
        elif value:
            flat.append(value)
    segments = [
        part.strip()
        for part in re.split(r"[；;，,、+（）()：:]", text)
        if len(part.strip()) >= 5
    ]
    result = []
    for phrase in [*flat, *segments]:
        phrase = re.sub(r"^(?:共性|差异补充|差异|材料第\d+段|材料依据为)\s*", "", phrase).strip()
        if phrase and phrase not in result:
            result.append(phrase)
    return result


def _exact_material_context(content, phrase, limit=220):
    text = str(content or "")
    index = text.find(phrase)
    if index < 0:
        return ""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    for paragraph in paragraphs:
        if phrase in paragraph:
            if len(paragraph) <= limit:
                return paragraph
            local_index = paragraph.find(phrase)
            before = max(0, local_index - 70)
            after = min(len(paragraph), local_index + len(phrase) + 110)
            snippet = paragraph[before:after].strip()
            if before:
                snippet = "…" + snippet
            if after < len(paragraph):
                snippet += "…"
            return snippet
    before = max(0, index - 70)
    after = min(len(text), index + len(phrase) + 110)
    return ("…" if before else "") + text[before:after].strip() + ("…" if after < len(text) else "")


def _compact_text_with_positions(value):
    text = str(value or "")
    compact = []
    positions = []
    for index, character in enumerate(text):
        if character.isspace():
            continue
        compact.append(character)
        positions.append(index)
    return "".join(compact), positions


def _material_sources(conn, row):
    sources = []
    paper_id = row.get("paper_id")
    if paper_id:
        sources = [
            {
                "material_number": material["material_number"],
                "content": str(material["content"] or ""),
            }
            for material in conn.execute(
                "SELECT material_number, content FROM paper_materials WHERE paper_id = ? ORDER BY material_number",
                (paper_id,),
            )
        ]
    if not sources and str(row.get("question_materials") or "").strip():
        sources = [
            {
                "material_number": "",
                "content": str(row.get("question_materials") or ""),
            }
        ]
    return sources


def _find_source_range(content, quote):
    text = str(content or "")
    phrase = str(quote or "").strip()
    if not text or not phrase:
        return None
    exact = text.find(phrase)
    if exact >= 0:
        return exact, exact + len(phrase)

    compact_text, positions = _compact_text_with_positions(text)
    compact_phrase, _ = _compact_text_with_positions(phrase)
    if not compact_phrase:
        return None
    compact_index = compact_text.find(compact_phrase)
    if compact_index < 0:
        return None
    start = positions[compact_index]
    end = positions[compact_index + len(compact_phrase) - 1] + 1
    return start, end


def _resolved_source_evidence(sources, quote, preferred_number=""):
    if not quote:
        return None
    preferred = str(preferred_number or "")
    ordered = sorted(
        sources,
        key=lambda source: 0 if str(source.get("material_number") or "") == preferred else 1,
    )
    for source in ordered:
        found = _find_source_range(source.get("content"), quote)
        if not found:
            continue
        start, end = found
        exact_phrase = str(source.get("content") or "")[start:end]
        context = _exact_material_context(source.get("content"), exact_phrase, 220)
        return {
            "material_number": source.get("material_number") or "",
            "quote": _clip(context or exact_phrase, 220),
        }
    return None


def _verified_material_evidence(point, sources):
    raw = _json_value((point or {}).get("material_evidence"), [])
    resolved = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "").strip()
        evidence = _resolved_source_evidence(
            sources,
            quote,
            item.get("material_number") or "",
        )
        if not evidence:
            # A model may slightly paraphrase a nominal “quote”.  Never show
            # that paraphrase as source text.  Try only longer exact fragments
            # from it, then display the real surrounding material if located.
            for phrase in _candidate_evidence_phrases(quote):
                if len("".join(phrase.split())) < 6:
                    continue
                evidence = _resolved_source_evidence(
                    sources,
                    phrase,
                    item.get("material_number") or "",
                )
                if evidence:
                    break
        if evidence and evidence not in resolved:
            resolved.append(evidence)
        if len(resolved) >= 3:
            break
    return resolved


def _answer_excerpts(match, answer_snapshot):
    answer = str(answer_snapshot or "")
    rows = []
    spans = match.get("evidence_spans") if isinstance(match, dict) else []
    for span in spans if isinstance(spans, list) else []:
        if not isinstance(span, dict):
            continue
        text = str(span.get("text") or "").strip()
        if not text or text not in answer:
            continue
        clipped = _clip(text, 240)
        if clipped and clipped not in rows:
            rows.append(clipped)
        if len(rows) >= 3:
            break
    if rows:
        return rows

    # Legacy-compatible fallback for a single continuous quote.  Deliberately
    # reject model-created “A……B” composites: the notebook must show what the
    # user actually wrote, not a synthetic summary of several answer spans.
    quote = str((match or {}).get("answer_quote") or "").strip()
    if quote and "……" not in quote and "..." not in quote and quote in answer:
        return [_clip(quote, 240)]
    return []


def _legacy_answer_excerpts(answer_quote, answer_snapshot):
    quote = str(answer_quote or "").strip().strip("“”\"")
    answer = str(answer_snapshot or "")
    if not quote or quote in {"未体现", "未写", "无", "未提及"}:
        return []
    if quote in answer:
        return [_clip(quote, 240)]
    parts = [part.strip() for part in re.split(r"…{2,}|\.{3,}", quote) if part.strip()]
    exact_parts = [_clip(part, 220) for part in parts if len(part) >= 4 and part in answer]
    return exact_parts[:3]


def _annotation_material_evidence(annotation, sources):
    """Bind an unscoped hard error to real source text, conservatively.

    Only an exact correction phrase (allowing whitespace differences) is used.
    We intentionally do not infer source text from a generic annotation reason,
    because structural/style feedback may have no single material excerpt.
    """
    replacement = str((annotation or {}).get("replacement") or "").strip()
    if len("".join(replacement.split())) < 4:
        return []
    evidence = _resolved_source_evidence(sources, replacement)
    if evidence:
        return [evidence]
    # For a longer correction sentence, an exact quoted/source phrase inside
    # it may still be sufficient, but keep the locator substantial.
    for phrase in _candidate_evidence_phrases(replacement):
        if len("".join(phrase.split())) < 6:
            continue
        evidence = _resolved_source_evidence(sources, phrase)
        if evidence:
            return [evidence]
    return []


def _legacy_material_evidence(conn, paper_id, question_materials, point):
    sources = []
    if paper_id:
        sources = [
            {"material_number": row["material_number"], "content": row["content"]}
            for row in conn.execute(
                "SELECT material_number, content FROM paper_materials WHERE paper_id = ? ORDER BY material_number",
                (paper_id,),
            )
        ]
    if not sources and str(question_materials or "").strip():
        sources = [{"material_number": "", "content": question_materials}]
    if not sources:
        return []

    phrases = _candidate_evidence_phrases(_legacy_evidence_basis(point))
    found = []
    for phrase in phrases:
        candidates = [phrase]
        compact = "".join(phrase.split())
        if len(compact) >= 8:
            # If the legacy rubric paraphrased a longer phrase, find a shorter
            # exact n-gram in the real material instead of displaying the
            # rubric paraphrase as though it were source text.
            for width in range(min(24, len(compact)), 5, -1):
                candidates.extend(compact[start : start + width] for start in range(0, len(compact) - width + 1))
                if len(candidates) > 160:
                    break
        matched = False
        for candidate in candidates:
            if len(candidate) < 4:
                continue
            for source in sources:
                if candidate not in source["content"]:
                    continue
                snippet = _exact_material_context(source["content"], candidate)
                if snippet and snippet not in {item["quote"] for item in found}:
                    found.append(
                        {
                            "material_number": source["material_number"],
                            "quote": _clip(snippet, 220),
                        }
                    )
                matched = True
                break
            if matched:
                break
        if len(found) >= 3:
            break
    return found


def _legacy_error_signal(row):
    text = " ".join(
        str(row.get(key) or "") for key in ("judgement", "suggestion")
    )
    if TYPO_REASON_RE.search(text) and not SUBSTANTIVE_ERROR_RE.search(text):
        return False
    return bool(re.search(r"严重误写|误写|错误|语病|偏离材料|不准确|应改为|必须.*改", text))


def _legacy_item(conn, row, report_text, table_row, scoring_point):
    evidence = _legacy_material_evidence(
        conn,
        row.get("paper_id"),
        row.get("question_materials"),
        scoring_point,
    )
    if not evidence:
        return None
    status_text = str(table_row.get("status") or "")
    omission = "部分命中" in status_text or "未命中" in status_text
    error = _legacy_error_signal(table_row)
    if not omission and not error:
        return None
    status = "miss" if "未命中" in status_text else ("partial" if omission else "error")
    coverage = 0.0 if status == "miss" else (0.5 if status == "partial" else 1.0)
    judgement = str(table_row.get("judgement") or "")
    suggestion = str(table_row.get("suggestion") or "")
    severe = bool(
        status == "miss"
        and re.search(r"核心|完全缺失|实质性|零分|不得分|重点", judgement + suggestion)
    ) or bool(re.search(r"严重|硬伤", judgement + suggestion))
    severity = "high" if severe else "medium"
    tags = set()
    if omission:
        tags.add("omission")
    if error:
        tags.add("error")
    if severe:
        tags.add("severe")
    point_label = str(table_row.get("label") or scoring_point.get("label") or "历史错点").strip()
    digest = hashlib.sha1(point_label.encode("utf-8")).hexdigest()[:14]
    score_match = re.search(r"总分\s*[:：]\s*([0-9.]+)\s*/\s*([0-9.]+)", report_text)
    core_match = re.search(r"核心判断\s*[:：]\s*(.+)", report_text)
    correction = suggestion
    if not correction:
        canonical = re.search(r"规范表达\s*[:：]\s*(.+)$", str(scoring_point.get("content") or ""))
        correction = canonical.group(1).strip() if canonical else ""
    answer_snapshot = str(row.get("answer_text") or "").strip()
    answer_excerpts = _legacy_answer_excerpts(table_row.get("answer_quote"), answer_snapshot)
    return {
        "issue_key": f"attempt:{row['attempt_id']}:legacy:{digest}",
        "report_id": row["report_id"],
        "attempt_id": row["attempt_id"],
        "question_id": row["question_id"],
        "question_title": row["question_title"],
        "paper_name": row["paper_name"],
        "question_number": row["question_number"],
        "question_type": row["question_type"],
        "word_limit": row["word_limit"],
        "year": row["year"],
        "region": row["region"],
        "exam_type": row["exam_type"],
        "created_at": row["created_at"],
        "provider": row["provider"],
        "model": row["model"],
        "point_key": f"legacy-{digest}",
        "label": _clip(point_label, 80),
        "status": status,
        "status_label": "完全遗漏" if status == "miss" else ("部分遗漏 · 50%" if status == "partial" else "答错/硬伤"),
        "coverage_ratio": coverage,
        "severity": severity,
        "tags": sorted(tags),
        "importance": "major",
        "weight": 0.0,
        "answer_quote": " ｜ ".join(answer_excerpts),
        "answer_excerpts": answer_excerpts,
        "reason": _clip(judgement, 360),
        "omission_reason": _clip(judgement, 360),
        "omission_suggestion": _clip(suggestion, 360),
        "missing_elements": [],
        "material_evidence": evidence,
        "canonical_expression": "",
        "minimum_expression": "",
        "correction": _clip(correction, 360),
        "annotations": [],
        "revised_answer": _legacy_revised_answer(report_text),
        "answer_snapshot": answer_snapshot,
        "display_score": float(score_match.group(1)) if score_match else None,
        "display_max_score": float(score_match.group(2)) if score_match else None,
        "overall_summary": _clip(core_match.group(1), 260) if core_match else "",
    }


def _base_item(row, result, point_key, point, match, annotations, material_evidence):
    status = str(match.get("status") or "partial")
    coverage = max(0.0, min(1.0, _as_float(match.get("coverage_ratio"), 0.0)))
    severity = _severity_for_point(match, point, annotations)
    tags = {"omission"} if status in {"partial", "miss"} else set()
    if any(_annotation_is_error(item) for item in annotations):
        tags.add("error")
    if _is_severe(match, point, annotations, severity):
        tags.add("severe")

    missing_elements = _json_value(match.get("missing_elements"), [])
    if not isinstance(missing_elements, list):
        missing_elements = []
    missing_elements = [_clip(item, 90) for item in missing_elements if _clip(item, 90)]

    replacement = _best_replacement(annotations)
    correction = (
        replacement
        or _clip(match.get("omission_suggestion"), 360)
        or _clip((point or {}).get("minimum_expression"), 360)
        or _clip((point or {}).get("canonical_expression"), 360)
    )

    label = _clip((point or {}).get("label") or "采分点遗漏", 80)
    if status == "miss":
        status_label = "完全遗漏"
    else:
        status_label = f"部分遗漏 · {round(coverage * 100)}%"

    answer_snapshot = str(result.get("answer_snapshot") or row["answer_text"] or "").strip()
    answer_excerpts = _answer_excerpts(match, answer_snapshot)

    return {
        "issue_key": f"attempt:{row['attempt_id']}:point:{point_key}",
        "report_id": row["report_id"],
        "attempt_id": row["attempt_id"],
        "question_id": row["question_id"],
        "question_title": row["question_title"],
        "paper_name": row["paper_name"],
        "question_number": row["question_number"],
        "question_type": row["question_type"],
        "word_limit": row["word_limit"],
        "year": row["year"],
        "region": row["region"],
        "exam_type": row["exam_type"],
        "created_at": row["created_at"],
        "provider": row["provider"],
        "model": row["model"],
        "point_key": point_key,
        "label": label,
        "status": status,
        "status_label": status_label,
        "coverage_ratio": coverage,
        "severity": severity,
        "tags": sorted(tags),
        "importance": str(match.get("importance") or (point or {}).get("importance") or "major"),
        "weight": _as_float(match.get("weight"), _as_float((point or {}).get("weight"), 0.0)),
        "answer_quote": " ｜ ".join(answer_excerpts),
        "answer_excerpts": answer_excerpts,
        "reason": _clip(match.get("reason"), 360),
        "omission_reason": _clip(match.get("omission_reason"), 360),
        "omission_suggestion": _clip(match.get("omission_suggestion"), 360),
        "missing_elements": missing_elements,
        "material_evidence": material_evidence,
        "canonical_expression": _clip((point or {}).get("canonical_expression"), 420),
        "minimum_expression": _clip((point or {}).get("minimum_expression"), 360),
        "correction": correction,
        "annotations": [
            {
                "kind": str(item.get("kind") or ""),
                "severity": str(item.get("severity") or "low"),
                "quote": _clip(item.get("quote"), 220),
                "replacement": _clip(item.get("replacement"), 320),
                "reason": _clip(item.get("reason"), 320),
            }
            for item in annotations
            if isinstance(item, dict)
            and item.get("severity") != "positive"
            and not _annotation_is_typo_only(item)
        ],
        "revised_answer": str(result.get("revised_answer") or "").strip(),
        "answer_snapshot": answer_snapshot,
        "display_score": result.get("display_score"),
        "display_max_score": result.get("display_max_score"),
        "overall_summary": _clip(result.get("overall_summary"), 260),
    }


def _annotation_only_item(row, result, point_key, point, annotation, material_evidence):
    raw_severity = str(annotation.get("severity") or "medium")
    severity = "high" if raw_severity == "critical" else raw_severity
    if severity not in {"high", "medium"}:
        return None
    if not material_evidence:
        return None
    tags = {"error"}
    if severity == "high":
        tags.add("severe")
    replacement = _clip(annotation.get("replacement"), 360)
    answer_snapshot = str(result.get("answer_snapshot") or row["answer_text"] or "").strip()
    answer_excerpts = _answer_excerpts(
        {"answer_quote": annotation.get("quote") or "", "evidence_spans": []},
        answer_snapshot,
    )
    if not answer_excerpts:
        return None
    return {
        "issue_key": f"attempt:{row['attempt_id']}:point:{point_key}:error",
        "report_id": row["report_id"],
        "attempt_id": row["attempt_id"],
        "question_id": row["question_id"],
        "question_title": row["question_title"],
        "paper_name": row["paper_name"],
        "question_number": row["question_number"],
        "question_type": row["question_type"],
        "word_limit": row["word_limit"],
        "year": row["year"],
        "region": row["region"],
        "exam_type": row["exam_type"],
        "created_at": row["created_at"],
        "provider": row["provider"],
        "model": row["model"],
        "point_key": point_key,
        "label": _clip((point or {}).get("label") or "事实或表达错误", 80),
        "status": "error",
        "status_label": "答错/硬伤",
        "coverage_ratio": 1.0,
        "severity": severity,
        "tags": sorted(tags),
        "importance": str((point or {}).get("importance") or "major"),
        "weight": _as_float((point or {}).get("weight"), 0.0),
        "answer_quote": " ｜ ".join(answer_excerpts),
        "answer_excerpts": answer_excerpts,
        "reason": _clip(annotation.get("reason"), 360),
        "omission_reason": "",
        "omission_suggestion": "",
        "missing_elements": [],
        "material_evidence": material_evidence,
        "canonical_expression": _clip((point or {}).get("canonical_expression"), 420),
        "minimum_expression": _clip((point or {}).get("minimum_expression"), 360),
        "correction": replacement or _clip((point or {}).get("minimum_expression"), 360),
        "annotations": [
            {
                "kind": str(annotation.get("kind") or ""),
                "severity": severity,
                "quote": _clip(annotation.get("quote"), 220),
                "replacement": replacement,
                "reason": _clip(annotation.get("reason"), 320),
            }
        ],
        "revised_answer": str(result.get("revised_answer") or "").strip(),
        "answer_snapshot": answer_snapshot,
        "display_score": result.get("display_score"),
        "display_max_score": result.get("display_max_score"),
        "overall_summary": _clip(result.get("overall_summary"), 260),
    }


def _unbound_annotation_item(row, result, annotation, material_evidence):
    raw_severity = str(annotation.get("severity") or "medium")
    severity = "high" if raw_severity in {"high", "critical"} else "medium"
    if severity != "high" or not material_evidence:
        return None
    answer_snapshot = str(result.get("answer_snapshot") or row["answer_text"] or "").strip()
    answer_excerpts = _answer_excerpts(
        {"answer_quote": annotation.get("quote") or "", "evidence_spans": []},
        answer_snapshot,
    )
    if not answer_excerpts:
        return None
    replacement = _clip(annotation.get("replacement"), 360)
    reason = _clip(annotation.get("reason"), 360)
    digest_source = "|".join(
        [
            str(annotation.get("quote") or ""),
            str(annotation.get("replacement") or ""),
            str(annotation.get("reason") or ""),
        ]
    )
    digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:14]
    return {
        "issue_key": f"attempt:{row['attempt_id']}:unbound:{digest}",
        "report_id": row["report_id"],
        "attempt_id": row["attempt_id"],
        "question_id": row["question_id"],
        "question_title": row["question_title"],
        "paper_name": row["paper_name"],
        "question_number": row["question_number"],
        "question_type": row["question_type"],
        "word_limit": row["word_limit"],
        "year": row["year"],
        "region": row["region"],
        "exam_type": row["exam_type"],
        "created_at": row["created_at"],
        "provider": row["provider"],
        "model": row["model"],
        "point_key": f"unbound-{digest}",
        "label": "事实或表达硬伤",
        "status": "error",
        "status_label": "答错/硬伤",
        "coverage_ratio": 1.0,
        "severity": "high",
        "tags": ["error", "severe"],
        "importance": "major",
        "weight": 0.0,
        "answer_quote": " ｜ ".join(answer_excerpts),
        "answer_excerpts": answer_excerpts,
        "reason": reason,
        "omission_reason": "",
        "omission_suggestion": "",
        "missing_elements": [],
        "material_evidence": material_evidence,
        "canonical_expression": "",
        "minimum_expression": "",
        "correction": replacement,
        "annotations": [
            {
                "kind": str(annotation.get("kind") or ""),
                "severity": raw_severity,
                "quote": _clip(annotation.get("quote"), 220),
                "replacement": replacement,
                "reason": reason,
            }
        ],
        "revised_answer": str(result.get("revised_answer") or "").strip(),
        "answer_snapshot": answer_snapshot,
        "display_score": result.get("display_score"),
        "display_max_score": result.get("display_max_score"),
        "overall_summary": _clip(result.get("overall_summary"), 260),
    }


def load_mistake_items(conn):
    rows = conn.execute(
        """
        SELECT grc.report_id,
               grc.result_json,
               grc.rubric_snapshot_json,
               gr.provider,
               gr.model,
               gr.created_at,
               a.id AS attempt_id,
               a.answer_text,
               q.id AS question_id,
               q.title AS question_title,
               q.paper_id,
               q.materials AS question_materials,
               q.paper_name,
               q.question_number,
               q.question_type,
               q.word_limit,
               q.year,
               q.region,
               q.exam_type
          FROM grading_report_contexts grc
          JOIN grading_reports gr ON gr.id = grc.report_id
          JOIN attempts a ON a.id = gr.attempt_id
          JOIN questions q ON q.id = a.question_id
         WHERE gr.status = 'ok'
           AND grc.result_json <> '{}'
           AND NOT EXISTS (
               SELECT 1
                 FROM grading_reports newer
                 JOIN grading_report_contexts newer_context ON newer_context.report_id = newer.id
                WHERE newer.attempt_id = gr.attempt_id
                  AND newer.status = 'ok'
                  AND newer_context.result_json <> '{}'
                  AND newer.id > gr.id
           )
      ORDER BY gr.created_at DESC, gr.id DESC
        """
    ).fetchall()

    items = []
    for raw_row in rows:
        row = _row_dict(raw_row)
        result = _json_value(row["result_json"], {})
        rubric = _json_value(row["rubric_snapshot_json"], {})
        if not isinstance(result, dict) or not isinstance(rubric, dict):
            continue
        points = _point_lookup(rubric)
        material_sources = _material_sources(conn, row)
        annotations = result.get("annotations") if isinstance(result.get("annotations"), list) else []
        annotations_by_point = defaultdict(list)
        for annotation in annotations:
            if not isinstance(annotation, dict):
                continue
            point_key = str(annotation.get("point_key") or "").strip()
            if point_key:
                annotations_by_point[point_key].append(annotation)

        created_points = set()
        matches = result.get("point_matches") if isinstance(result.get("point_matches"), list) else []
        for match in matches:
            if not isinstance(match, dict):
                continue
            status = str(match.get("status") or "")
            if status not in {"partial", "miss"}:
                continue
            point_key = str(match.get("point_key") or "").strip()
            if not point_key:
                continue
            point = points.get(point_key) or {}
            # Re-resolve every model-supplied material quote against the real
            # stored source before it reaches the notebook.  The rubric may
            # contain a wrong material number or a lightly paraphrased quote.
            # Those are useful hints, not trusted display text.
            material_evidence = _verified_material_evidence(point, material_sources)
            if not material_evidence:
                continue
            item = _base_item(
                row,
                result,
                point_key,
                point,
                match,
                annotations_by_point.get(point_key, []),
                material_evidence,
            )
            items.append(item)
            created_points.add(point_key)

        for point_key, point_annotations in annotations_by_point.items():
            if point_key in created_points:
                continue
            point = points.get(point_key)
            if not point:
                continue
            error_annotations = [item for item in point_annotations if _annotation_is_error(item)]
            if not error_annotations:
                continue
            annotation = max(
                error_annotations,
                key=lambda item: SEVERITY_ORDER.get(str(item.get("severity") or "low"), 1),
            )
            material_evidence = _verified_material_evidence(point, material_sources)
            item = _annotation_only_item(
                row,
                result,
                point_key,
                point,
                annotation,
                material_evidence,
            )
            if item:
                items.append(item)

        # A rare but important class of hard errors is attached to the whole
        # answer rather than a rubric point.  Only admit high/critical errors
        # when their correction can itself be located in the real material.
        # Pure structure/style feedback without a source-bound excerpt stays
        # in the full grading report instead of being forced into the notebook.
        for annotation in annotations:
            if not isinstance(annotation, dict) or str(annotation.get("point_key") or "").strip():
                continue
            if not _annotation_is_error(annotation):
                continue
            if str(annotation.get("severity") or "") not in {"high", "critical"}:
                continue
            material_evidence = _annotation_material_evidence(annotation, material_sources)
            item = _unbound_annotation_item(row, result, annotation, material_evidence)
            if item:
                items.append(item)

    # Conservative compatibility for pre-structured reports.  Only the latest
    # successful report for an attempt is considered, and a legacy mistake is
    # emitted only when a phrase from its rubric can be located verbatim in the
    # real stored material.  This keeps old history useful without ever
    # presenting model paraphrase as source text.
    legacy_rows = conn.execute(
        """
        SELECT gr.id AS report_id,
               gr.provider,
               gr.model,
               gr.report_text,
               gr.created_at,
               a.id AS attempt_id,
               a.answer_text,
               q.id AS question_id,
               q.title AS question_title,
               q.paper_id,
               q.paper_name,
               q.question_number,
               q.question_type,
               q.word_limit,
               q.materials AS question_materials,
               q.year,
               q.region,
               q.exam_type
          FROM grading_reports gr
          JOIN attempts a ON a.id = gr.attempt_id
          JOIN questions q ON q.id = a.question_id
         WHERE gr.status = 'ok'
           AND NOT EXISTS (
               SELECT 1
                 FROM grading_reports structured_report
                 JOIN grading_report_contexts structured_context
                   ON structured_context.report_id = structured_report.id
                WHERE structured_report.attempt_id = gr.attempt_id
                  AND structured_report.status = 'ok'
                  AND structured_context.result_json <> '{}'
           )
           AND NOT EXISTS (
               SELECT 1
                 FROM grading_reports newer
                WHERE newer.attempt_id = gr.attempt_id
                  AND newer.status = 'ok'
                  AND newer.id > gr.id
           )
      ORDER BY gr.created_at DESC, gr.id DESC
        """
    ).fetchall()
    for raw_row in legacy_rows:
        row = _row_dict(raw_row)
        report_text = row.get("report_text") or ""
        scoring_points = _legacy_scoring_points(report_text)
        for table_row in _legacy_table_rows(report_text):
            scoring_point = _match_legacy_point(table_row.get("label"), scoring_points)
            if not scoring_point:
                continue
            item = _legacy_item(conn, row, report_text, table_row, scoring_point)
            if item:
                items.append(item)

    recurrence = defaultdict(int)
    for item in items:
        recurrence[(item["question_id"], item["point_key"])] += 1
    for item in items:
        item["recurrence_count"] = recurrence[(item["question_id"], item["point_key"])]

    review_rows = conn.execute(
        """
        SELECT issue_key, state, is_starred, note, review_count, last_reviewed_at, updated_at
          FROM mistake_reviews
        """
    ).fetchall()
    reviews = {row["issue_key"]: _row_dict(row) for row in review_rows}
    for item in items:
        review = reviews.get(item["issue_key"], {})
        item["review_state"] = review.get("state") or "active"
        item["is_starred"] = bool(review.get("is_starred") or 0)
        item["note"] = review.get("note") or ""
        item["review_count"] = int(review.get("review_count") or 0)
        item["last_reviewed_at"] = review.get("last_reviewed_at") or ""
    return items


def mistake_summary(items):
    return {
        "total": len(items),
        "active": sum(1 for item in items if item.get("review_state") != "mastered"),
        "severe": sum(1 for item in items if "severe" in item.get("tags", []) and item.get("review_state") != "mastered"),
        "errors": sum(1 for item in items if "error" in item.get("tags", []) and item.get("review_state") != "mastered"),
        "repeated": sum(1 for item in items if item.get("recurrence_count", 1) > 1 and item.get("review_state") != "mastered"),
        "mastered": sum(1 for item in items if item.get("review_state") == "mastered"),
        "starred": sum(1 for item in items if item.get("is_starred")),
    }
