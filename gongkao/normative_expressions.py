"""Build and maintain a personal library of standardized Shenlun expressions."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime

CATEGORY_RULES = (
    # The first eight dimensions mirror the user's long-run review corpus.
    # Keep them ahead of generic governance buckets so expressions such as
    # “部门联动、免申即享” stay in public service rather than being swallowed
    # by the broader “责任协同” category.
    ("市场竞争与秩序", ("市场", "竞争", "垄断", "准入", "资质", "合同", "收费", "转包", "红黑名单", "信用惩戒", "售后", "欺诈")),
    ("产业与平台", ("产业", "平台", "订单", "场景化", "专业化", "精细化", "定制化", "产业带", "龙头企业", "抱团", "项目", "东数西算")),
    ("人才队伍", ("人才", "招聘", "职称", "编制", "县管乡用", "乡管村用", "实训", "培训", "进修", "师带徒", "传帮带", "定向培养", "专家工作站")),
    ("资金金融与产权", ("资金", "金融", "贷款", "融资", "抵押", "质押", "交易", "兑现", "产权", "资本", "确权", "评估", "银行")),
    ("公共服务与政务", ("政务", "服务", "绿色通道", "免申即享", "应享尽享", "应兑尽兑", "帮办", "代办", "一网通办", "跨省通办", "政策直达", "备案", "企业少跑腿")),
    ("文化空间与宣传引导", ("文化", "研学", "讲座", "表演", "IP", "宣传", "教育", "价值观", "历史遗存", "场景", "个性化", "差异化")),
    ("劳动与社会保障", ("就业", "再就业", "适老", "劳务", "雇主险", "意外伤害险", "老年大学", "社会保障", "保险", "岗位")),
    ("施工与城市管理", ("施工", "围挡", "工期", "防尘", "降尘", "通道", "开挖", "堆放", "疏导", "人车分流", "非机动车道", "占道", "摊贩", "城管", "违停")),
    ("责任主体与协同机制", ("责任", "牵头", "部门", "协同", "联动", "分工", "履职", "专班", "统筹", "协调", "主体")),
    ("制度建设与日常管理", ("制度", "机制", "常态", "日常", "巡查", "管护", "养护", "管理", "长效", "闭环")),
    ("监督检查与群众参与", ("监督", "督查", "检查", "反馈", "举报", "群众", "考核", "监控", "排查", "参与")),
    ("规划建设与设施保障", ("规划", "建设", "基础设施", "设施", "设备", "配备", "路权", "井盖", "路面")),
)

COMMON_VERBS = (
    "压实", "落实", "明确", "厘清", "建立", "健全", "完善", "增设", "配备", "开展", "组织",
    "划定", "设置", "查处", "引导", "畅通", "规范", "整治", "加强", "强化", "统筹", "协调",
    "纳入", "督促", "加快", "推进", "定期", "实现", "推动", "制定", "保护", "培养", "提供",
    "规划", "修剪", "平整", "提高", "发动", "公示", "预留", "安排", "采取", "禁止",
    "串联", "打通", "延伸", "整合", "盘活", "激活", "撬动", "赋能", "注入", "涵养", "深耕",
    "聚焦", "对接", "匹配", "覆盖", "嵌入", "转化", "释放", "激发", "倒逼", "兜底", "前置",
    "下沉", "开放", "拓展", "培育", "简化", "缩短", "赋予", "引入", "升级", "深挖",
)

INGEST_VERSION = "normative-expressions-v3"

_LEADING_NOISE = re.compile(
    r"^(?:建议(?:补写|改为|写成)?|可写为|规范表述|优先补充|改为|替换为)\s*[：:]\s*",
    re.I,
)
_TRAILING_PUNCT = re.compile(r"[。；;，,、\s]+$")


def _json(value, default):
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def clean_expression(value: str) -> str:
    text = str(value or "").strip().strip("`*_#")
    text = _LEADING_NOISE.sub("", text)
    text = _TRAILING_PUNCT.sub("", text).strip()
    text = re.sub(r"\s+", "", text)
    if len(text) > 180:
        return ""
    if len(text) < 3:
        return ""
    if text in {"按材料原词补足该点的关键动作、对象、限定和结果", "优先修复影响最大的核心问题，再优化结构与表达"}:
        return ""
    return text


def expression_key(expression: str) -> str:
    normalized = re.sub(r"[\s，。；;、：:（）()《》“”‘’\-—_/]", "", expression).casefold()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def infer_category(expression: str, label: str = "", context: str = "") -> str:
    haystack = f"{expression} {label} {context}"
    scores = []
    for index, (category, keywords) in enumerate(CATEGORY_RULES):
        score = sum(1 for keyword in keywords if keyword in haystack)
        if score:
            scores.append((score, -index, category))
    if scores:
        return max(scores)[2]
    return "其他规范表达"


def _ensure_expression(conn, expression: str, category: str = "", *, is_custom: int = 0) -> int | None:
    expression = clean_expression(expression)
    if not expression:
        return None
    key = expression_key(expression)
    category = category or infer_category(expression)
    row = conn.execute(
        "SELECT id, category, is_custom FROM normative_expressions WHERE expression_key = ?",
        (key,),
    ).fetchone()
    if row:
        updates = []
        params = []
        if (
            not row["is_custom"]
            and category != "其他规范表达"
            and row["category"] != category
        ):
            updates.append("category = ?")
            params.append(category)
        if is_custom and not row["is_custom"]:
            updates.append("is_custom = 1")
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            conn.execute(
                f"UPDATE normative_expressions SET {', '.join(updates)} WHERE id = ?",
                (*params, row["id"]),
            )
        return int(row["id"])
    cursor = conn.execute(
        """
        INSERT INTO normative_expressions (expression, expression_key, category, is_custom)
        VALUES (?, ?, ?, ?)
        """,
        (expression, key, category, int(bool(is_custom))),
    )
    return int(cursor.lastrowid)


def _add_evidence(
    conn,
    expression: str,
    *,
    report_id: int | None,
    question_id: int | None,
    source_kind: str,
    match_status: str = "",
    trigger_signal: str = "",
    context_text: str = "",
    wrong_expression: str = "",
    source_label: str = "",
    category: str = "",
) -> int:
    expression = clean_expression(expression)
    if not expression:
        return 0
    category = category or infer_category(expression, source_label, context_text)
    expression_id = _ensure_expression(conn, expression, category)
    if not expression_id:
        return 0
    conn.execute(
        """
        INSERT INTO normative_expression_evidence (
            expression_id, report_id, question_id, source_kind, match_status,
            trigger_signal, context_text, wrong_expression, source_label
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            expression_id,
            report_id,
            question_id,
            source_kind,
            str(match_status or "")[:32],
            str(trigger_signal or "").strip()[:420],
            str(context_text or "").strip()[:420],
            str(wrong_expression or "").strip()[:240],
            str(source_label or "").strip()[:160],
        ),
    )
    conn.execute(
        "UPDATE normative_expressions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (expression_id,),
    )
    return 1


def _markdown_omission_candidates(report_text: str):
    in_section = False
    for raw in str(report_text or "").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            in_section = line.startswith("## 遗漏点分析")
            continue
        if not in_section or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 4 or cells[0] in {"遗漏的采分点", "---"} or set(cells[0]) <= {"-", ":"}:
            continue
        label, material, _reason, suggestion = cells[:4]
        expression = clean_expression(suggestion)
        if expression and not expression.startswith("按材料原词"):
            yield {
                "expression": expression,
                "trigger_signal": material,
                "source_label": label,
                "context_text": label,
            }


def ingest_report_normative_expressions(conn, report_id: int, *, force: bool = False) -> int:
    row = conn.execute(
        """
        SELECT gr.id, gr.report_text, gr.status,
               a.question_id, a.answer_text,
               q.question_code, q.question_type, q.title,
               grc.rubric_snapshot_json, grc.result_json
          FROM grading_reports gr
          JOIN attempts a ON a.id = gr.attempt_id
          JOIN questions q ON q.id = a.question_id
     LEFT JOIN grading_report_contexts grc ON grc.report_id = gr.id
         WHERE gr.id = ?
        """,
        (report_id,),
    ).fetchone()
    if not row or row["status"] != "ok":
        return 0

    signature_source = "\n".join(
        [
            INGEST_VERSION,
            row["report_text"] or "",
            row["rubric_snapshot_json"] or "",
            row["result_json"] or "",
        ]
    )
    signature = hashlib.sha256(signature_source.encode("utf-8")).hexdigest()
    state = conn.execute(
        "SELECT signature FROM normative_expression_ingest_state WHERE report_id = ?",
        (report_id,),
    ).fetchone()
    if state and state["signature"] == signature and not force:
        return 0

    conn.execute("DELETE FROM normative_expression_evidence WHERE report_id = ?", (report_id,))
    inserted = 0
    rubric = _json(row["rubric_snapshot_json"], {})
    result = _json(row["result_json"], {})
    point_matches = {
        str(item.get("point_key") or ""): item
        for item in (result.get("point_matches") or [])
        if isinstance(item, dict)
    }

    context_prefix = " · ".join(
        value for value in (row["question_type"] or "", row["question_code"] or "", row["title"] or "") if value
    )
    seen = set()
    for point in rubric.get("points") or []:
        if not isinstance(point, dict):
            continue
        point_key = str(point.get("point_key") or "")
        match = point_matches.get(point_key) or {}
        label = str(point.get("label") or "").strip()
        material_bits = []
        for material in point.get("material_evidence") or []:
            if not isinstance(material, dict):
                continue
            quote = str(material.get("quote") or "").strip()
            number = material.get("material_number")
            if quote:
                material_bits.append(f"材料{number}：{quote}" if number not in {None, ""} else quote)
            if len(material_bits) >= 2:
                break
        trigger = "；".join(material_bits)
        for candidate in (point.get("canonical_expression"), point.get("minimum_expression")):
            expression = clean_expression(candidate)
            if not expression:
                continue
            dedupe_key = expression_key(expression)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            inserted += _add_evidence(
                conn,
                expression,
                report_id=report_id,
                question_id=row["question_id"],
                source_kind="rubric",
                match_status=match.get("status") or "",
                trigger_signal=trigger,
                context_text=f"{context_prefix} · {label}" if label else context_prefix,
                source_label=label,
            )

    for annotation in result.get("annotations") or []:
        if not isinstance(annotation, dict):
            continue
        replacement = clean_expression(annotation.get("replacement"))
        if not replacement:
            continue
        inserted += _add_evidence(
            conn,
            replacement,
            report_id=report_id,
            question_id=row["question_id"],
            source_kind="revision",
            match_status=str(annotation.get("kind") or ""),
            trigger_signal="",
            context_text=(
                f"{context_prefix} · {str(annotation.get('reason') or '').strip()}"
                if str(annotation.get("reason") or "").strip()
                else context_prefix
            ),
            wrong_expression=str(annotation.get("quote") or ""),
            source_label="原答案修订",
        )

    if not rubric:
        for candidate in _markdown_omission_candidates(row["report_text"]):
            inserted += _add_evidence(
                conn,
                candidate["expression"],
                report_id=report_id,
                question_id=row["question_id"],
                source_kind="imported",
                match_status="miss",
                trigger_signal=candidate["trigger_signal"],
                context_text=f"{context_prefix} · {candidate['context_text']}",
                source_label=candidate["source_label"],
            )

    conn.execute(
        """
        INSERT INTO normative_expression_ingest_state (report_id, signature, ingested_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(report_id) DO UPDATE SET
            signature = excluded.signature,
            ingested_at = CURRENT_TIMESTAMP
        """,
        (report_id, signature),
    )
    conn.execute(
        """
        DELETE FROM normative_expressions
         WHERE is_custom = 0
           AND NOT EXISTS (
               SELECT 1 FROM normative_expression_evidence nee
                WHERE nee.expression_id = normative_expressions.id
           )
        """
    )
    return inserted


def sync_normative_expression_history(conn, *, force: bool = False) -> dict:
    report_ids = [
        int(row["id"])
        for row in conn.execute("SELECT id FROM grading_reports WHERE status = 'ok' ORDER BY id")
    ]
    inserted = 0
    updated_reports = 0
    for report_id in report_ids:
        before = conn.total_changes
        inserted += ingest_report_normative_expressions(conn, report_id, force=force)
        if conn.total_changes > before:
            updated_reports += 1
    return {"reports": len(report_ids), "updated_reports": updated_reports, "evidence": inserted}


def _unique(values, limit=12):
    output = []
    seen = set()
    for value in values:
        value = str(value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
        if len(output) >= limit:
            break
    return output


def list_normative_expressions(conn, filters=None):
    filters = filters or {}
    q = str(filters.get("q") or "").strip()
    category = str(filters.get("category") or "").strip()
    question_type = str(filters.get("question_type") or "").strip()
    status = str(filters.get("status") or "").strip()
    source_kind = str(filters.get("source_kind") or "").strip()
    coverage = str(filters.get("coverage") or "").strip()
    clauses = []
    params = []
    if status:
        clauses.append("ne.status = ?")
        params.append(status)
    else:
        clauses.append("ne.status <> 'hidden'")
    if category:
        clauses.append("ne.category = ?")
        params.append(category)
    if question_type:
        clauses.append(
            "EXISTS (SELECT 1 FROM normative_expression_evidence e JOIN questions q2 ON q2.id = e.question_id "
            "WHERE e.expression_id = ne.id AND q2.question_type = ?)"
        )
        params.append(question_type)
    if source_kind:
        clauses.append(
            "EXISTS (SELECT 1 FROM normative_expression_evidence e WHERE e.expression_id = ne.id AND e.source_kind = ?)"
        )
        params.append(source_kind)
    if coverage == "needs_work":
        clauses.append(
            "EXISTS (SELECT 1 FROM normative_expression_evidence e WHERE e.expression_id = ne.id AND e.match_status IN ('miss', 'partial'))"
        )
    elif coverage in {"miss", "partial", "hit"}:
        clauses.append(
            "EXISTS (SELECT 1 FROM normative_expression_evidence e WHERE e.expression_id = ne.id AND e.match_status = ?)"
        )
        params.append(coverage)
    if q:
        term = f"%{q}%"
        clauses.append(
            "(ne.expression LIKE ? OR ne.category LIKE ? OR ne.note LIKE ? OR EXISTS ("
            "SELECT 1 FROM normative_expression_evidence e LEFT JOIN questions q2 ON q2.id=e.question_id "
            "WHERE e.expression_id=ne.id AND (e.trigger_signal LIKE ? OR e.context_text LIKE ? OR "
            "e.wrong_expression LIKE ? OR e.source_label LIKE ? OR q2.title LIKE ? OR q2.question_code LIKE ?)))"
        )
        params.extend([term] * 9)
    where = " AND ".join(clauses) if clauses else "1=1"
    expressions = [
        dict(row)
        for row in conn.execute(
            f"SELECT ne.* FROM normative_expressions ne WHERE {where} ORDER BY ne.is_starred DESC, ne.updated_at DESC, ne.id DESC LIMIT 600",
            params,
        )
    ]
    if not expressions:
        return []
    ids = [item["id"] for item in expressions]
    placeholders = ",".join("?" for _ in ids)
    evidence_rows = conn.execute(
        f"""
        SELECT e.*, q.question_code, q.question_type, q.title
          FROM normative_expression_evidence e
     LEFT JOIN questions q ON q.id = e.question_id
         WHERE e.expression_id IN ({placeholders})
      ORDER BY e.id DESC
        """,
        ids,
    ).fetchall()
    by_expression = {item["id"]: [] for item in expressions}
    for row in evidence_rows:
        by_expression.setdefault(row["expression_id"], []).append(dict(row))
    for item in expressions:
        evidence = by_expression.get(item["id"], [])
        item["evidence"] = evidence
        item["question_types"] = _unique(row.get("question_type") for row in evidence)
        item["trigger_signals"] = _unique((row.get("trigger_signal") for row in evidence), 6)
        item["contexts"] = _unique((row.get("context_text") for row in evidence), 6)
        item["wrong_expressions"] = _unique((row.get("wrong_expression") for row in evidence), 8)
        item["source_kinds"] = _unique((row.get("source_kind") for row in evidence), 8)
        report_ids = {row.get("report_id") for row in evidence if row.get("report_id")}
        question_ids = {row.get("question_id") for row in evidence if row.get("question_id")}
        item["source_count"] = len(report_ids) or (1 if item.get("is_custom") else 0)
        item["question_count"] = len(question_ids)
        statuses = Counter(row.get("match_status") for row in evidence)
        item["miss_count"] = statuses.get("miss", 0)
        item["partial_count"] = statuses.get("partial", 0)
        item["hit_count"] = statuses.get("hit", 0)
        item["priority"] = item["miss_count"] * 3 + item["partial_count"] * 2 + item["source_count"]
    expressions.sort(
        key=lambda item: (
            int(bool(item.get("is_starred"))),
            item.get("priority", 0),
            item.get("source_count", 0),
            item.get("updated_at") or "",
        ),
        reverse=True,
    )
    return expressions


def normative_expression_summary(conn) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status='mastered' THEN 1 ELSE 0 END) AS mastered,
               SUM(CASE WHEN is_starred=1 AND status<>'hidden' THEN 1 ELSE 0 END) AS starred,
               SUM(CASE WHEN is_custom=1 AND status<>'hidden' THEN 1 ELSE 0 END) AS custom
          FROM normative_expressions
         WHERE status <> 'hidden'
        """
    ).fetchone()
    source_row = conn.execute(
        """
        SELECT COUNT(DISTINCT report_id) AS reports,
               COUNT(DISTINCT question_id) AS questions,
               COUNT(*) AS evidence
          FROM normative_expression_evidence
        """
    ).fetchone()
    categories = [
        dict(item)
        for item in conn.execute(
            """
            SELECT category, COUNT(*) AS count
              FROM normative_expressions
             WHERE status <> 'hidden'
          GROUP BY category
          ORDER BY count DESC, category
            """
        )
    ]
    question_types = [
        row["question_type"]
        for row in conn.execute(
            """
            SELECT DISTINCT q.question_type
              FROM normative_expression_evidence e
              JOIN questions q ON q.id=e.question_id
             WHERE TRIM(q.question_type) <> ''
          ORDER BY q.question_type
            """
        )
    ]
    return {
        "total": int(row["total"] or 0),
        "mastered": int(row["mastered"] or 0),
        "starred": int(row["starred"] or 0),
        "custom": int(row["custom"] or 0),
        "reports": int(source_row["reports"] or 0),
        "questions": int(source_row["questions"] or 0),
        "evidence": int(source_row["evidence"] or 0),
        "categories": categories,
        "question_types": question_types,
    }


def top_normative_verbs(entries, limit=16):
    counts = Counter()
    for item in entries:
        text = item.get("expression") or ""
        for verb in COMMON_VERBS:
            if text.startswith(verb):
                counts[verb] += 1
                break
    return counts.most_common(limit)


def add_custom_normative_expression(conn, expression: str, category: str = "", note: str = "") -> int | None:
    expression = clean_expression(expression)
    if not expression:
        return None
    entry_id = _ensure_expression(conn, expression, category or infer_category(expression), is_custom=1)
    if entry_id and note:
        conn.execute(
            "UPDATE normative_expressions SET note=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(note).strip()[:1000], entry_id),
        )
    return entry_id


def update_normative_expression(conn, entry_id: int, *, action: str, value: str = "") -> bool:
    row = conn.execute("SELECT id FROM normative_expressions WHERE id=?", (entry_id,)).fetchone()
    if not row:
        return False
    if action == "star":
        conn.execute(
            "UPDATE normative_expressions SET is_starred=1-is_starred, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (entry_id,),
        )
    elif action == "status" and value in {"learning", "mastered", "hidden"}:
        conn.execute(
            "UPDATE normative_expressions SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (value, entry_id),
        )
    elif action == "category" and value.strip():
        conn.execute(
            "UPDATE normative_expressions SET category=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (value.strip()[:80], entry_id),
        )
    elif action == "note":
        conn.execute(
            "UPDATE normative_expressions SET note=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (value.strip()[:1000], entry_id),
        )
    else:
        return False
    return True


def export_normative_expressions_markdown(conn) -> str:
    entries = list_normative_expressions(conn, {})
    grouped = {}
    for item in entries:
        grouped.setdefault(item["category"], []).append(item)
    lines = [
        "# 申论规范表达 · 个人整理库",
        "",
        f"> 自动整理于 {datetime.now().strftime('%Y-%m-%d %H:%M')}。来源包括智能批改评分基准、答案修订与导入报告。",
        "",
    ]
    for category, items in grouped.items():
        lines.extend(
            [
                f"## {category}",
                "",
                "| 规范表述 | 材料触发信号 | 适用情境 | 常见原表述 | 来源 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for item in items:
            trigger = "；".join(item["trigger_signals"][:2]) or "—"
            context = "；".join(item["contexts"][:2]) or "—"
            wrong = "；".join(item["wrong_expressions"][:2]) or "—"
            source = f"{item['question_count']}题 / {item['source_count']}份报告"
            cells = [item["expression"], trigger, context, wrong, source]
            cells = [str(cell).replace("|", "／").replace("\n", " ") for cell in cells]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    verbs = top_normative_verbs(entries, 30)
    if verbs:
        lines.extend(["## 高频动词", "", " · ".join(f"{verb}（{count}）" for verb, count in verbs), ""])
    return "\n".join(lines)
