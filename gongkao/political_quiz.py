"""Political-theory quiz packs, validation, persistence, and analysis."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from urllib.parse import urlparse

from .ai import codex_chat_completion, codex_research_completion

QUESTION_TYPES = {"combo", "count", "count5", "pick", "map", "multi"}
CIRCLED = "①②③④⑤"
DEFAULT_TRAP_TYPES = (
    "主体替换",
    "定位词错位",
    "时间节点混装",
    "方向反转",
    "绝对化和过度化",
    "政策目标与工具错配",
    "近邻概念归属错位",
    "真知识放错语境",
    "半句正确半句错误",
    "条件偷换",
    # 兼容旧题包已有标签。
    "方向颠倒",
    "绝对化",
    "偷换关键词",
    "无中生有",
    "张冠李戴",
)
KNOWLEDGE_LAYERS = (
    "马克思主义基本原理",
    "党的创新理论与党的建设",
    "改革与国家治理",
    "发展与安全",
    "地方治理",
)
RELATION_TYPES = (
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
    "历史时空",
    "科学条件",
)
OFFICIAL_SOURCE_NAMES = (
    "新华社",
    "人民日报",
    "中国政府网",
    "求是网",
    "中央人民政府",
    "最高人民法院",
)
OFFICIAL_SOURCE_HOST_SUFFIXES = (
    "gov.cn",
    "news.cn",
    "xinhuanet.com",
    "people.com.cn",
    "qstheory.cn",
    "court.gov.cn",
    "spp.gov.cn",
    "npc.gov.cn",
    "ccdi.gov.cn",
    "12371.cn",
    "mod.gov.cn",
)


def _canonical_trap_type(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if text in DEFAULT_TRAP_TYPES:
        return text
    # 模型在审校时经常使用同义标签；这里按设误语义归类，避免因为命名差异误杀整包。
    if "主体" in text:
        return "主体替换"
    if "时间" in text or "阶段" in text:
        return "时间节点混装"
    if "定位" in text or "层级" in text:
        return "定位词错位"
    if "方向" in text or "颠倒" in text or "反转" in text:
        return "方向反转"
    if "绝对" in text or "过度" in text or "范围" in text:
        return "绝对化和过度化"
    if ("目标" in text and "工具" in text) or "政策工具" in text:
        return "政策目标与工具错配"
    if "概念" in text or "归属" in text or "体系" in text:
        return "近邻概念归属错位"
    if "半句" in text or "半真" in text or "半假" in text:
        return "半句正确半句错误"
    if "条件" in text:
        return "条件偷换"
    if "语境" in text or "错配" in text or "错位" in text or "替换" in text or "偷换" in text:
        return "真知识放错语境"
    return ""


def _question_number_set(traps):
    covered = set()
    for group in traps or []:
        for number in group.get("qs") or []:
            try:
                covered.add(int(number))
            except (TypeError, ValueError):
                continue
    return covered


def _source_complete(source):
    if not isinstance(source, dict):
        return False
    return all(str(source.get(key) or "").strip() for key in ("name", "date", "url"))


def _official_source_url(url):
    try:
        host = (urlparse(str(url or "")).hostname or "").lower().strip(".")
    except ValueError:
        return False
    return bool(host) and any(host == suffix or host.endswith("." + suffix) for suffix in OFFICIAL_SOURCE_HOST_SUFFIXES)


_CIRCLED_INDEX = {mark: index for index, mark in enumerate(CIRCLED)}
_STATEMENT_DIGITS = {str(index + 1): index for index in range(5)}
_STATEMENT_DIGITS.update({chr(0xFF10 + index + 1): index for index in range(5)})
_CHINESE_NUMERALS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5}


def _option_statement_set(option):
    """Return the statement numbers an option label refers to, ignoring formatting.

    Models write the same combo answer as ①③, A．①③, ①、③, （1）（3） or 13.
    Normalizing before comparing keeps a cosmetic difference from voiding a pack.
    Circled markers win when present, so trailing notes like "①③（共2项）" stay exact.
    """
    text = str(option or "")
    circled = {_CIRCLED_INDEX[mark] for mark in text if mark in _CIRCLED_INDEX}
    if circled:
        return circled
    return {_STATEMENT_DIGITS[char] for char in text if char in _STATEMENT_DIGITS}


def _count_option_value(option):
    text = str(option or "")
    match = re.search(r"\d+", text)
    if match:
        return int(match.group())
    for mark in text:
        if mark in _CIRCLED_INDEX:
            return _CIRCLED_INDEX[mark] + 1
    for mark, value in _CHINESE_NUMERALS.items():
        if mark in text:
            return value
    return None


def validate_quiz_pack(data):
    """Return a list of human-readable structural errors for a quiz pack."""
    errors = []
    if not isinstance(data, dict):
        return ["题包必须是 JSON 对象"]
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        return ["题包缺少 questions"]
    traps = data.get("traps")
    if not isinstance(traps, list):
        errors.append("题包缺少 traps")
        traps = []

    for index, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            errors.append(f"第{index}题结构异常")
            continue
        qtype = question.get("type")
        if qtype not in QUESTION_TYPES:
            errors.append(f"第{index}题题型异常")
        options = question.get("options")
        if not isinstance(options, list) or len(options) != 4:
            errors.append(f"第{index}题选项数异常")
        answer_index = question.get("answer_index")
        if qtype != "multi" and (not isinstance(answer_index, int) or not 0 <= answer_index <= 3):
            errors.append(f"第{index}题答案索引异常")
        if not _source_complete(question.get("source")):
            errors.append(f"第{index}题缺少来源")

        if qtype == "multi":
            truth = question.get("truth")
            explanations = question.get("explanations")
            if not isinstance(truth, list) or len(truth) != 4:
                errors.append(f"第{index}题 truth 条数异常")
            if not isinstance(explanations, list) or len(explanations) != 4:
                errors.append(f"第{index}题解析条数异常")
        elif qtype in {"combo", "count", "count5"}:
            expected = 5 if qtype == "count5" else 4
            statements = question.get("statements")
            truth = question.get("truth")
            explanations = question.get("explanations")
            if not isinstance(statements, list) or len(statements) != expected:
                errors.append(f"第{index}题表述条数异常")
            if not isinstance(truth, list) or len(truth) != expected:
                errors.append(f"第{index}题 truth 条数异常")
            if not isinstance(explanations, list) or len(explanations) != expected:
                errors.append(f"第{index}题解析条数异常")
            if (
                isinstance(options, list)
                and len(options) == 4
                and isinstance(answer_index, int)
                and 0 <= answer_index <= 3
                and isinstance(truth, list)
                and len(truth) == expected
            ):
                if qtype in {"count", "count5"}:
                    expected_count = sum(1 for value in truth if bool(value))
                    if _count_option_value(options[answer_index]) != expected_count:
                        errors.append(f"第{index}题计数答案错位")
                elif _option_statement_set(options[answer_index]) != {
                    position for position, value in enumerate(truth) if value
                }:
                    errors.append(f"第{index}题组合答案错位")
        else:
            explanations = question.get("explanations")
            if not isinstance(explanations, list) or len(explanations) < 2:
                errors.append(f"第{index}题解析过少")

    covered = _question_number_set(traps)
    for number in range(1, len(questions) + 1):
        if number not in covered:
            errors.append(f"易错清单未覆盖第{number}题")
    return errors


def validate_quiz_quality(data, *, question_count=None, exam_scope="national", province=""):
    """Check whether a generated pack resembles recent civil-service exam construction.

    Structural validity is handled by :func:`validate_quiz_pack`. This gate checks
    the characteristics that matter for realism: relationship-based knowledge,
    plausible micro-edits, topic layers, source diversity and answer distribution.
    It intentionally only becomes strict for real 10/20/30-question generations,
    leaving small fixture packs and imported historical packs backwards compatible.
    """
    questions = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(questions, list):
        return ["拟真度质检缺少 questions"]
    target = int(question_count or len(questions) or 0)
    if target < 10:
        return []

    errors = []
    stems = []
    trap_types = []
    relation_types = []
    knowledge_layers = []
    source_urls = []
    single_answers = []
    trivial_recall_markers = (
        "发生于哪一年",
        "发生在哪一年",
        "召开时间是",
        "召开地点是",
        "发布日期是",
        "具体日期是",
        "哪一天召开",
    )

    for index, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            continue
        stem = re.sub(r"\s+", "", str(question.get("stem") or ""))
        if stem:
            stems.append(stem)
        if any(marker in stem for marker in trivial_recall_markers):
            errors.append(f"第{index}题仍是新闻日期/地点记忆题，未转化为关系辨析")

        source = question.get("source") if isinstance(question.get("source"), dict) else {}
        source_url = str(source.get("url") or "").strip()
        if source_url:
            source_urls.append(source_url)
        if not _official_source_url(source_url):
            errors.append(f"第{index}题来源域名不在官方白名单")

        layer = str(question.get("knowledge_layer") or "").strip()
        if layer not in KNOWLEDGE_LAYERS:
            errors.append(f"第{index}题缺少有效 knowledge_layer")
        else:
            knowledge_layers.append(layer)

        relation = str(question.get("relation_type") or "").strip()
        if relation not in RELATION_TYPES:
            errors.append(f"第{index}题缺少有效 relation_type")
        else:
            relation_types.append(relation)

        trap = _canonical_trap_type(question.get("trap_type"))
        if not trap:
            errors.append(f"第{index}题 trap_type 未落入近年真题设误类型")
        else:
            trap_types.append(trap)

        trap_point = question.get("trap_point")
        if not isinstance(trap_point, dict) or not all(
            str(trap_point.get(key) or "").strip() for key in ("wrong", "right", "reason")
        ):
            errors.append(f"第{index}题缺少可核查的微改设误点 trap_point")

        key_words = question.get("key_words")
        if not isinstance(key_words, list) or not 1 <= len([item for item in key_words if str(item).strip()]) <= 4:
            errors.append(f"第{index}题 key_words 应为1-4个关键辨析词")

        explanations = question.get("explanations")
        if isinstance(explanations, list) and explanations:
            informative = [text for text in explanations if len(str(text).strip()) >= 10]
            if len(informative) < max(1, len(explanations) - 1):
                errors.append(f"第{index}题解析过于简略，未定位具体错误关系")

        answer_index = question.get("answer_index")
        if question.get("type") != "multi" and isinstance(answer_index, int) and 0 <= answer_index <= 3:
            single_answers.append(answer_index)

    if len(stems) != len(set(stems)):
        errors.append("题包存在重复题干")

    expected_mix = {
        "national": {10: (2, 1), 20: (3, 3), 30: (5, 4)},
        "province": {10: (2, 2), 20: (4, 4), 30: (6, 6)},
    }.get(exam_scope, {}).get(target)
    if expected_mix:
        combo_expected, count_expected = expected_mix
        combo_actual = sum(1 for q in questions if isinstance(q, dict) and q.get("type") == "combo")
        count_actual = sum(1 for q in questions if isinstance(q, dict) and q.get("type") in {"count", "count5"})
        if combo_actual != combo_expected:
            errors.append(f"组合题配比异常：应为{combo_expected}题，实际{combo_actual}题")
        if count_actual != count_expected:
            errors.append(f"计数题配比异常：应为{count_expected}题，实际{count_actual}题")

    diversity_floor = {10: 4, 20: 6, 30: 7}.get(target, 4)
    if len(set(trap_types)) < diversity_floor:
        errors.append(f"设误类型过于单一：至少覆盖{diversity_floor}类近年真题陷阱")
    if len(set(relation_types)) < diversity_floor:
        errors.append(f"关系型考点过于单一：至少覆盖{diversity_floor}类关系")

    min_sources = {10: 4, 20: 6, 30: 8}.get(target, 4)
    if len(set(source_urls)) < min_sources:
        errors.append(f"官方来源覆盖过窄：至少使用{min_sources}个不同来源页面")

    if single_answers:
        counts = Counter(single_answers)
        distribution = [counts.get(i, 0) for i in range(4)]
        tolerance = {10: 2, 20: 2, 30: 3}.get(target, 2)
        if max(distribution) - min(distribution) > tolerance:
            errors.append("正确答案位置分布失衡，ABCD集中度过高")

    if exam_scope == "national":
        layer_counts = Counter(knowledge_layers)
        layer_minimums = {
            10: {
                "马克思主义基本原理": 1,
                "党的创新理论与党的建设": 2,
                "改革与国家治理": 3,
                "发展与安全": 1,
            },
            20: {
                "马克思主义基本原理": 3,
                "党的创新理论与党的建设": 4,
                "改革与国家治理": 6,
                "发展与安全": 3,
            },
            30: {
                "马克思主义基本原理": 4,
                "党的创新理论与党的建设": 6,
                "改革与国家治理": 9,
                "发展与安全": 4,
            },
        }.get(target, {})
        for layer, minimum in layer_minimums.items():
            if layer_counts.get(layer, 0) < minimum:
                errors.append(f"知识结构失衡：{layer}至少{minimum}题")

    return errors


def parse_quiz_json_response(raw):
    text = str(raw or "").strip()
    if not text:
        raise ValueError("生成结果不是合法 JSON，请重新生成。")

    def decode_object(candidate):
        candidate = str(candidate or "").strip()
        if not candidate:
            return None
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    # Fast path for the ideal response and the historic single-fence response.
    direct = decode_object(text)
    if direct is not None:
        return direct
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    if fenced:
        direct = decode_object(fenced.group(1))
        if direct is not None:
            return direct

    # Codex occasionally wraps an otherwise valid object in prose or emits more
    # than one fenced block. Recover only complete JSON objects. JSONDecoder's
    # raw_decode understands braces and quotes inside string values, so this is
    # intentionally safer than slicing from the first "{" to the last "}".
    decoder = json.JSONDecoder()
    candidates = []
    seen = set()

    def add_candidate(value, start, end, source_rank):
        if not isinstance(value, dict):
            return
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if serialized in seen:
            return
        root_keys = {"meta", "cards", "questions", "traps", "cheat"}
        schema_score = len(root_keys.intersection(value.keys()))
        # Embedded recovery is only for a complete quiz-pack root. Requiring
        # multiple root markers prevents a nested object from being accepted
        # when the real outer JSON was truncated halfway through generation.
        if schema_score < 2:
            return
        seen.add(serialized)
        candidates.append((schema_score, source_rank, end - start, value))

    # Prefer complete objects found in explicit code fences.
    for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE):
        block = match.group(1).strip()
        try:
            value, end = decoder.raw_decode(block)
        except json.JSONDecodeError:
            continue
        if not block[end:].strip():
            add_candidate(value, 0, end, 2)

    # Then scan the full response for a complete embedded top-level object.
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            continue
        add_candidate(value, index, end, 1)

    if candidates:
        # A real quiz pack carries several known root keys; sorting by schema
        # score also prevents a nested source/trap object from winning when the
        # surrounding response contains the full pack.
        candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return candidates[0][3]

    # Preserve the more specific historical error for a valid non-object JSON.
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("生成结果不是合法 JSON，请重新生成。") from exc
    if not isinstance(parsed, dict):
        raise ValueError("生成结果必须是单个 JSON 对象。")
    return parsed


def _repair_quiz_json_serialization(settings, content):
    """Repair JSON serialization damage without repeating web research."""
    repair_prompt = f"""下面内容来自一次已经完成联网检索的公务员政治理论题包生成。题包事实、来源和答案已经生成，但返回文本的 JSON 序列化可能存在截断以外的格式问题，例如多余说明文字、Markdown 围栏、引号/逗号/括号错误。

只做 JSON 序列化修复：
- 保留原有字段、数组顺序、题目文字、答案、解析、来源 URL 和所有事实内容。
- 不新增、不删除、不改写任何时政事实、政策表述、题目或来源。
- 仅修复使现有内容能够组成一个合法 JSON 对象所必需的标点、引号、转义、逗号和括号。
- 如果原始内容确实被截断，无法在不编造内容的前提下完整恢复，就不要补写缺失事实。

只输出单个合法 JSON 对象，不要解释，不要 Markdown 围栏。

原始返回：
{str(content or '')}
"""
    repaired, _raw = codex_chat_completion(settings, repair_prompt, {"thinking": "disabled"})
    return repaired


def _ensure_trap_coverage(data):
    """Fill only missing trap coverage from already-generated question fields.

    This is deterministic normalization, not a second content-generation pass.
    It never invents new current-affairs facts: the fallback entries are derived
    from each question's own options, answer/truth, topic, trap_type and keywords.
    """
    if not isinstance(data, dict):
        return data
    questions = data.get("questions")
    if not isinstance(questions, list):
        return data
    traps = data.get("traps")
    if not isinstance(traps, list):
        traps = []
        data["traps"] = traps
    covered = _question_number_set(traps)
    for number, question in enumerate(questions, start=1):
        if number in covered or not isinstance(question, dict):
            continue
        options = question.get("options") if isinstance(question.get("options"), list) else []
        qtype = str(question.get("type") or "")
        correct_text = ""
        wrong_text = ""
        if qtype == "multi":
            truth = question.get("truth") if isinstance(question.get("truth"), list) else []
            correct = [str(option) for option, flag in zip(options, truth) if bool(flag)]
            wrong = [str(option) for option, flag in zip(options, truth) if not bool(flag)]
            correct_text = "；".join(correct)
            wrong_text = wrong[0] if wrong else ""
        else:
            answer_index = question.get("answer_index")
            if isinstance(answer_index, int) and 0 <= answer_index < len(options):
                correct_text = str(options[answer_index])
                wrong_text = next((str(value) for index, value in enumerate(options) if index != answer_index), "")
        trap_type = str(question.get("trap_type") or "").strip() or "表述辨析"
        key_words = question.get("key_words") if isinstance(question.get("key_words"), list) else []
        miss = [str(value) for value in key_words if str(value).strip()][:3]
        traps.append(
            {
                "topic": str(question.get("topic") or "综合"),
                "qs": [number],
                "set": [
                    {
                        "q": number,
                        "wrong": wrong_text,
                        "right": correct_text,
                        "how": trap_type,
                    }
                ],
                "miss": miss,
            }
        )
    return data


def normalize_quiz_pack(data):
    normalized = json.loads(json.dumps(data, ensure_ascii=False))
    normalized.setdefault("meta", {})
    normalized.setdefault("cards", [])
    normalized.setdefault("traps", [])
    normalized.setdefault("cheat", {"trap_methods": list(DEFAULT_TRAP_TYPES), "numbers": []})
    normalized["meta"].setdefault("exam_scope", "national")
    normalized["meta"].setdefault("province", "")
    normalized["meta"].setdefault("verification_status", "generated")
    _ensure_trap_coverage(normalized)
    return normalized


def _pack_key(data):
    meta = data.get("meta") or {}
    explicit = str(meta.get("pack_key") or "").strip()
    if explicit:
        return explicit
    identity = {
        "title": meta.get("title") or "",
        "exam_scope": meta.get("exam_scope") or "national",
        "province": meta.get("province") or "",
        "period_label": meta.get("period_label") or "",
        "questions": data.get("questions") or [],
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    period = re.sub(r"[^0-9A-Za-z_-]+", "-", str(meta.get("period_label") or "period")).strip("-")
    scope = str(meta.get("exam_scope") or "national")
    return f"{period or 'period'}-{scope}-{digest}"


def _source_count(data):
    urls = {
        str((question.get("source") or {}).get("url") or "").strip()
        for question in data.get("questions") or []
        if isinstance(question, dict)
    }
    urls.discard("")
    return len(urls)


def save_quiz_pack(
    conn,
    data,
    *,
    verification_status=None,
    status="ready",
    generation_prompt="",
    generation_error="",
):
    normalized = normalize_quiz_pack(data)
    errors = validate_quiz_pack(normalized) if status == "ready" else []
    if errors:
        raise ValueError("；".join(errors))
    meta = normalized["meta"]
    verification_status = verification_status or meta.get("verification_status") or "generated"
    meta["verification_status"] = verification_status
    key = _pack_key(normalized)
    payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    values = (
        key,
        str(meta.get("title") or "时政测验"),
        str(meta.get("exam_scope") or "national"),
        str(meta.get("province") or ""),
        str(meta.get("period_label") or ""),
        len(normalized.get("questions") or []),
        status,
        verification_status,
        _source_count(normalized),
        payload,
        generation_prompt,
        generation_error,
    )
    conn.execute(
        """
        INSERT INTO political_quiz_packs (
            pack_key, title, exam_scope, province, period_label, question_count,
            status, verification_status, source_count, data_json,
            generation_prompt, generation_error, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(pack_key) DO UPDATE SET
            title=excluded.title,
            exam_scope=excluded.exam_scope,
            province=excluded.province,
            period_label=excluded.period_label,
            question_count=excluded.question_count,
            status=excluded.status,
            verification_status=excluded.verification_status,
            source_count=excluded.source_count,
            data_json=excluded.data_json,
            generation_prompt=excluded.generation_prompt,
            generation_error=excluded.generation_error,
            updated_at=CURRENT_TIMESTAMP
        """,
        values,
    )
    row = conn.execute("SELECT id FROM political_quiz_packs WHERE pack_key = ?", (key,)).fetchone()
    conn.commit()
    return row[0]


def record_quiz_generation_failure(conn, *, error, options=None, generation_prompt=""):
    """Persist a failed generation attempt so the reason outlives the job registry.

    Failed attempts use status='failed' (never returned by :func:`list_quiz_packs`)
    and are keyed per exam scope/period/question count, so the table keeps the
    latest failure for each configuration instead of growing without bound.
    """
    options = options if isinstance(options, dict) else {}
    scope = str(options.get("exam_scope") or "national")
    province = str(options.get("province") or "") if scope == "province" else ""
    question_count = int(options.get("question_count") or 0)
    try:
        period_label = _normalize_month_range(options.get("period_start"), options.get("period_end"))[2]
    except ValueError:
        period_label = ""
    key = f"failed:{scope}:{province}:{period_label}:{question_count}"
    title = f"{period_label or '未指定时间'}{'省考' if scope == 'province' else '国考'}政治理论专项测验（生成失败）"
    conn.execute(
        """
        INSERT INTO political_quiz_packs (
            pack_key, title, exam_scope, province, period_label, question_count,
            status, verification_status, source_count, data_json,
            generation_prompt, generation_error, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'failed', 'generated', 0, '{}', ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(pack_key) DO UPDATE SET
            title=excluded.title,
            period_label=excluded.period_label,
            question_count=excluded.question_count,
            status='failed',
            generation_prompt=excluded.generation_prompt,
            generation_error=excluded.generation_error,
            updated_at=CURRENT_TIMESTAMP
        """,
        (key, title, scope, province, period_label, question_count, generation_prompt, str(error)[:4000]),
    )
    conn.commit()
    return key


def list_quiz_packs(conn, *, limit=20):
    return conn.execute(
        """
        SELECT * FROM political_quiz_packs
        WHERE status = 'ready'
        ORDER BY CASE verification_status WHEN 'verified' THEN 0 ELSE 1 END,
                 updated_at DESC, id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()


def get_quiz_pack(conn, pack_id):
    return conn.execute("SELECT * FROM political_quiz_packs WHERE id = ?", (int(pack_id),)).fetchone()


def mark_quiz_pack_verified(conn, pack_id):
    row = get_quiz_pack(conn, pack_id)
    if row is None or row["status"] != "ready":
        return False
    try:
        data = json.loads(row["data_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    meta = data.setdefault("meta", {})
    if not isinstance(meta, dict):
        return False
    meta["verification_status"] = "verified"
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    conn.execute(
        """
        UPDATE political_quiz_packs
           SET verification_status = 'verified', data_json = ?, updated_at = CURRENT_TIMESTAMP
         WHERE id = ?
        """,
        (payload, int(pack_id)),
    )
    conn.commit()
    return True


def create_quiz_attempt(conn, pack_id, mode="practice"):
    if mode not in {"practice", "exam"}:
        raise ValueError("mode must be practice or exam")
    cursor = conn.execute(
        "INSERT INTO political_quiz_attempts (pack_id, mode) VALUES (?, ?)",
        (int(pack_id), mode),
    )
    conn.commit()
    return cursor.lastrowid


def update_quiz_attempt(conn, attempt_id, answers, *, elapsed_seconds=0):
    payload = json.dumps(answers or {}, ensure_ascii=False, separators=(",", ":"))
    conn.execute(
        """
        UPDATE political_quiz_attempts
           SET answers_json = ?, elapsed_seconds = ?, updated_at = CURRENT_TIMESTAMP
         WHERE id = ?
        """,
        (payload, max(0, int(elapsed_seconds or 0)), int(attempt_id)),
    )
    conn.commit()


def get_quiz_attempt(conn, attempt_id):
    return conn.execute("SELECT * FROM political_quiz_attempts WHERE id = ?", (int(attempt_id),)).fetchone()


def build_attempt_analysis(pack, answers):
    questions = pack.get("questions") or []
    if isinstance(answers, dict):

        def answer_for(index):
            return answers.get(str(index), answers.get(index))

    else:

        def answer_for(index):
            return answers[index] if index < len(answers or []) else None
    correct_count = 0
    topic_stats = OrderedDict()
    trap_errors = OrderedDict((name, 0) for name in DEFAULT_TRAP_TYPES)
    key_words = []
    for index, question in enumerate(questions):
        answer = answer_for(index)
        if question.get("type") == "multi":
            expected = [
                option_index
                for option_index, value in enumerate(question.get("truth") or [])
                if bool(value)
            ]
            selected = sorted(
                {
                    int(value)
                    for value in (answer if isinstance(answer, list) else [])
                    if isinstance(value, int) or str(value).isdigit()
                }
            )
            is_correct = selected == expected
        else:
            is_correct = answer == question.get("answer_index")
        if is_correct:
            correct_count += 1
        topic = str(question.get("topic") or "其他")
        stat = topic_stats.setdefault(topic, {"correct": 0, "total": 0})
        stat["total"] += 1
        if is_correct:
            stat["correct"] += 1
        else:
            trap = str(question.get("trap_type") or "").strip()
            if trap:
                trap_errors[trap] = trap_errors.get(trap, 0) + 1
            for word in question.get("key_words") or []:
                word = str(word).strip()
                if word and word not in key_words:
                    key_words.append(word)
    return {
        "correct_count": correct_count,
        "question_count": len(questions),
        "accuracy": round(correct_count * 100 / len(questions), 1) if questions else 0.0,
        "topic_stats": dict(topic_stats),
        "trap_errors": dict(trap_errors),
        "key_words": key_words[:3],
    }


def complete_quiz_attempt(conn, attempt_id, pack, answers, *, elapsed_seconds=0):
    analysis = build_attempt_analysis(pack, answers)
    payload = json.dumps(answers or {}, ensure_ascii=False, separators=(",", ":"))
    conn.execute(
        """
        UPDATE political_quiz_attempts
           SET answers_json = ?, correct_count = ?, question_count = ?,
               elapsed_seconds = ?, completed_at = CURRENT_TIMESTAMP,
               updated_at = CURRENT_TIMESTAMP
         WHERE id = ?
        """,
        (
            payload,
            analysis["correct_count"],
            analysis["question_count"],
            max(0, int(elapsed_seconds or 0)),
            int(attempt_id),
        ),
    )
    conn.commit()
    return analysis


def _normalize_month_range(period_start="", period_end=""):
    start = str(period_start or "").strip()
    end = str(period_end or "").strip()
    if not start and not end:
        return "", "", ""
    start = start or end
    end = end or start
    month_pattern = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
    start_match = month_pattern.fullmatch(start)
    end_match = month_pattern.fullmatch(end)
    if not start_match or not end_match:
        raise ValueError("时间范围必须使用有效月份，例如 2026-01")
    if start > end:
        raise ValueError("起始月份不能晚于结束月份")
    start_year, start_month = int(start_match.group(1)), int(start_match.group(2))
    end_year, end_month = int(end_match.group(1)), int(end_match.group(2))
    if start == end:
        label = f"{start_year}年{start_month}月"
    elif start_year == end_year:
        label = f"{start_year}年{start_month}月至{end_month}月"
    else:
        label = f"{start_year}年{start_month}月至{end_year}年{end_month}月"
    return start, end, label


def build_generation_prompt(
    *,
    exam_scope="national",
    province="",
    period="month",
    period_start="",
    period_end="",
    question_count=20,
):
    question_count = int(question_count)
    scope_text = "国考政治理论"
    national_mix = {
        10: "组合2、计数1（含1道5条）、选非2、四句式选是5",
        20: "组合3、计数3（至少1道5条）、选非5、四句式选是9",
        30: "组合5、计数4（至少1道5条）、选非7、四句式选是14",
    }
    province_mix = {
        10: "组合2、计数2（含1道5条）、选非2、四句式选是4；马原约1题",
        20: "组合4、计数4（至少1道5条）、选非4、四句式选是8；马原约2题",
        30: "组合6、计数6（至少1道5条）、选非6、四句式选是12；马原约3题",
    }
    ratio_text = national_mix.get(question_count, national_mix[20])
    extra = ""
    if exam_scope == "province":
        scope_text = f"{province or '省考'}政治理论"
        ratio_text = province_mix.get(question_count, province_mix[20])
        extra = f"\n- 追加一轮 {province or '目标省份'} 省情、战略、政府工作报告官方检索。"
        if province == "上海":
            extra += (
                "\n- 上海版必须包含不定项选择题，题型 type='multi'，options 为4个完整表述，"
                "truth 为4项布尔/0-1向量；多选、少选、错选均不得分，multi 不使用 answer_index。"
            )
    range_start, range_end, range_label = _normalize_month_range(period_start, period_end)
    period_text = range_label or {
        "month": "本月并回炉上月重点政策",
        "30d": "最近30日",
        "review": "上月重点政策回炉",
    }.get(period, "本月")
    range_rule = ""
    if range_label:
        range_rule = (
            f"\n- 本次用户指定时间区间为 {range_label}（{range_start} 至 {range_end}）。"
            "时政事件、会议、文件和新政策的发生或发布日期必须落在该区间内；不得用区间外的新旧时政材料凑题。"
        )
    return f"""你正在为公务员行测生成{scope_text}专项测验。目标题量 {int(question_count)} 题，时间范围：{period_text}。

必须先联网搜索并逐字核对政治表述。可信来源只采用新华社、人民日报、中国政府网、求是网、中央和国务院部门官网、最高人民法院等官方来源。网友回忆版真题只可判断题型趋势，**不得作为政治表述来源**。拿不准的表述宁可弃用，不得凭记忆补齐。{extra}

这套题必须按 2016—2025 国考真题规律命制，尤其以 2022—2025 为主样本。不要把“时政”理解成每日新闻问答。近年国考真正考的是新材料中的稳定理论、制度关系和治理逻辑；会议日期、新闻地点、人物名单、单一数字等纯记忆信息原则上不得独立成题。

命题前先在内部建立“关系素材池”，每条材料至少识别：规范概念、主体、定位/层级、时间阶段、政策目标、政策工具、容易互换的近邻概念。只有同时具备权威来源、结构化关系、现实治理意义和合理干扰项空间的材料才进入题目。

国考拟真要求：
- 知识结构必须混合四层：马克思主义基本原理；党的创新理论与党的建设；进一步全面深化改革与国家治理；发展与安全。20题国考至少分别安排3、4、6、3题，其余题在四层内机动。不得做成“本月新闻20题”。
- 题型配比：{ratio_text}。
- 时间筛选：严格按上述时间范围检索和选材。{range_rule}
- 题目落点优先考“关系组”：主体—权限、概念—定位、阶段—目标、理念—解决问题、政策目标—政策工具、制度—适用对象、理论—方法论。题干可以使用最新材料，答案必须落在可精确判断的规范关系上。
- 近年高频设误至少覆盖：主体替换、定位词错位、时间节点混装、方向反转、绝对化和过度化、政策目标与工具错配、近邻概念归属错位、真知识放错语境、半句正确半句错误、条件偷换。20题至少覆盖其中6类。
- 错项要有“九成正确感”。优先只改一个主体、一个动词、一个定位词、一个时间层级或一个对象；不要写明显荒谬、常识即可秒杀的错项。四个候选应尽量保持句式、长度、政策语体接近。
- 允许“政策本身正确但不回答本题”的干扰项，用于考政策目标与工具的直接对应关系。
- 计数/组合题应提高判断单元密度；同一题可把两到三种微改方式混合，但每条解析必须指出具体错字、错词、错关系，并给出正确关系。
- combo 选项保持升序；计数题 truth 数量必须与答案选项严格对应；multi 用 truth 直接判分，不使用 answer_index。
- 每题必须记录官方来源 name/date/url；引用讲话、文件、法律必须逐字核对。
- 每题增加 knowledge_layer、relation_type、trap_point。knowledge_layer 只能从“马克思主义基本原理/党的创新理论与党的建设/改革与国家治理/发展与安全/地方治理”中选；relation_type 只能从“主体关系/定位关系/时间层级/方向关系/条件范围/概念归属/目标工具/理论对应/制度机制/法治主体程序/历史时空/科学条件”中选；trap_point 固定为 {{"wrong":"错误片段","right":"规范片段","reason":"为何构成设误"}}。
- traps 必须覆盖全部题号；每组同时给出已设误点与未设但高频的易错点。
- 正确答案位置 A/B/C/D 要尽量均衡，20题各位置原则上约5题，不能连续大面积集中。
- 数字不单独成题；时间只在“阶段目标错层”这类关系辨析中使用。

输出结构是硬约束，任何一题都不得省字段或改变数组长度：
- 所有题的 options 必须且只能有4项；source 必须含 name/date/url；key_words 必须是数组。
- combo：statements/truth/explanations 各4项，truth 与 statements 一一对应；options 是4个“①②③”式组合；answer_index 为0-3整数。
- count：statements/truth/explanations 各4项；options 是4个“1项/2项/3项/4项”式计数答案；answer_index 指向 truth 中正确项总数。
- count5：statements/truth/explanations 各5项；options 仍只能有4项，但必须覆盖实际正确项总数；answer_index 指向实际正确项总数。
- pick/map：不使用 statements/truth；4个完整候选表述直接放入 options；explanations 至少4项，逐项解释；answer_index 为0-3整数。
- multi：只用于要求不定项的省考；options/truth/explanations 各4项；省略 answer_index。
- questions 必须恰好 {int(question_count)} 题。生成完成后先自行做一次“命题人检查”：主体、定位词、时间层级、方向、范围、半句真假、概念归属、目标工具是否精确；若错误项一眼可排除，必须重写。

只输出**单个合法 JSON 对象**，不得输出 Markdown 代码围栏、解释文字或 HTML。JSON 顶层固定为 meta/cards/questions/traps/cheat；questions 字段使用 type/stem/ask/statements/truth/options/answer_index/explanations/source/topic/trap_type/key_words/knowledge_layer/relation_type/trap_point，其中 multi 省略 answer_index。"""


def _repair_quiz_pack_structure(settings, data, errors, question_count):
    """Repair schema/shape mistakes without doing a second web-research pass."""
    repair_prompt = f"""下面是一份已经完成联网核验的公务员政治理论题包，但结构质检失败。

你的任务只允许修复 JSON 结构、字段位置、数组长度、answer_index 与 truth/options 的对应关系；不得新增时政事实、不得改写官方来源 URL、不得凭空补充新的政策表述。若某字段内容已经存在，应移动/整理而不是重写。

质检错误：
{chr(10).join('- ' + str(item) for item in errors)}

硬约束：
- questions 必须恰好 {int(question_count)} 题。
- 每题 options 恰好4项，source 必须有 name/date/url。
- combo/count：statements、truth、explanations 各4项。
- count5：statements、truth、explanations 各5项，options 仍为4项。
- pick/map：4个候选完整表述放 options，不要放 statements；explanations 至少4项。
- multi：options、truth、explanations 各4项且不使用 answer_index。
- traps 必须覆盖1到{int(question_count)}的全部题号。

只输出修复后的单个合法 JSON 对象，不要解释。

待修复 JSON：
{json.dumps(data, ensure_ascii=False, separators=(',', ':'))}
"""
    content, _raw = codex_chat_completion(settings, repair_prompt, {"thinking": "disabled"})
    return normalize_quiz_pack(parse_quiz_json_response(content))


def _review_quiz_pack_exam_quality(settings, data, quality_errors, question_count):
    """Run a dedicated exam-style editorial pass after facts have been researched."""
    issue_text = "\n".join("- " + str(item) for item in (quality_errors or [])) or "- 无硬性报错；仍需逐题提升拟真度"
    review_prompt = f"""你是国家公务员考试行测政治理论命题审校员。下面题包已经完成联网检索和官方来源核验，现在进行第二遍“真题拟真度”审校。

这一步的任务是改造题目结构、设问和干扰项，使其更接近 2022—2025 国考，尤其是 2025 年政治理论独立模块。不得新增未经现有 source 支持的新事实，不得更换或虚构来源 URL。可以围绕现有已核验事实重写题干、正确项表达和错误项微改方式。

当前自动质检发现：
{issue_text}

逐题执行以下检查：
1. 时政材料必须落到稳定关系，不出“哪天召开、在哪里召开、谁参加、数字是多少”式新闻记忆题。
2. 错项优先只改一个主体、一个定位词、一个动词、一个时间层级、一个政策对象或一个归属关系，让错误项保持高度熟悉感。
3. 至少覆盖主体替换、定位词错位、时间节点混装、方向反转、绝对化、目标工具错配、近邻概念归属错位、真知识错语境、半句真半句假、条件偷换中的多种方式。
4. 四个候选项必须处于同一语义层级和相近语言风格，禁止三个明显假项陪一个原文项。
5. 计数题和组合题的每个判断单元都要有辨析价值；解析必须明确指出“错在哪里、正确关系是什么”，不能只写“错误/正确”。
6. 国考知识结构同时包含马克思主义基本原理、党的创新理论与党的建设、改革与国家治理、发展与安全。20题至少分别3、4、6、3题。
7. 正确答案 A/B/C/D 尽量均衡；题干不得重复；同一官方网页不要支撑过多题目。
8. 每题补齐 knowledge_layer、relation_type、trap_point={{"wrong":"错误片段","right":"规范片段","reason":"设误逻辑"}}，并令 trap_type 使用近年真题设误名称。
9. 保持原题数量恰好 {int(question_count)} 题，保持现有 source 的 name/date/url，不得删除来源。

只输出审校后的单个合法 JSON 对象，不要解释，不要 Markdown 围栏。

待审校题包：
{json.dumps(data, ensure_ascii=False, separators=(',', ':'))}
"""
    content, _raw = codex_chat_completion(settings, review_prompt, {"thinking": "enabled"})
    return normalize_quiz_pack(parse_quiz_json_response(content))


def _planned_question_blueprints(exam_scope, province, question_count):
    """Build a deterministic global blueprint so batched generation stays balanced."""
    target = int(question_count)
    if target not in {20, 30}:
        return []

    if exam_scope == "province":
        combo_count, count_count = {20: (4, 4), 30: (6, 6)}[target]
    else:
        combo_count, count_count = {20: (3, 3), 30: (5, 4)}[target]

    types = ["pick" if index % 2 == 0 else "map" for index in range(target)]
    combo_positions = [index * 5 for index in range(combo_count)]
    count_positions = [index * 5 + 1 for index in range(count_count)]
    for index in combo_positions:
        types[index] = "combo"
    for position_index, index in enumerate(count_positions):
        types[index] = "count5" if position_index == 0 else "count"

    if exam_scope == "province" and province == "上海":
        for index in range(4, target, 5):
            if types[index] not in {"combo", "count", "count5"}:
                types[index] = "multi"

    if exam_scope == "national":
        quotas = {
            20: {
                "马克思主义基本原理": 3,
                "党的创新理论与党的建设": 4,
                "改革与国家治理": 8,
                "发展与安全": 5,
            },
            30: {
                "马克思主义基本原理": 4,
                "党的创新理论与党的建设": 6,
                "改革与国家治理": 12,
                "发展与安全": 8,
            },
        }[target]
        layer_cycle = [
            "改革与国家治理",
            "党的创新理论与党的建设",
            "发展与安全",
            "改革与国家治理",
            "马克思主义基本原理",
        ]
        layers = []
        remaining = dict(quotas)
        while len(layers) < target:
            for layer in layer_cycle:
                if remaining.get(layer, 0) <= 0:
                    continue
                layers.append(layer)
                remaining[layer] -= 1
                if len(layers) >= target:
                    break
    else:
        province_layers = [
            "地方治理",
            "改革与国家治理",
            "党的创新理论与党的建设",
            "发展与安全",
            "马克思主义基本原理",
        ]
        layers = [province_layers[index % len(province_layers)] for index in range(target)]

    trap_cycle = list(DEFAULT_TRAP_TYPES[:10])
    relation_cycle = list(RELATION_TYPES)
    blueprints = []
    for index in range(target):
        blueprints.append(
            {
                "number": index + 1,
                "type": types[index],
                "knowledge_layer": layers[index],
                "relation_type": relation_cycle[index % len(relation_cycle)],
                "trap_type": trap_cycle[index % len(trap_cycle)],
                "answer_index": index % 4,
            }
        )
    return blueprints


def _build_batch_generation_prompt(
    *,
    exam_scope,
    province,
    period,
    period_start,
    period_end,
    question_count,
    blueprints,
    existing_data=None,
):
    target = int(question_count)
    batch_size = len(blueprints)
    first_number = int(blueprints[0]["number"])
    last_number = int(blueprints[-1]["number"])
    range_start, range_end, range_label = _normalize_month_range(period_start, period_end)
    period_text = range_label or {
        "month": "本月并回炉上月重点政策",
        "30d": "最近30日",
        "review": "上月重点政策回炉",
    }.get(period, "本月")
    scope_text = "国考政治理论" if exam_scope == "national" else f"{province or '省考'}政治理论"
    existing_data = existing_data if isinstance(existing_data, dict) else {}
    existing_questions = existing_data.get("questions") if isinstance(existing_data.get("questions"), list) else []
    used_stems = [str(item.get("stem") or "") for item in existing_questions if isinstance(item, dict)][-20:]
    used_urls = [
        str((item.get("source") or {}).get("url") or "")
        for item in existing_questions
        if isinstance(item, dict) and isinstance(item.get("source"), dict)
    ][-20:]
    range_rule = ""
    if range_label:
        range_rule = f"本批所有时政事件、文件和新政策的发生或发布日期必须落在 {range_start} 至 {range_end}。"
    blueprint_text = json.dumps(blueprints, ensure_ascii=False, separators=(",", ":"))
    return f"""你正在为公务员行测生成{scope_text}专项测验。完整题包共 {target} 题；本次只生成第 {first_number}—{last_number} 题，共 {batch_size} 题，禁止多生成或少生成。

必须联网检索并逐字核对政治表述，只采用新华社、人民日报、中国政府网、求是网、中央和国务院部门官网、最高人民法院等官方来源。时间范围：{period_text}。{range_rule}

本批每题必须严格服从下面的全局蓝图。type、knowledge_layer、relation_type、trap_type 必须逐题对应；除 multi 外，answer_index 必须等于蓝图指定位置，并通过重排 options、truth 或组合/计数答案选项保证答案逻辑正确：
{blueprint_text}

命题规则：
- 不做会议日期、地点、人物名单、单一数字式新闻记忆题；用最新材料考主体—权限、概念—定位、阶段—目标、政策目标—工具、制度—对象、理论—方法等稳定关系。
- 错项只微调一个主体、定位词、动词、时间层级、对象或归属关系，保持“九成正确感”；四个选项语言风格和长度尽量接近。
- 每题必须有官方 source={{"name":"","date":"","url":""}}，并有 topic、trap_type、1—4个 key_words、knowledge_layer、relation_type、trap_point={{"wrong":"错误片段","right":"规范片段","reason":"设误逻辑"}}。
- combo：statements/truth/explanations 各4项，options 恰好4项且 answer_index 与 truth 对应。
- count：statements/truth/explanations 各4项；count5 各5项；options 始终4项且答案必须等于 truth 中正确项数量。
- pick/map：options 恰好4项，explanations 至少4项，不使用 statements/truth。
- multi：options/truth/explanations 各4项，不输出 answer_index。
- 每题解析要指出具体错词或错关系并给出正确关系，不能只写“正确/错误”。
- 本批尽量使用不同官方来源页面，并避开已经用过的题干和来源。

已经使用的题干：{json.dumps(used_stems, ensure_ascii=False)}
已经使用的来源 URL：{json.dumps(used_urls, ensure_ascii=False)}

输出必须是一个完整合法 JSON 对象，顶层固定为 meta/cards/questions/traps/cheat。questions 必须恰好 {batch_size} 题；traps 中 q/qs 使用本批局部编号 1—{batch_size}。不得输出 Markdown 围栏、解释文字或 HTML。"""


def _merge_quiz_batch(base, batch):
    batch = normalize_quiz_pack(batch)
    if not isinstance(base, dict) or not base:
        merged = {
            "meta": json.loads(json.dumps(batch.get("meta") or {}, ensure_ascii=False)),
            "cards": [],
            "questions": [],
            "traps": [],
            "cheat": {},
        }
    else:
        merged = json.loads(json.dumps(base, ensure_ascii=False))
        merged.setdefault("meta", {})
        merged.setdefault("cards", [])
        merged.setdefault("questions", [])
        merged.setdefault("traps", [])
        merged.setdefault("cheat", {})

    offset = len(merged["questions"])
    merged["questions"].extend(json.loads(json.dumps(batch.get("questions") or [], ensure_ascii=False)))

    seen_cards = {
        json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for card in merged.get("cards") or []
        if isinstance(card, dict)
    }
    for card in batch.get("cards") or []:
        if not isinstance(card, dict):
            continue
        key = json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if key not in seen_cards:
            merged["cards"].append(json.loads(json.dumps(card, ensure_ascii=False)))
            seen_cards.add(key)

    for trap in batch.get("traps") or []:
        if not isinstance(trap, dict):
            continue
        item = json.loads(json.dumps(trap, ensure_ascii=False))
        if isinstance(item.get("qs"), list):
            item["qs"] = [int(value) + offset for value in item["qs"] if str(value).isdigit()]
        if isinstance(item.get("set"), list):
            for entry in item["set"]:
                if isinstance(entry, dict) and str(entry.get("q") or "").isdigit():
                    entry["q"] = int(entry["q"]) + offset
        merged["traps"].append(item)

    batch_cheat = batch.get("cheat") if isinstance(batch.get("cheat"), dict) else {}
    merged_cheat = merged.get("cheat") if isinstance(merged.get("cheat"), dict) else {}
    for key, value in batch_cheat.items():
        if isinstance(value, list):
            existing = merged_cheat.get(key) if isinstance(merged_cheat.get(key), list) else []
            for item in value:
                if item not in existing:
                    existing.append(item)
            merged_cheat[key] = existing
        elif key not in merged_cheat:
            merged_cheat[key] = value
    merged["cheat"] = merged_cheat
    return normalize_quiz_pack(merged)


def _review_quiz_pack_exam_quality_batched(settings, data, quality_errors, batch_size=5):
    """Review long packs in bounded chunks so the review response cannot truncate the pack."""
    reviewed = json.loads(json.dumps(data, ensure_ascii=False))
    questions = reviewed.get("questions") if isinstance(reviewed.get("questions"), list) else []
    issue_text = "\n".join("- " + str(item) for item in (quality_errors or [])) or "- 无硬性报错"
    for start in range(0, len(questions), int(batch_size)):
        original_chunk = json.loads(json.dumps(questions[start:start + int(batch_size)], ensure_ascii=False))
        mini = {
            "meta": {"batch_start": start + 1},
            "cards": [],
            "questions": original_chunk,
            "traps": [],
            "cheat": {},
        }
        _ensure_trap_coverage(mini)
        prompt = f"""你是公务员行测政治理论命题审校员。下面是完整题包中的第 {start + 1}—{start + len(original_chunk)} 题。只审校这 {len(original_chunk)} 题，返回同样数量，不能增删题目。

全局质检提示：
{issue_text}

要求：保持每题 type 和官方 source.name/date/url 不变；不得新增未经 source 支持的事实。可以改写题干、选项、解析、answer_index/truth 以及 knowledge_layer、relation_type、trap_type、trap_point，使错项更接近近年国考“微改关系”风格，并改善答案位置分布。所有数组长度必须符合题型结构。

只输出单个合法 JSON 对象，顶层至少包含 meta/questions/traps，不要解释，不要 Markdown 围栏。

待审校批次：
{json.dumps(mini, ensure_ascii=False, separators=(',', ':'))}
"""
        try:
            content, _raw = codex_chat_completion(settings, prompt, {"thinking": "enabled"})
            candidate = normalize_quiz_pack(parse_quiz_json_response(content))
        except Exception:
            continue
        candidate_questions = candidate.get("questions") if isinstance(candidate.get("questions"), list) else []
        if len(candidate_questions) != len(original_chunk):
            continue
        safe = True
        for before, after in zip(original_chunk, candidate_questions):
            if not isinstance(before, dict) or not isinstance(after, dict):
                safe = False
                break
            if before.get("type") != after.get("type"):
                safe = False
                break
            before_url = str((before.get("source") or {}).get("url") or "")
            after_url = str((after.get("source") or {}).get("url") or "")
            if before_url != after_url:
                safe = False
                break
        if safe:
            questions[start:start + len(original_chunk)] = candidate_questions
    reviewed["questions"] = questions
    _ensure_trap_coverage(reviewed)
    return normalize_quiz_pack(reviewed)


def generate_quiz_pack(
    settings,
    *,
    exam_scope="national",
    province="",
    period="month",
    period_start="",
    period_end="",
    question_count=20,
    progress_callback=None,
    resume_state=None,
    checkpoint_callback=None,
):
    def emit(progress, message, phase):
        if progress_callback is None:
            return
        progress_callback(
            {
                "kind": "quiz_progress",
                "progress": max(0, min(100, int(progress))),
                "message": str(message or ""),
                "phase": str(phase or ""),
            }
        )

    def checkpoint(progress, phase, **payload):
        if checkpoint_callback is None:
            return
        state = {
            "progress": max(0, min(100, int(progress))),
            "phase": str(phase or ""),
        }
        state.update(payload)
        checkpoint_callback(state)

    if str((settings or {}).get("mode") or "") != "chatgpt":
        raise ValueError("生成最新测验需要使用 ChatGPT/Codex 账号连接，以启用联网检索。")

    resume_state = dict(resume_state or {})
    resume_phase = str(resume_state.get("phase") or "")
    resume_progress = max(0, min(100, int(resume_state.get("progress") or 0)))
    prompt = str(resume_state.get("prompt") or "")
    if not prompt:
        prompt = build_generation_prompt(
            exam_scope=exam_scope,
            province=province,
            period=period,
            period_start=period_start,
            period_end=period_end,
            question_count=question_count,
        )
    raw = resume_state.get("raw") or ""
    content = resume_state.get("content") or ""
    data = resume_state.get("data") if isinstance(resume_state.get("data"), dict) else None
    batch_data = resume_state.get("batch_data") if isinstance(resume_state.get("batch_data"), dict) else None
    try:
        batch_index = max(0, int(resume_state.get("batch_index") or 0))
    except (TypeError, ValueError):
        batch_index = 0

    if int(question_count) > 10 and data is None and not content:
        blueprints = _planned_question_blueprints(exam_scope, province, question_count)
        if not blueprints:
            raise ValueError("当前题量无法建立分批命题计划。")
        batches = [blueprints[index:index + 5] for index in range(0, len(blueprints), 5)]
        merged = normalize_quiz_pack(batch_data) if batch_data else None
        batch_index = min(batch_index, len(batches))
        start_progress = max(14, resume_progress if resume_phase == "batch_generate" else 14)
        emit(start_progress, "长题包已切换为分批联网命题，避免单次 JSON 输出被截断…", "batch_generate")
        checkpoint(
            start_progress,
            "batch_generate",
            prompt=prompt,
            raw=raw,
            batch_data=merged or {},
            batch_index=batch_index,
        )

        for current_index in range(batch_index, len(batches)):
            batch_blueprints = batches[current_index]
            batch_total = len(batches)
            first_number = int(batch_blueprints[0]["number"])
            last_number = int(batch_blueprints[-1]["number"])
            band_start = 16 + round(current_index * 64 / batch_total)
            band_end = 16 + round((current_index + 1) * 64 / batch_total)
            parsed_batch = None
            last_batch_error = ""

            for attempt in range(2):
                attempt_note = "" if attempt == 0 else " · 正在重试本批完整输出"
                emit(
                    max(band_start, resume_progress if current_index == batch_index else band_start),
                    f"正在生成第 {current_index + 1}/{batch_total} 批（第{first_number}—{last_number}题）{attempt_note}…",
                    "batch_generate",
                )
                batch_prompt = _build_batch_generation_prompt(
                    exam_scope=exam_scope,
                    province=province,
                    period=period,
                    period_start=period_start,
                    period_end=period_end,
                    question_count=question_count,
                    blueprints=batch_blueprints,
                    existing_data=merged,
                )
                if attempt:
                    batch_prompt += "\n\n上一次本批返回不完整或不符合蓝图。本次必须一次输出全部题目，完成 JSON 闭合后再结束回答。"

                batch_search_count = 0

                def on_batch_event(event):
                    nonlocal batch_search_count
                    if not isinstance(event, dict):
                        return
                    event_type = str(event.get("type") or "")
                    item = event.get("item") if isinstance(event.get("item"), dict) else {}
                    item_type = str(item.get("type") or event.get("item_type") or "").lower()
                    progress = band_start
                    message = f"第 {current_index + 1}/{batch_total} 批正在联网检索…"
                    if "web" in item_type and "search" in item_type:
                        if event_type == "item.completed":
                            batch_search_count += 1
                        progress = min(band_end - 2, band_start + 2 + batch_search_count * 2)
                        message = f"第 {current_index + 1}/{batch_total} 批已完成 {batch_search_count} 轮来源核验"
                    elif item_type == "reasoning" and event_type == "item.completed":
                        progress = max(progress, band_end - 3)
                        message = f"第 {current_index + 1}/{batch_total} 批正在编排题目…"
                    elif item_type in {"agent_message", "message"} and event_type == "item.completed":
                        progress = max(progress, band_end - 1)
                        message = f"第 {current_index + 1}/{batch_total} 批正在收尾 JSON…"
                    elif event_type == "turn.completed":
                        progress = band_end
                        message = f"第 {current_index + 1}/{batch_total} 批已生成，正在校验…"
                    else:
                        return
                    emit(progress, message, "batch_generate")

                batch_content, batch_raw = codex_research_completion(
                    settings,
                    batch_prompt,
                    {"thinking": "enabled"},
                    progress_callback=on_batch_event if progress_callback is not None else None,
                )
                raw = batch_raw
                try:
                    candidate = normalize_quiz_pack(parse_quiz_json_response(batch_content))
                except ValueError:
                    emit(max(band_start, band_end - 1), "本批返回格式有杂质，正在恢复 JSON…", "parse_repair")
                    repaired_content = _repair_quiz_json_serialization(settings, batch_content)
                    try:
                        candidate = normalize_quiz_pack(parse_quiz_json_response(repaired_content))
                    except ValueError as exc:
                        last_batch_error = str(exc)
                        continue

                expected_batch_count = len(batch_blueprints)
                actual_batch_count = len(candidate.get("questions") or [])
                if actual_batch_count != expected_batch_count:
                    last_batch_error = f"本批要求{expected_batch_count}题，实际{actual_batch_count}题"
                    continue

                expected_types = [str(item.get("type") or "") for item in batch_blueprints]
                actual_types = [
                    str(item.get("type") or "")
                    for item in candidate.get("questions") or []
                    if isinstance(item, dict)
                ]
                if actual_types != expected_types:
                    last_batch_error = f"本批题型未按蓝图生成：期望{expected_types}，实际{actual_types}"
                    continue

                batch_errors = validate_quiz_pack(candidate)
                if batch_errors:
                    emit(max(band_start, band_end - 1), f"本批发现 {len(batch_errors)} 个结构问题，正在修复…", "repair")
                    candidate = _repair_quiz_pack_structure(
                        settings,
                        candidate,
                        batch_errors,
                        expected_batch_count,
                    )
                    batch_errors = validate_quiz_pack(candidate)
                    repaired_types = [
                        str(item.get("type") or "")
                        for item in candidate.get("questions") or []
                        if isinstance(item, dict)
                    ]
                    if batch_errors or repaired_types != expected_types:
                        last_batch_error = "；".join(batch_errors[:6]) or "结构修复后题型偏离蓝图"
                        continue

                parsed_batch = candidate
                break

            if parsed_batch is None:
                raise ValueError(
                    f"第 {current_index + 1}/{len(batches)} 批生成不完整：{last_batch_error or '返回内容异常'}。"
                    "再次生成会从已完成批次继续，不会重跑前面的批次。"
                )

            merged = _merge_quiz_batch(merged, parsed_batch)
            completed_progress = max(band_end, band_start)
            checkpoint(
                completed_progress,
                "batch_generate",
                prompt=prompt,
                raw=raw,
                batch_data=merged,
                batch_index=current_index + 1,
            )
            emit(
                completed_progress,
                f"第 {current_index + 1}/{len(batches)} 批已完成，当前累计 {len(merged.get('questions') or [])} 题…",
                "batch_generate",
            )

        data = normalize_quiz_pack(merged or {})
        data["meta"]["batched_generation"] = True
        data["meta"]["batch_size"] = 5
        content = ""

    elif data is None and not content:
        emit(max(8, resume_progress), "正在整理考试范围、时间区间和题型配比…", "prepare")
        emit(max(14, resume_progress), "命题规则已生成，正在连接 Codex 联网研究…", "connecting")

        search_count = 0
        highest_model_progress = max(14, resume_progress)

        def on_codex_event(event):
            nonlocal search_count, highest_model_progress
            if not isinstance(event, dict):
                return
            event_type = str(event.get("type") or "")
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            item_type = str(item.get("type") or event.get("item_type") or "").lower()

            progress = highest_model_progress
            message = "正在联网检索并组织题目…"
            phase = "research"
            if event_type == "thread.started":
                progress = max(progress, 18)
                message = "已连接 Codex，开始联网检索官方来源…"
            elif event_type == "turn.started":
                progress = max(progress, 22)
                message = "正在分析时政范围并规划检索路径…"
            elif "web" in item_type and "search" in item_type:
                if event_type == "item.completed":
                    search_count += 1
                progress = max(progress, min(62, 28 + search_count * 5))
                message = f"正在核验官方来源 · 已完成 {search_count} 轮检索" if search_count else "正在检索官方来源…"
            elif item_type == "reasoning" and event_type == "item.completed":
                progress = max(progress, 64)
                message = "官方材料已收集，正在编排题型与干扰项…"
                phase = "compose"
            elif item_type in {"agent_message", "message"} and event_type == "item.completed":
                progress = max(progress, 74)
                message = "题包内容已生成，正在收尾并准备结构质检…"
                phase = "compose"
            elif event_type == "turn.completed":
                progress = max(progress, 78)
                message = "联网研究完成，正在解析题包…"
                phase = "parse"
            else:
                return

            highest_model_progress = max(highest_model_progress, progress)
            emit(highest_model_progress, message, phase)

        content, raw = codex_research_completion(
            settings,
            prompt,
            {"thinking": "enabled"},
            progress_callback=on_codex_event if progress_callback is not None else None,
        )
        checkpoint(82, "parse", prompt=prompt, raw=raw, content=content)

    if data is None:
        emit(max(82, resume_progress if resume_phase == "parse" else 82), "已收到完整题包，正在解析 JSON 结构…", "parse")
        try:
            data = normalize_quiz_pack(parse_quiz_json_response(content))
        except ValueError:
            emit(
                max(84, resume_progress if resume_phase == "parse_repair" else 84),
                "题包内容已生成，正在修复返回格式并继续质检…",
                "parse_repair",
            )
            repaired_content = _repair_quiz_json_serialization(settings, content)
            checkpoint(85, "parse_repair", prompt=prompt, raw=raw, content=repaired_content)
            try:
                data = normalize_quiz_pack(parse_quiz_json_response(repaired_content))
                content = repaired_content
            except ValueError as repaired_error:
                raise ValueError("题包内容已生成，但返回 JSON 仍不完整；再次生成会从当前进度继续，不会重复联网检索。") from repaired_error

    emit(max(88, resume_progress if resume_phase in {"normalize", "validate", "repair", "quality_review", "save"} else 88), "正在补齐考试范围、时间标签与核验状态…", "normalize")
    data["meta"]["exam_scope"] = exam_scope
    data["meta"]["province"] = province if exam_scope == "province" else ""
    data["meta"]["verification_status"] = "generated"
    _range_start, _range_end, range_label = _normalize_month_range(period_start, period_end)
    if range_label:
        data["meta"]["period_label"] = range_label
    _ensure_trap_coverage(data)
    checkpoint(91, "validate", prompt=prompt, raw=raw, data=data)

    emit(max(91, resume_progress if resume_phase in {"validate", "repair", "quality_review", "save"} else 91), "正在检查题数、答案、解析、来源和易错点…", "validate")
    errors = validate_quiz_pack(data)
    actual_count = len(data.get("questions") or [])
    if actual_count != int(question_count):
        errors.insert(0, f"题数异常：要求{int(question_count)}题，实际{actual_count}题")
    if errors and actual_count == int(question_count):
        checkpoint(92, "repair", prompt=prompt, raw=raw, data=data)
        emit(max(92, resume_progress if resume_phase == "repair" else 92), f"首轮质检发现 {len(errors)} 个结构问题，正在自动修复…", "repair")
        if bool((data.get("meta") or {}).get("batched_generation")):
            # Every five-question batch has already passed structural validation.
            # Rebuild derived trap coverage deterministically here instead of
            # sending the entire 20/30-question pack through another long model
            # response that could truncate it again.
            data["traps"] = []
            _ensure_trap_coverage(data)
        else:
            data = _repair_quiz_pack_structure(settings, data, errors, question_count)
        data["meta"]["exam_scope"] = exam_scope
        data["meta"]["province"] = province if exam_scope == "province" else ""
        data["meta"]["verification_status"] = "generated"
        if range_label:
            data["meta"]["period_label"] = range_label
        errors = validate_quiz_pack(data)
        actual_count = len(data.get("questions") or [])
        if actual_count != int(question_count):
            errors.insert(0, f"题数异常：要求{int(question_count)}题，实际{actual_count}题")
    if errors:
        raise ValueError("题包质检未通过：" + "；".join(errors[:12]))

    if int(question_count) >= 10 and not bool((data.get("meta") or {}).get("quality_reviewed")):
        quality_errors = validate_quiz_quality(
            data,
            question_count=question_count,
            exam_scope=exam_scope,
            province=province,
        )
        checkpoint(94, "quality_review", prompt=prompt, raw=raw, data=data)
        emit(
            max(94, resume_progress if resume_phase == "quality_review" else 94),
            "正在按2016—2025真题规律做第二遍命题审校…",
            "quality_review",
        )
        if int(question_count) > 10:
            if quality_errors:
                data = _review_quiz_pack_exam_quality_batched(settings, data, quality_errors, batch_size=5)
        else:
            data = _review_quiz_pack_exam_quality(settings, data, quality_errors, question_count)
        data["meta"]["exam_scope"] = exam_scope
        data["meta"]["province"] = province if exam_scope == "province" else ""
        data["meta"]["verification_status"] = "generated"
        data["meta"]["quality_reviewed"] = True
        data["meta"]["quality_profile"] = "national-exam-2016-2025-v2"
        if range_label:
            data["meta"]["period_label"] = range_label
        _ensure_trap_coverage(data)

    if int(question_count) >= 10:
        emit(96, "正在检查知识结构、微改设误、来源覆盖与答案分布…", "quality_validate")
        errors = validate_quiz_pack(data)
        quality_errors = validate_quiz_quality(
            data,
            question_count=question_count,
            exam_scope=exam_scope,
            province=province,
        )
        if errors or quality_errors:
            combined = errors + quality_errors
            raise ValueError("题包拟真度质检未通过：" + "；".join(combined[:16]))

    checkpoint(97, "save", prompt=prompt, raw=raw, data=data)
    emit(max(97, resume_progress if resume_phase == "save" else 97), "结构与拟真度质检通过，正在保存题包…", "save")
    return data, prompt, raw


def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
