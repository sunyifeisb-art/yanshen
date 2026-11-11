import re

from .agent_retrieval import PROBLEM_PATTERNS
from .grading import select_relevant_materials
from .skill_graph import related_skill_context
from .statistics import build_training_statistics, parse_report_score
from .timeutils import format_beijing_time


def _dict(row):
    return dict(row) if row is not None else {}


def _clip(value, limit=420):
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _task_label(task_type):
    return {
        "diagnosis": "训练诊断",
        "review": "本题复盘",
        "recommend": "择题助手",
    }.get(task_type, "AI 训练教练")


def _weakness_profile_summary(conn):
    rows = conn.execute(
        """
        SELECT module, question_type, problem_type, frequency, severity, evidence_json, last_seen_at
          FROM agent_weakness_profile
      ORDER BY severity DESC, frequency DESC, updated_at DESC
         LIMIT 10
        """
    ).fetchall()
    profile = []
    for row in rows:
        profile.append(
            {
                "module": row["module"],
                "question_type": row["question_type"],
                "problem_type": row["problem_type"],
                "frequency": row["frequency"],
                "severity": row["severity"],
                "last_seen_at": row["last_seen_at"],
            }
        )
    return profile


def _report_history(conn, limit=12):
    rows = conn.execute(
        """
        SELECT gr.id, gr.attempt_id, gr.report_text, gr.created_at,
               q.question_type
          FROM grading_reports gr
          JOIN attempts a ON a.id = gr.attempt_id
          JOIN questions q ON q.id = a.question_id
      ORDER BY gr.created_at DESC, gr.id DESC
         LIMIT ?
        """,
        (max(4, int(limit) * 3),),
    ).fetchall()
    latest_per_attempt = []
    seen_attempts = set()
    for row in rows:
        attempt_id = row["attempt_id"]
        if attempt_id in seen_attempts:
            continue
        seen_attempts.add(attempt_id)
        latest_per_attempt.append(_dict(row))
        if len(latest_per_attempt) >= limit:
            break
    return latest_per_attempt


def _performance_trend(report_history):
    scores = []
    for report in report_history:
        score = parse_report_score(report.get("report_text") or "")
        if score is not None:
            scores.append(float(score))
    if len(scores) < 4:
        return {
            "direction": "insufficient",
            "recent_average": None,
            "previous_average": None,
            "delta": None,
            "sample_count": len(scores),
        }
    window = min(3, len(scores) // 2)
    recent = scores[:window]
    previous = scores[window : window * 2]
    recent_average = round(sum(recent) / len(recent), 1)
    previous_average = round(sum(previous) / len(previous), 1)
    delta = round(recent_average - previous_average, 1)
    if delta >= 2.0:
        direction = "improving"
    elif delta <= -2.0:
        direction = "worsening"
    else:
        direction = "stable"
    return {
        "direction": direction,
        "recent_average": recent_average,
        "previous_average": previous_average,
        "delta": delta,
        "sample_count": len(recent) + len(previous),
    }


def _weakness_trends(report_history, weakness_profile):
    if len(report_history) < 4:
        return []
    window = min(4, len(report_history) // 2)
    recent = report_history[:window]
    previous = report_history[window : window * 2]
    trends = []
    strict_patterns = {
        "材料遗漏": (r"材料.{0,8}(遗漏|漏掉|漏写|缺少)", r"(遗漏|漏掉|漏写).{0,8}(材料|信息点)", r"信息点.{0,6}(不全|缺失)"),
        "采分点遗漏": (r"采分点.{0,8}(遗漏|漏掉|缺少|不全)", r"(要点|核心点|得分点).{0,8}(遗漏|漏掉|不全|缺失)", r"点不全"),
        "结构表达": (r"结构.{0,8}(混乱|松散|不清|不当|有问题|不足)", r"(层次|逻辑|条理).{0,8}(不清|混乱|不足|较弱|欠缺)", r"段落.{0,8}(失衡|混乱|问题)"),
        "审题偏差": (r"审题.{0,8}(偏差|错误|不准|不清|不足)", r"(对象|任务|范围).{0,8}(理解错误|判断错误|偏离|搞错)"),
        "语言概括": (r"(概括|表达|表述).{0,8}(不准|不够|冗长|啰嗦|欠缺|问题)", r"(不够|欠缺).{0,5}(凝练|准确|简洁)", r"语言.{0,8}(冗余|拖沓|问题)"),
        "格式规范": (r"格式.{0,8}(错误|不规范|有误|问题)", r"(称谓|落款|文种).{0,8}(错误|不当|缺失|有误)"),
        "对策针对性": (r"(对策|措施).{0,10}(针对性不足|不够针对|脱离材料|不具体|空泛|可操作性不足)", r"缺乏.{0,5}(针对性|可操作性)", r"针对性.{0,8}(不足|不强|不够)"),
    }
    for weakness in weakness_profile[:6]:
        problem_type = weakness.get("problem_type") or ""
        patterns = strict_patterns.get(problem_type)
        keywords = PROBLEM_PATTERNS.get(problem_type, (problem_type,))

        def rate(rows):
            hits = 0
            for row in rows:
                text = row.get("report_text") or ""
                matched = (
                    any(re.search(pattern, text) for pattern in patterns)
                    if patterns
                    else any(keyword and keyword in text for keyword in keywords)
                )
                if matched:
                    hits += 1
            return hits / len(rows) if rows else 0.0

        recent_rate = rate(recent)
        previous_rate = rate(previous)
        delta = round(recent_rate - previous_rate, 3)
        if recent_rate == 0 and previous_rate == 0:
            direction = "not_observed"
        elif delta <= -0.25:
            direction = "improving"
        elif delta >= 0.25:
            direction = "worsening"
        else:
            direction = "stable"
        trends.append(
            {
                "problem_type": problem_type,
                "direction": direction,
                "recent_rate": round(recent_rate, 2),
                "previous_rate": round(previous_rate, 2),
                "delta": delta,
                "recent_samples": len(recent),
                "previous_samples": len(previous),
            }
        )
    return trends


def load_user_context(conn):
    stats = build_training_statistics(conn)
    recent_attempts = [
        _dict(row)
        for row in conn.execute(
            """
            SELECT a.id, a.question_id, a.word_count, a.created_at,
                   q.title, q.question_type, q.year, q.region, q.exam_type,
                   COUNT(gr.id) AS report_count
              FROM attempts a
              JOIN questions q ON q.id = a.question_id
         LEFT JOIN grading_reports gr ON gr.attempt_id = a.id
          GROUP BY a.id
          ORDER BY a.created_at DESC, a.id DESC
             LIMIT 8
            """
        )
    ]
    recent_notes = [
        {
            "attempt_id": row["id"],
            "question_id": row["question_id"],
            "title": row["title"],
            "note": _clip(row["personal_note"], 180),
            "created_at": row["created_at"],
        }
        for row in conn.execute(
            """
            SELECT a.id, a.question_id, a.personal_note, a.created_at, q.title
              FROM attempts a
              JOIN questions q ON q.id = a.question_id
             WHERE TRIM(a.personal_note) <> ''
          ORDER BY a.created_at DESC, a.id DESC
             LIMIT 5
            """
        )
    ]
    weakness_profile = _weakness_profile_summary(conn)
    report_history = _report_history(conn)
    skill_gaps = []
    seen_skills = set()
    for weakness in weakness_profile:
        for row in related_skill_context(conn, "error", weakness.get("problem_type"), limit=4):
            item = _dict(row)
            skill_key = item.get("skill_key")
            if not skill_key or skill_key in seen_skills:
                continue
            seen_skills.add(skill_key)
            item["problem_type"] = weakness.get("problem_type")
            item["severity"] = weakness.get("severity")
            skill_gaps.append(item)
    return {
        "summary": {
            "attempt_count": stats["attempt_count"],
            "question_count": stats["question_count"],
            "report_count": stats["report_count"],
            "active_days": stats["active_days"],
            "streak": stats["streak"],
            "average_score": stats["average_score"],
            "recognized_scores": stats["recognized_scores"],
            "favorite_questions": stats["favorite_questions"],
            "favorite_papers": stats["favorite_papers"],
        },
        "type_stats": stats["type_stats"],
        "regions": stats["regions"][:8],
        "recent_attempts": recent_attempts,
        "recent_notes": recent_notes,
        "weakness_profile": weakness_profile,
        "weakness_trends": _weakness_trends(report_history, weakness_profile),
        "performance_trend": _performance_trend(report_history),
        "skill_gaps": skill_gaps[:12],
    }


def _candidate_personalization(conn, candidate, weakness_profile):
    skill_targets = candidate.get("skill_targets") or []
    candidate_skill_weights = {
        item.get("skill_key"): float(item.get("weight") or 0)
        for item in skill_targets
        if item.get("skill_key")
    }
    desired_skill_weights = {}
    desired_skill_problems = {}
    problem_matches = []
    for weakness in weakness_profile[:8]:
        severity = max(0.0, min(1.0, float(weakness.get("severity") or 0)))
        frequency = max(0, int(weakness.get("frequency") or 0))
        importance = severity * min(1.0, 0.35 + frequency / 6.0)
        if weakness.get("question_type") and weakness.get("question_type") == candidate.get("question_type"):
            problem_matches.append((importance, weakness.get("problem_type") or ""))
        for skill in related_skill_context(conn, "error", weakness.get("problem_type"), limit=5):
            key = skill["skill_key"]
            desired_skill_weights[key] = max(
                desired_skill_weights.get(key, 0.0),
                importance * float(skill["weight"] or 0),
            )
            desired_skill_problems.setdefault(key, set()).add(weakness.get("problem_type") or "")
    matched_skills = []
    skill_match_score = 0.0
    for skill in skill_targets:
        key = skill.get("skill_key")
        desired = desired_skill_weights.get(key, 0.0)
        if not desired:
            continue
        contribution = desired * max(0.0, min(1.0, candidate_skill_weights.get(key, 0.0)))
        skill_match_score += contribution
        matched_skills.append((contribution, skill.get("label") or key))
    skill_match_score = min(1.0, skill_match_score / 2.2)
    problem_match_score = min(1.0, sum(item[0] for item in problem_matches[:3]))
    attempt_count = max(0, int(candidate.get("attempt_count") or 0))
    novelty_score = 1.0 if attempt_count == 0 else max(0.0, 0.45 - attempt_count * 0.12)
    relevance = max(0.0, min(1.0, float(candidate.get("zhejiang_relevance") or 0) / 5.0))
    coach_score = round(
        skill_match_score * 0.52
        + problem_match_score * 0.23
        + novelty_score * 0.20
        + relevance * 0.05,
        4,
    )
    reasons = []
    for _, problem in sorted(problem_matches, reverse=True)[:2]:
        if problem and f"针对短板：{problem}" not in reasons:
            reasons.append(f"针对短板：{problem}")
    for _, label in sorted(matched_skills, reverse=True)[:2]:
        if label:
            reasons.append(f"训练能力：{label}")
    matched_problem_names = []
    for key in candidate_skill_weights:
        if key not in desired_skill_weights:
            continue
        matched_problem_names.extend(
            name for name in desired_skill_problems.get(key, set()) if name
        )
    for problem in matched_problem_names:
        reason = f"针对短板：{problem}"
        if reason not in reasons:
            reasons.insert(0, reason)
        if len(reasons) >= 4:
            break
    if attempt_count == 0:
        reasons.append("尚未作答，可作为新样本")
    return coach_score, reasons[:4]


def _diversify_candidates(candidates, limit):
    remaining = list(candidates)
    selected = []
    type_counts = {}
    reason_counts = {}
    while remaining and len(selected) < limit:
        def adjusted_score(item):
            question_type = item.get("question_type") or ""
            primary_reason = next(
                (reason for reason in item.get("match_reasons") or [] if reason.startswith("针对短板：")),
                "",
            )
            return (
                float(item.get("coach_score") or 0)
                - 0.12 * type_counts.get(question_type, 0)
                - 0.08 * reason_counts.get(primary_reason, 0)
            )

        best_index = max(
            range(len(remaining)),
            key=lambda index: (
                adjusted_score(remaining[index]),
                int(remaining[index].get("zhejiang_relevance") or 0),
                int(remaining[index].get("year") or 0),
                int(remaining[index].get("id") or 0),
            ),
        )
        item = remaining.pop(best_index)
        selected.append(item)
        question_type = item.get("question_type") or ""
        type_counts[question_type] = type_counts.get(question_type, 0) + 1
        primary_reason = next(
            (reason for reason in item.get("match_reasons") or [] if reason.startswith("针对短板：")),
            "",
        )
        if primary_reason:
            reason_counts[primary_reason] = reason_counts.get(primary_reason, 0) + 1
    return selected


def retrieve_candidates(conn, filters=None, limit=8):
    filters = filters or {}
    clauses = ["1 = 1"]
    params = []
    question_type = (filters.get("question_type") or "").strip()
    exclude_question_type = (filters.get("exclude_question_type") or "").strip()
    region = (filters.get("region") or "").strip()
    work_status = (filters.get("work_status") or "").strip()
    q = (filters.get("q") or "").strip()
    if question_type:
        clauses.append("q.question_type = ?")
        params.append(question_type)
    if exclude_question_type:
        clauses.append("q.question_type <> ?")
        params.append(exclude_question_type)
    if region:
        clauses.append("q.region = ?")
        params.append(region)
    if q:
        clauses.append("(q.title LIKE ? OR q.prompt LIKE ? OR q.paper_name LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    having = ""
    if work_status == "unattempted":
        having = "HAVING attempt_count = 0"
    elif work_status == "ungraded":
        having = "HAVING report_count = 0"
    elif work_status == "graded":
        having = "HAVING report_count > 0"
    elif work_status == "attempted":
        having = "HAVING attempt_count > 0"
    pool_limit = max(int(limit), min(80, max(24, int(limit) * 8)))
    rows = conn.execute(
        f"""
        SELECT q.id, q.question_code, q.title, q.question_type, q.year, q.region,
               q.exam_type, q.paper_name, q.question_number, q.word_limit,
               q.zhejiang_relevance, COUNT(DISTINCT a.id) AS attempt_count,
               COUNT(DISTINCT gr.id) AS report_count
          FROM questions q
     LEFT JOIN attempts a ON a.question_id = q.id
     LEFT JOIN grading_reports gr ON gr.attempt_id = a.id
         WHERE {' AND '.join(clauses)}
      GROUP BY q.id
        {having}
      ORDER BY report_count ASC,
               attempt_count ASC,
               q.zhejiang_relevance DESC,
               q.year DESC,
               q.id DESC
         LIMIT ?
        """,
        (*params, pool_limit),
    ).fetchall()
    weakness_profile = _weakness_profile_summary(conn)
    candidates = []
    for row in rows:
        candidate = _dict(row)
        candidate["skill_targets"] = [
            _dict(skill)
            for skill in related_skill_context(conn, "question", candidate["id"], limit=8)
        ]
        coach_score, match_reasons = _candidate_personalization(conn, candidate, weakness_profile)
        candidate["coach_score"] = coach_score
        candidate["match_reasons"] = match_reasons
        candidates.append(candidate)
    candidates.sort(
        key=lambda item: (
            float(item.get("coach_score") or 0),
            -int(item.get("attempt_count") or 0),
            -int(item.get("report_count") or 0),
            int(item.get("zhejiang_relevance") or 0),
            int(item.get("year") or 0),
            int(item.get("id") or 0),
        ),
        reverse=True,
    )
    limit = max(1, int(limit))
    if not question_type and limit > 1:
        return _diversify_candidates(candidates, limit)
    return candidates[:limit]


def get_attempt_review_context(conn, attempt_id):
    attempt = conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
    if not attempt:
        return {}
    question = conn.execute(
        "SELECT * FROM questions WHERE id = ?",
        (attempt["question_id"],),
    ).fetchone()
    references = conn.execute(
        "SELECT organization, answer_text, scoring_points FROM reference_answers WHERE question_id = ? ORDER BY organization LIMIT 6",
        (question["id"],),
    ).fetchall()
    materials = conn.execute(
        "SELECT * FROM paper_materials WHERE paper_id = ? ORDER BY material_number",
        (question["paper_id"],),
    ).fetchall() if question["paper_id"] else []
    relevant_materials = select_relevant_materials(question, materials)
    reports = conn.execute(
        "SELECT * FROM grading_reports WHERE attempt_id = ? ORDER BY created_at DESC, id DESC LIMIT 3",
        (attempt_id,),
    ).fetchall()
    latest_score = None
    for report in reports:
        latest_score = parse_report_score(report["report_text"])
        if latest_score is not None:
            break
    return {
        "attempt": {
            "id": attempt["id"],
            "question_id": attempt["question_id"],
            "answer_text": _clip(attempt["answer_text"], 1600),
            "word_count": attempt["word_count"],
            "personal_note": _clip(attempt["personal_note"], 700),
            "created_at": format_beijing_time(attempt["created_at"]),
        },
        "question": {
            "id": question["id"],
            "question_code": question["question_code"],
            "title": question["title"],
            "question_type": question["question_type"],
            "year": question["year"],
            "region": question["region"],
            "exam_type": question["exam_type"],
            "paper_name": question["paper_name"],
            "question_number": question["question_number"],
            "prompt": _clip(question["prompt"], 900),
            "requirements": _clip(question["requirements"], 600),
            "word_limit": question["word_limit"],
        },
        "materials": [
            {
                "material_number": row["material_number"],
                "title": row["title"],
                "content": _clip(row["content"], 900),
            }
            for row in relevant_materials[:6]
        ],
        "references": [
            {
                "organization": row["organization"],
                "answer_text": _clip(row["answer_text"], 700),
                "scoring_points": _clip(row["scoring_points"], 500),
            }
            for row in references
        ],
        "reports": [
            {
                "provider": row["provider"],
                "model": row["model"],
                "created_at": format_beijing_time(row["created_at"]),
                "report_text": _clip(row["report_text"], 1200),
            }
            for row in reports
        ],
        "latest_score": latest_score,
    }


def get_attempts_review_context(conn, attempt_ids):
    contexts = []
    seen = set()
    for attempt_id in attempt_ids or []:
        if attempt_id in seen:
            continue
        seen.add(attempt_id)
        context = get_attempt_review_context(conn, attempt_id)
        if context:
            contexts.append(context)
    if not contexts:
        return {}
    if len(contexts) == 1:
        return contexts[0]
    return {
        "mode": "multi_attempt_review",
        "requested_attempt_count": len(attempt_ids or []),
        "loaded_attempt_count": len(contexts),
        "attempt_reviews": contexts,
    }


def input_summary(task_type, user_context=None, candidates=None, review_context=None):
    user_context = user_context or {}
    candidates = candidates or []
    summary = user_context.get("summary", {})
    parts = [
        f"任务：{_task_label(task_type)}",
        f"累计作答：{summary.get('attempt_count', 0)} 次",
        f"已练题目：{summary.get('question_count', 0)} 道",
        f"批改报告：{summary.get('report_count', 0)} 份",
    ]
    if summary.get("average_score") is not None:
        parts.append(f"AI 百分制均分：{summary['average_score']}")
    if review_context and review_context.get("attempt_reviews"):
        titles = []
        for item in review_context["attempt_reviews"][:3]:
            question = item.get("question") or {}
            if question:
                titles.append(f"{question['question_code']} {question['title']}")
        if titles:
            parts.append(f"复盘题目：{'；'.join(titles)}")
    elif review_context and review_context.get("question"):
        question = review_context["question"]
        parts.append(f"复盘题目：{question['question_code']} {question['title']}")
    if candidates:
        parts.append(f"候选题：{len(candidates)} 道")
    return "；".join(parts)
