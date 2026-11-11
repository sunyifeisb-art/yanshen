"""Native current-affairs / political-theory quiz pages and endpoints."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import Counter

from ...ai import AiConfigError, AiRequestError
from ...ai_config import load_grading_settings
from ...political_quiz import (
    DEFAULT_TRAP_TYPES,
    build_attempt_analysis,
    complete_quiz_attempt,
    create_quiz_attempt,
    generate_quiz_pack,
    get_quiz_attempt,
    get_quiz_pack,
    list_quiz_packs,
    mark_quiz_pack_verified,
    record_quiz_generation_failure,
    save_quiz_pack,
    update_quiz_attempt,
)
from ..runtime import connect, esc, format_beijing_time, layout, parse_qs

logger = logging.getLogger(__name__)


PROVINCES = (
    "北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "内蒙古",
    "广西", "西藏", "宁夏", "新疆",
)


_GENERATION_JOBS = {}
_GENERATION_JOBS_LOCK = threading.Lock()


def _generation_job_payload(job):
    if not job:
        return None
    checkpoint = job.get("checkpoint") if isinstance(job.get("checkpoint"), dict) else {}
    return {
        "ok": True,
        "job_id": job["job_id"],
        "status": job["status"],
        "progress": int(job.get("progress") or 0),
        "message": str(job.get("message") or ""),
        "phase": str(job.get("phase") or ""),
        "redirect": str(job.get("redirect") or ""),
        "error": str(job.get("error") or ""),
        "resume_available": bool(job.get("status") == "failed" and checkpoint),
        "resume_phase": str(checkpoint.get("phase") or ""),
    }


def _create_generation_job(options=None):
    job_id = uuid.uuid4().hex
    now = time.monotonic()
    job = {
        "job_id": job_id,
        "status": "queued",
        "progress": 3,
        "message": "生成任务已创建，正在准备命题参数…",
        "phase": "queued",
        "redirect": "",
        "error": "",
        "options": dict(options or {}),
        "checkpoint": {},
        "updated_at": now,
    }
    with _GENERATION_JOBS_LOCK:
        # Keep the in-memory registry bounded during long-running app sessions.
        stale = [key for key, value in _GENERATION_JOBS.items() if now - value.get("updated_at", now) > 7200]
        for key in stale:
            _GENERATION_JOBS.pop(key, None)
        _GENERATION_JOBS[job_id] = job
    return _generation_job_payload(job)


def _update_generation_job(job_id, **changes):
    with _GENERATION_JOBS_LOCK:
        job = _GENERATION_JOBS.get(job_id)
        if job is None:
            return None
        if "progress" in changes:
            changes["progress"] = max(int(job.get("progress") or 0), min(100, int(changes["progress"])))
        job.update(changes)
        job["updated_at"] = time.monotonic()
        return _generation_job_payload(job)


def _get_generation_job(job_id):
    with _GENERATION_JOBS_LOCK:
        return _generation_job_payload(_GENERATION_JOBS.get(job_id))


def _set_generation_checkpoint(job_id, checkpoint):
    if not isinstance(checkpoint, dict):
        return None
    with _GENERATION_JOBS_LOCK:
        job = _GENERATION_JOBS.get(job_id)
        if job is None:
            return None
        previous = job.get("checkpoint") if isinstance(job.get("checkpoint"), dict) else {}
        previous_progress = int(previous.get("progress") or 0)
        next_progress = int(checkpoint.get("progress") or 0)
        if next_progress >= previous_progress:
            job["checkpoint"] = dict(checkpoint)
        job["updated_at"] = time.monotonic()
        return _generation_job_payload(job)


def _resume_generation_job(job_id, options):
    with _GENERATION_JOBS_LOCK:
        job = _GENERATION_JOBS.get(job_id)
        if job is None or job.get("status") != "failed":
            return None, None
        if dict(job.get("options") or {}) != dict(options or {}):
            return None, None
        checkpoint = job.get("checkpoint") if isinstance(job.get("checkpoint"), dict) else {}
        if not checkpoint:
            return None, None
        progress = max(int(job.get("progress") or 0), int(checkpoint.get("progress") or 0))
        phase = str(checkpoint.get("phase") or job.get("phase") or "queued")
        job.update(
            {
                "status": "queued",
                "progress": progress,
                "message": f"正在从上次 {progress}% 的中断位置继续生成…",
                "phase": phase,
                "redirect": "",
                "error": "",
                "updated_at": time.monotonic(),
            }
        )
        return _generation_job_payload(job), dict(checkpoint)


def _run_generation_job(db_path, settings, job_id, options, resume_state=None):
    if resume_state:
        resume_progress = int(resume_state.get("progress") or 0)
        resume_phase = str(resume_state.get("phase") or "prepare")
        _update_generation_job(
            job_id,
            status="running",
            progress=resume_progress,
            message=f"已恢复上次进度，正在从 {resume_progress}% 继续…",
            phase=resume_phase,
        )
    else:
        _update_generation_job(
            job_id,
            status="running",
            progress=5,
            message="正在启动联网命题流程…",
            phase="prepare",
        )

    def on_progress(payload):
        if not isinstance(payload, dict):
            return
        _update_generation_job(
            job_id,
            status="running",
            progress=payload.get("progress", 5),
            message=payload.get("message", "正在生成题包…"),
            phase=payload.get("phase", "running"),
        )

    try:
        pack, prompt, _raw = generate_quiz_pack(
            settings,
            exam_scope=options["exam_scope"],
            province=options["province"],
            period=options["period"],
            period_start=options["period_start"],
            period_end=options["period_end"],
            question_count=options["question_count"],
            progress_callback=on_progress,
            resume_state=resume_state,
            checkpoint_callback=lambda state: _set_generation_checkpoint(job_id, state),
        )
        _update_generation_job(job_id, progress=98, message="质检通过，正在写入本机题库…", phase="save")
        with connect(db_path) as conn:
            pack_id = save_quiz_pack(
                conn,
                pack,
                verification_status="generated",
                generation_prompt=prompt,
            )
        redirect = f"/political-quiz/packs/{pack_id}?mode={options['mode']}"
        _update_generation_job(
            job_id,
            status="completed",
            progress=100,
            message="题包已生成并通过结构质检，正在打开…",
            phase="completed",
            redirect=redirect,
        )
    except (AiConfigError, AiRequestError, ValueError) as exc:
        _fail_generation_job(db_path, job_id, options, str(exc), expected=True)
    except Exception as exc:  # pragma: no cover - defensive guard for background worker failures.
        _fail_generation_job(db_path, job_id, options, f"生成任务异常：{exc}", expected=False)


def _fail_generation_job(db_path, job_id, options, message, *, expected):
    """Mark the job failed and make the reason recoverable after the job is gone."""
    previous = _get_generation_job(job_id) or {}
    _update_generation_job(
        job_id,
        status="failed",
        message=message,
        phase="failed",
        error=message,
    )
    context = (
        f"job={job_id} phase={previous.get('phase')} progress={previous.get('progress')} "
        f"scope={options.get('exam_scope')} province={options.get('province')} "
        f"period={options.get('period_start')}~{options.get('period_end')} "
        f"question_count={options.get('question_count')}"
    )
    if expected:
        logger.warning("Political quiz generation failed: %s | %s", context, message)
    else:
        logger.exception("Political quiz generation crashed: %s | %s", context, message)
    try:
        with connect(db_path) as conn:
            record_quiz_generation_failure(
                conn,
                error=f"{message}\n{context}",
                options=options,
            )
    except Exception:  # pragma: no cover - never let diagnostics mask the real failure.
        logger.exception("Failed to persist political quiz generation error: %s", context)


def _row_pack(row):
    if row is None:
        return None
    try:
        data = json.loads(row["data_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        data = {}
    return data


def _pack_badge(row):
    if row["verification_status"] == "verified":
        return '<span class="political-pack-status verified">✓ 已核验</span>'
    return '<span class="political-pack-status generated">联网生成 · 待人工核验</span>'


def _topic_chips(data, limit=7):
    counts = Counter(
        str(question.get("topic") or "其他")
        for question in data.get("questions") or []
        if isinstance(question, dict)
    )
    if not counts:
        return '<span class="political-topic-chip muted">等待题包数据</span>'
    return "".join(
        f'<span class="political-topic-chip">{esc(topic)} <b>{count}</b></span>'
        for topic, count in counts.most_common(limit)
    )


def _recent_attempts_html(rows):
    if not rows:
        return '<div class="political-empty"><strong>还没有时政测验记录</strong><p>完成第一套后，这里会保留正确率、模式和复盘入口。</p></div>'
    items = []
    for row in rows:
        total = int(row["question_count"] or 0)
        correct = int(row["correct_count"] or 0)
        accuracy = round(correct * 100 / total) if total else 0
        mode = "考试" if row["mode"] == "exam" else "练习"
        state = "已完成" if row["completed_at"] else "未完成"
        items.append(
            f"""
            <a class="political-recent-row" href="/political-quiz/packs/{row['pack_id']}?mode={esc(row['mode'])}&attempt={row['id']}">
              <span><strong>{esc(row['title'])}</strong><small>{esc(row['period_label'] or '')} · {mode}模式 · {esc(format_beijing_time(row['created_at']))}</small></span>
              <span class="political-recent-score"><b>{accuracy}%</b><small>{state}</small></span>
              <i aria-hidden="true">→</i>
            </a>
            """
        )
    return "".join(items)


def _trap_radar_html(counts):
    peak = max(counts.values(), default=0)
    rows = []
    for name in DEFAULT_TRAP_TYPES:
        count = int(counts.get(name, 0))
        width = 0 if peak == 0 else max(5 if count else 0, round(count * 100 / peak))
        rows.append(
            f'<div class="political-trap-row"><span>{esc(name)}</span><i><b style="width:{width}%"></b></i><strong>{count}</strong></div>'
        )
    return "".join(rows)


def _pack_json_script(data):
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f'<script type="application/json" data-quiz-pack-json>{payload}</script>'


def _analysis_json_script(data):
    if not data:
        return ""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f'<script type="application/json" data-quiz-analysis-json>{payload}</script>'


def _pack_library_html(rows, featured_id=None, limit=8):
    candidates = [
        row
        for row in rows
        if row["status"] == "ready" and int(row["id"]) != int(featured_id or -1)
    ][: max(1, int(limit))]
    if not candidates:
        return '<div class="political-empty"><strong>暂无其他题包</strong><p>之后生成或导入的题包会保留在这里，可以随时重新练习。</p></div>'
    cards = []
    for row in candidates:
        if row["exam_scope"] == "province":
            scope = f"{row['province'] or '省考'}"
        else:
            scope = "国考"
        cards.append(
            f"""
            <article class="political-pack-card">
              <div class="political-pack-card-top"><span>{esc(scope)}</span>{_pack_badge(row)}</div>
              <h3>{esc(row['title'])}</h3>
              <p>{esc(row['period_label'] or '近期')} · {row['question_count']} 题 · {row['source_count']} 个官方来源</p>
              <div class="political-pack-card-actions">
                <a href="/political-quiz/packs/{row['id']}?mode=practice">练习</a>
                <a href="/political-quiz/packs/{row['id']}?mode=exam">考试</a>
              </div>
            </article>
            """
        )
    return "".join(cards)


def _verification_callout(row, data, attempt_id, mode):
    if row["verification_status"] == "verified":
        return ""
    sources = []
    seen = set()
    for question in data.get("questions") or []:
        if not isinstance(question, dict):
            continue
        source = question.get("source") or {}
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "").strip()
        if not re.match(r"^https?://", url, re.I) or url in seen:
            continue
        seen.add(url)
        name = str(source.get("name") or "官方来源").strip()
        date = str(source.get("date") or "").strip()
        label = f"{name} · {date}" if date else name
        sources.append(f'<a href="{esc(url)}" target="_blank" rel="noreferrer">{esc(label)} ↗</a>')
    source_html = "".join(sources) or "<span>题包未提供可打开的来源链接</span>"
    return f"""
    <section class="political-verification-callout">
      <div>
        <p class="eyebrow">HUMAN VERIFICATION</p>
        <h2>人工核验闭环</h2>
        <p>这套题来自联网生成，结构质检已通过，但政治表述仍需人工确认。逐题核对官方原文后，再将它升级为“已核验”题包。</p>
        <details><summary>查看 {len(sources)} 个官方来源</summary><div class="political-verification-sources">{source_html}</div></details>
      </div>
      <form method="post" action="/political-quiz/packs/{row['id']}/verify">
        <input type="hidden" name="mode" value="{esc(mode)}">
        <input type="hidden" name="attempt" value="{int(attempt_id)}">
        <button class="button primary" type="submit">我已逐题核对来源，标记已核验</button>
        <small>该操作只改变本机题包的核验状态。</small>
      </form>
    </section>
    """


class PoliticalQuizController:
    def page_political_quiz(self, query=None):
        query = query or {}
        exam_scope = query.get("exam_scope", ["national"])[0]
        if exam_scope not in {"national", "province"}:
            exam_scope = "national"
        province = query.get("province", [""])[0].strip()
        mode = query.get("mode", ["practice"])[0]
        if mode not in {"practice", "exam"}:
            mode = "practice"

        with connect(self.db_path) as conn:
            packs = list_quiz_packs(conn, limit=30)
            recent = conn.execute(
                """
                SELECT a.*, p.title, p.period_label
                  FROM political_quiz_attempts a
                  JOIN political_quiz_packs p ON p.id = a.pack_id
              ORDER BY a.created_at DESC, a.id DESC
                 LIMIT 6
                """
            ).fetchall()
            completed = conn.execute(
                """
                SELECT a.answers_json, p.data_json
                  FROM political_quiz_attempts a
                  JOIN political_quiz_packs p ON p.id = a.pack_id
                 WHERE a.completed_at IS NOT NULL
              ORDER BY a.completed_at DESC, a.id DESC
                 LIMIT 30
                """
            ).fetchall()

        matching = [
            row
            for row in packs
            if row["status"] == "ready"
            and row["exam_scope"] == exam_scope
            and (exam_scope != "province" or not province or row["province"] == province)
        ]
        verified = next((row for row in matching if row["verification_status"] == "verified"), None)
        featured = verified or (matching[0] if matching else None)
        featured_data = _row_pack(featured) if featured else {}
        featured_id = int(featured["id"]) if featured else None

        trap_counts = Counter()
        for row in completed:
            try:
                pack = json.loads(row["data_json"] or "{}")
                answers = json.loads(row["answers_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            for name, count in build_attempt_analysis(pack, answers)["trap_errors"].items():
                trap_counts[name] += int(count or 0)

        if featured:
            featured_label = "本期核验题" if featured["verification_status"] == "verified" else "最近可用题包"
            featured_action = "开始本期核验题" if featured["verification_status"] == "verified" else "开始最近题包"
            featured_html = f"""
            <article class="political-feature-card">
              <header><div><p class="eyebrow">{featured_label}</p><h2>{esc(featured['title'])}</h2></div>{_pack_badge(featured)}</header>
              <p class="political-feature-meta">{esc(featured['period_label'] or '近期')} · {featured['question_count']} 题 · {featured['source_count']} 个官方来源</p>
              <div class="political-topic-list">{_topic_chips(featured_data)}</div>
              <div class="political-feature-actions">
                <a class="button primary" data-political-start href="/political-quiz/packs/{featured['id']}?mode={esc(mode)}">{featured_action}</a>
                <a class="button ghost" href="/political-quiz/packs/{featured['id']}?mode=exam">考试模式进入</a>
              </div>
            </article>
            """
        else:
            featured_html = """
            <article class="political-feature-card empty">
              <header><div><p class="eyebrow">本期题包</p><h2>还没有可用的本期测验</h2></div><span class="political-pack-status generated">等待生成</span></header>
              <p>点击右侧“生成最新测验”，联网检索官方来源并按国考政治理论题型生成。生成结果默认标记为“待人工核验”。</p>
            </article>
            """

        province_options = '<option value="">选择省份</option>' + "".join(
            f'<option value="{esc(name)}"{" selected" if name == province else ""}>{esc(name)}</option>'
            for name in PROVINCES
        )
        body = f"""
        <div class="political-quiz-home" data-political-quiz-page="home">
          <section class="political-hero">
            <div><p class="eyebrow">POLITICAL THEORY · CURRENT AFFAIRS</p><h1>时政测验</h1><p>按国考政治理论命题逻辑训练：材料化题干、组合/计数/选非/选是、逐条解析与设误点复盘。</p></div>
            <div class="political-hero-note"><strong>政治表述先核验，再出题</strong><span>官方来源 · 错项关键字 · 本机保存</span></div>
          </section>

          <section class="political-launch-grid">
            {featured_html}
            <form class="political-generator-card" data-political-generator action="/political-quiz/generate" method="post">
              <div class="political-card-heading"><div><p class="eyebrow">LIVE RESEARCH</p><h2>生成最新测验</h2></div><span class="political-live-dot">联网</span></div>
              <p>调用本机 ChatGPT/Codex 联网搜索，只从官方来源取政治表述。生成后先标记“待人工核验”。</p>
              <div class="political-mode-switch" role="group" aria-label="答题模式">
                <label><input type="radio" name="mode" value="practice"{" checked" if mode == "practice" else ""}><span><strong>练习模式</strong><small>逐题判分</small></span></label>
                <label><input type="radio" name="mode" value="exam"{" checked" if mode == "exam" else ""}><span><strong>考试模式</strong><small>交卷后解析</small></span></label>
              </div>
              <div class="political-generator-grid">
                <label><span>考试范围</span><select name="exam_scope" data-political-exam-scope><option value="national"{" selected" if exam_scope == "national" else ""}>国考</option><option value="province"{" selected" if exam_scope == "province" else ""}>省考</option></select></label>
                <label data-political-province-field{"" if exam_scope == "province" else " hidden"}><span>省份</span><select name="province">{province_options}</select></label>
                <label><span>快捷时间</span><select data-political-period-preset><option value="this_month">本月</option><option value="last_month">上月</option><option value="last_3_months">近3个月</option><option value="first_half">今年上半年</option><option value="second_half">今年下半年</option><option value="this_year">今年全年</option><option value="custom">自定义</option></select></label>
                <label><span>题量</span><select name="question_count"><option value="10">10题</option><option value="20" selected>20题</option><option value="30">30题</option></select></label>
                <div class="political-period-range">
                  <label><span>起始月份</span><input type="month" name="period_start" data-political-period-start required></label>
                  <span class="political-period-arrow" aria-hidden="true">→</span>
                  <label><span>结束月份</span><input type="month" name="period_end" data-political-period-end required></label>
                </div>
                <input type="hidden" name="period" value="custom">
              </div>
              <button class="button primary political-generate-button" type="submit"><span>生成最新测验</span><small>联网研究后自动质检</small></button>
              <div class="political-generation-progress" data-political-generation-progress hidden>
                <div class="political-generation-progress-head"><strong data-political-generation-message>准备生成…</strong><span data-political-generation-percent>0%</span></div>
                <i class="political-generation-track"><b data-political-generation-fill></b></i>
                <small data-political-generation-phase>等待启动</small>
              </div>
              <p class="political-generator-status" data-political-generator-status>实时生成需在设置中连接 ChatGPT/Codex 账号。</p>
            </form>
          </section>

          <section class="political-home-lower">
            <article class="political-recent-card">
              <div class="political-card-heading"><div><p class="eyebrow">RECENT QUIZZES</p><h2>最近测验</h2></div></div>
              <div class="political-recent-list">{_recent_attempts_html(recent)}</div>
            </article>
            <article class="political-radar-card">
              <div class="political-card-heading"><div><p class="eyebrow">TRAP RADAR</p><h2>易错雷达</h2></div><span>累计</span></div>
              <div class="political-trap-radar">{_trap_radar_html(trap_counts)}</div>
              <p>先看自己最常掉进哪类干扰项，再去背具体表述。</p>
            </article>
          </section>

          <section class="political-pack-library">
            <div class="political-card-heading"><div><p class="eyebrow">QUIZ LIBRARY</p><h2>历史题包</h2></div><span>保留最近可用题包</span></div>
            <div class="political-pack-grid">{_pack_library_html(packs, featured_id=featured_id)}</div>
          </section>
        </div>
        """
        self.send_html(layout("时政测验 - 研申", body, "political_quiz"))

    def page_political_quiz_pack(self, path, query=None):
        match = re.fullmatch(r"/political-quiz/packs/(\d+)", path)
        if not match:
            self.send_error(404)
            return
        pack_id = int(match.group(1))
        query = query or {}
        mode = query.get("mode", ["practice"])[0]
        if mode not in {"practice", "exam"}:
            mode = "practice"
        requested_attempt = query.get("attempt", [""])[0]
        with connect(self.db_path) as conn:
            row = get_quiz_pack(conn, pack_id)
            if row is None or row["status"] != "ready":
                self.send_error(404)
                return
            data = _row_pack(row)
            attempt = None
            if requested_attempt.isdigit():
                candidate = get_quiz_attempt(conn, int(requested_attempt))
                if candidate is not None and int(candidate["pack_id"]) == pack_id:
                    attempt = candidate
                    mode = candidate["mode"]
            if attempt is None:
                attempt_id = create_quiz_attempt(conn, pack_id, mode)
                attempt = get_quiz_attempt(conn, attempt_id)

        title = str((data.get("meta") or {}).get("title") or row["title"] or "时政测验")
        answers = attempt["answers_json"] or "{}"
        completed = bool(attempt["completed_at"])
        analysis = None
        if completed:
            try:
                saved_answers = json.loads(answers)
            except (TypeError, ValueError, json.JSONDecodeError):
                saved_answers = {}
            analysis = build_attempt_analysis(data, saved_answers)
        if completed:
            mode_label = "REVIEW MODE"
            mode_note = "复盘模式：本次答案已锁定，直接查看逐条解析、官方来源和成绩结构。"
            score_text = f"{int(attempt['correct_count'] or 0)} 正确"
        elif mode == "exam":
            mode_label = "EXAM MODE"
            mode_note = "考试模式：交卷前不显示答案，可修改选择。"
            score_text = "考试中"
        else:
            mode_label = "PRACTICE MODE"
            mode_note = "练习模式：每题选择后锁定，并立即显示逐条解析。"
            score_text = "0 正确"
        body = f"""
        <div class="political-quiz-session" data-political-quiz-page="pack" data-quiz-mode="{esc(mode)}" data-attempt-id="{attempt['id']}" data-attempt-completed="{'true' if completed else 'false'}" data-pack-id="{pack_id}" data-saved-answers="{esc(answers)}">
          {_pack_json_script(data)}
          {_analysis_json_script(analysis)}
          <header class="political-session-head">
            <div><a href="/political-quiz" class="political-back">← 返回时政测验</a><p class="eyebrow">{esc(mode_label)}</p><h1>{esc(title)}</h1><p>{esc(mode_note)}</p></div>
            <div class="political-session-badges">{_pack_badge(row)}<span>{row['question_count']} 题</span><span>{row['source_count']} 个来源</span></div>
          </header>
          {_verification_callout(row, data, attempt['id'], mode)}
          <div class="political-progress" data-quiz-progress><span data-quiz-progress-text>0 / {row['question_count']}</span><i><b data-quiz-progress-fill></b></i><strong data-quiz-score-now>{esc(score_text)}</strong></div>
          <section class="political-quiz-stage">
            <div class="political-quiz-list" data-quiz-root></div>
            <aside class="political-session-side">
              <section class="political-answer-sheet"><p class="eyebrow">ANSWER SHEET</p><h2>答题卡</h2><p>点击题号快速跳转；已答题会自动标记，交卷后显示对错。</p><div class="political-answer-grid" data-quiz-nav></div></section>
              <section><p class="eyebrow">REVIEW RULE</p><h2>逐条解析</h2><p>每个判断项单独说明对错，错项圈出真正改变政治含义的关键词。</p></section>
              <section><p class="eyebrow">SOURCE RULE</p><h2>来源核验</h2><p>每题保留官方来源、日期和链接；网友回忆版不作为政治表述依据。</p></section>
            </aside>
          </section>
          <section class="political-result" data-quiz-result hidden></section>
        </div>
        """
        self.send_html(layout(f"{title} - 时政测验", body, "political_quiz"))

    def handle_political_quiz_attempt(self, path):
        match = re.fullmatch(r"/political-quiz/attempts/(\d+)", path)
        if not match:
            self.send_json({"ok": False, "error": "作答记录不存在"}, status=404)
            return
        payload = self._political_json_body()
        if payload is None:
            return
        answers = payload.get("answers") or {}
        if not isinstance(answers, dict):
            self.send_json({"ok": False, "error": "answers 必须是对象"}, status=400)
            return
        attempt_id = int(match.group(1))
        with connect(self.db_path) as conn:
            attempt = get_quiz_attempt(conn, attempt_id)
            if attempt is None:
                self.send_json({"ok": False, "error": "作答记录不存在"}, status=404)
                return
            if attempt["completed_at"]:
                self.send_json({"ok": False, "error": "该测验已交卷，复盘记录不可修改"}, status=409)
                return
            update_quiz_attempt(
                conn,
                attempt_id,
                answers,
                elapsed_seconds=payload.get("elapsed_seconds") or 0,
            )
        self.send_json({"ok": True})

    def handle_political_quiz_submit(self, path):
        match = re.fullmatch(r"/political-quiz/attempts/(\d+)/submit", path)
        if not match:
            self.send_json({"ok": False, "error": "作答记录不存在"}, status=404)
            return
        payload = self._political_json_body()
        if payload is None:
            return
        answers = payload.get("answers") or {}
        if not isinstance(answers, dict):
            self.send_json({"ok": False, "error": "answers 必须是对象"}, status=400)
            return
        attempt_id = int(match.group(1))
        with connect(self.db_path) as conn:
            attempt = get_quiz_attempt(conn, attempt_id)
            if attempt is None:
                self.send_json({"ok": False, "error": "作答记录不存在"}, status=404)
                return
            row = get_quiz_pack(conn, attempt["pack_id"])
            if row is None:
                self.send_json({"ok": False, "error": "题包不存在"}, status=404)
                return
            pack = _row_pack(row)
            if attempt["completed_at"]:
                try:
                    saved_answers = json.loads(attempt["answers_json"] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    saved_answers = {}
                self.send_json({"ok": True, "analysis": build_attempt_analysis(pack, saved_answers), "replay": True})
                return
            analysis = complete_quiz_attempt(
                conn,
                attempt_id,
                pack,
                answers,
                elapsed_seconds=payload.get("elapsed_seconds") or 0,
            )
        self.send_json({"ok": True, "analysis": analysis})

    def handle_political_quiz_verify(self, path):
        match = re.fullmatch(r"/political-quiz/packs/(\d+)/verify", path)
        if not match:
            self.send_error(404)
            return
        pack_id = int(match.group(1))
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8") if length else ""
        form = parse_qs(data, keep_blank_values=True)
        mode = form.get("mode", ["practice"])[0]
        if mode not in {"practice", "exam"}:
            mode = "practice"
        requested_attempt = form.get("attempt", [""])[0]
        with connect(self.db_path) as conn:
            row = get_quiz_pack(conn, pack_id)
            if row is None or row["status"] != "ready":
                self.send_error(404)
                return
            mark_quiz_pack_verified(conn, pack_id)
            attempt = get_quiz_attempt(conn, int(requested_attempt)) if requested_attempt.isdigit() else None
        target = f"/political-quiz/packs/{pack_id}?mode={mode}"
        if attempt is not None and int(attempt["pack_id"]) == pack_id:
            target += f"&attempt={attempt['id']}"
        self.redirect(target)

    def handle_political_quiz_generate(self):
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8") if length else ""
        form = parse_qs(data, keep_blank_values=True)
        exam_scope = form.get("exam_scope", ["national"])[0]
        if exam_scope not in {"national", "province"}:
            exam_scope = "national"
        province = form.get("province", [""])[0].strip()
        period = form.get("period", ["month"])[0]
        if period not in {"month", "30d", "review", "custom"}:
            period = "month"
        period_start = form.get("period_start", [""])[0].strip()
        period_end = form.get("period_end", [""])[0].strip()
        if period == "custom" and not (period_start or period_end):
            self.send_json({"ok": False, "error": "请选择起始月份和结束月份"}, status=400)
            return
        try:
            question_count = int(form.get("question_count", ["20"])[0])
        except ValueError:
            question_count = 20
        question_count = question_count if question_count in {10, 20, 30} else 20
        mode = form.get("mode", ["practice"])[0]
        if mode not in {"practice", "exam"}:
            mode = "practice"

        with connect(self.db_path) as conn:
            settings = load_grading_settings(conn)
        if settings.get("mode") != "chatgpt":
            self.send_json(
                {
                    "ok": False,
                    "error": "生成最新测验需要在设置中连接 ChatGPT/Codex 账号，以启用联网检索；API 模式不会伪装成最新时政。",
                },
                status=400,
            )
            return
        options = {
            "exam_scope": exam_scope,
            "province": province,
            "period": period,
            "period_start": period_start,
            "period_end": period_end,
            "question_count": question_count,
            "mode": mode,
        }
        resume_job_id = form.get("resume_job_id", [""])[0].strip()
        job = None
        resume_state = None
        resumed = False
        if re.fullmatch(r"[0-9a-f]+", resume_job_id):
            job, resume_state = _resume_generation_job(resume_job_id, options)
            resumed = job is not None
        if job is None:
            job = _create_generation_job(options)
        thread = threading.Thread(
            target=_run_generation_job,
            args=(self.db_path, dict(settings), job["job_id"], options, resume_state),
            name=f"political-quiz-{job['job_id'][:8]}",
            daemon=True,
        )
        thread.start()
        self.send_json(
            {
                **job,
                "resumed": resumed,
                "status_url": f"/political-quiz/generation-jobs/{job['job_id']}/status",
            },
            status=202,
        )

    def handle_political_quiz_generation_status(self, path):
        match = re.fullmatch(r"/political-quiz/generation-jobs/([0-9a-f]+)/status", path)
        if not match:
            self.send_error(404)
            return
        job = _get_generation_job(match.group(1))
        if job is None:
            self.send_json({"ok": False, "error": "生成任务不存在或已过期"}, status=404)
            return
        self.send_json(job)

    def _political_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "请求 JSON 无法解析"}, status=400)
            return None
        if not isinstance(payload, dict):
            self.send_json({"ok": False, "error": "请求体必须是 JSON 对象"}, status=400)
            return None
        return payload
