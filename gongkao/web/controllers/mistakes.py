"""Mistake notebook controller.

The notebook is derived from structured grading reports.  Only review state,
starred state, and user notes are persisted; grading evidence remains the
single source of truth for issue content.
"""

from urllib.parse import urlencode

from ...mistakes import load_mistake_items, mistake_summary
from ..runtime import (
    connect,
    esc,
    format_beijing_time,
    layout,
    math,
    pagination_html,
    parse_qs,
    requested_page,
)

SEVERITY_LABELS = {
    "high": "高影响",
    "medium": "重点",
    "low": "一般",
}

STATE_LABELS = {
    "active": "待复盘",
    "reviewing": "复盘中",
    "mastered": "已掌握",
}


def _filter_option(value, current, label):
    selected = " selected" if value == current else ""
    return f'<option value="{esc(value)}"{selected}>{esc(label)}</option>'


def _tag_html(item):
    rows = []
    tags = set(item.get("tags") or [])
    if "omission" in tags:
        rows.append('<span class="mistake-tag omission">遗漏</span>')
    if "error" in tags:
        rows.append('<span class="mistake-tag error">答错</span>')
    if "severe" in tags:
        rows.append('<span class="mistake-tag severe">严重</span>')
    if item.get("recurrence_count", 1) > 1:
        rows.append(f'<span class="mistake-tag repeated">重复 {item["recurrence_count"]} 次</span>')
    return "".join(rows)


def _score_html(item):
    score = item.get("display_score")
    maximum = item.get("display_max_score")
    if score in (None, "") or maximum in (None, ""):
        return ""
    try:
        return f'<span>{float(score):g}/{float(maximum):g}分</span>'
    except (TypeError, ValueError):
        return ""


def _material_html(item):
    evidence = item.get("material_evidence") or []
    if not evidence:
        return '<p class="mistake-empty-note">该问题没有绑定可核验的材料短引文。</p>'
    rows = []
    for evidence_item in evidence:
        number = evidence_item.get("material_number")
        label = f"材料{number}" if number not in (None, "") else "材料依据"
        rows.append(
            f"""
            <blockquote class="mistake-material-quote">
              <span>{esc(label)}</span>
              <p>{esc(evidence_item.get("quote") or "")}</p>
            </blockquote>
            """
        )
    return "".join(rows)


def _answer_excerpts_html(item):
    excerpts = item.get("answer_excerpts") or []
    if not excerpts:
        return '<p class="mistake-empty-note">该采分点在原答案中没有可定位的对应表达。</p>'
    multiple = len(excerpts) > 1
    rows = []
    for index, excerpt in enumerate(excerpts, start=1):
        label = f'<span>原答案片段 {index}</span>' if multiple else ""
        rows.append(
            f'<blockquote class="mistake-answer-quote">{label}<p>{esc(excerpt)}</p></blockquote>'
        )
    return "".join(rows)


def _annotations_html(item):
    annotations = item.get("annotations") or []
    if not annotations:
        return ""
    rows = []
    for annotation in annotations:
        quote = annotation.get("quote") or ""
        replacement = annotation.get("replacement") or ""
        reason = annotation.get("reason") or ""
        if not (quote or replacement or reason):
            continue
        change = ""
        if quote and replacement:
            change = (
                f'<div class="mistake-change"><del>{esc(quote)}</del>'
                f'<span aria-hidden="true">→</span><ins>{esc(replacement)}</ins></div>'
            )
        elif quote:
            change = f'<div class="mistake-change"><del>{esc(quote)}</del></div>'
        elif replacement:
            change = f'<div class="mistake-change"><ins>{esc(replacement)}</ins></div>'
        rows.append(
            f"""
            <div class="mistake-annotation-row">
              {change}
              {f'<p>{esc(reason)}</p>' if reason else ''}
            </div>
            """
        )
    if not rows:
        return ""
    return f'<section class="mistake-detail-section"><h3>答错位置与纠正</h3>{"".join(rows)}</section>'


def _detail_panel(item, hidden=False):
    issue_key = item["issue_key"]
    state = item.get("review_state") or "active"
    missing = item.get("missing_elements") or []
    missing_html = (
        '<div class="mistake-missing-chips">'
        + "".join(f'<span>{esc(value)}</span>' for value in missing)
        + "</div>"
        if missing
        else ""
    )
    omission_reason = item.get("omission_reason") or item.get("reason") or ""
    correction = item.get("correction") or item.get("minimum_expression") or item.get("canonical_expression") or ""
    revised = item.get("revised_answer") or ""
    answer_snapshot = item.get("answer_snapshot") or ""
    starred = bool(item.get("is_starred"))
    review_count = int(item.get("review_count") or 0)
    hidden_attr = " hidden" if hidden else ""
    mastered_label = "重新加入复盘" if state == "mastered" else "标记已掌握"
    mastered_action = "restore" if state == "mastered" else "mastered"
    state_label = STATE_LABELS.get(state, "待复盘")
    coverage = round(float(item.get("coverage_ratio") or 0) * 100)
    coverage_html = ""
    if item.get("status") in {"partial", "miss"}:
        coverage_html = f"""
        <div class="mistake-coverage" aria-label="采分点覆盖率 {coverage}%">
          <span>本次覆盖</span><div><i style="width:{coverage}%"></i></div><strong>{coverage}%</strong>
        </div>
        """

    return f"""
    <article class="mistake-detail" data-mistake-detail="{esc(issue_key)}"{hidden_attr}>
      <header class="mistake-detail-head">
        <div>
          <div class="mistake-detail-tags">{_tag_html(item)}</div>
          <h2>{esc(item.get("label") or "错题")}</h2>
          <p>{esc(item.get("year"))} · {esc(item.get("exam_type"))} · {esc(item.get("question_type"))} · {esc(item.get("question_title"))}</p>
        </div>
        <div class="mistake-detail-actions">
          <button type="button" class="mistake-icon-button{' is-starred' if starred else ''}" data-mistake-action="toggle_star" aria-label="重点收藏" title="重点收藏">★</button>
          <button type="button" class="button small" data-mistake-action="review">复盘 +1</button>
          <button type="button" class="button small primary" data-mistake-action="{mastered_action}">{mastered_label}</button>
        </div>
      </header>

      <div class="mistake-detail-meta">
        <span class="mistake-state-pill {esc(state)}" data-mistake-state>{esc(state_label)}</span>
        <span>{esc(SEVERITY_LABELS.get(item.get("severity"), "重点"))}</span>
        {_score_html(item)}
        <span>已复盘 <b data-mistake-review-count>{review_count}</b> 次</span>
        <span>{esc(format_beijing_time(item.get("created_at")))}</span>
      </div>

      <section class="mistake-detail-section mistake-material-section mistake-material-primary">
        <div class="mistake-section-heading"><h3>对应材料节选</h3><span>先看材料，再复盘作答</span></div>
        {_material_html(item)}
      </section>

      {coverage_html}

      <section class="mistake-detail-section mistake-answer-section">
        <div class="mistake-section-heading"><h3>我当时怎么写</h3><span>只展示可回查到原答案的连续片段</span></div>
        {_answer_excerpts_html(item)}
      </section>

      <section class="mistake-detail-section">
        <h3>为什么丢分</h3>
        <p class="mistake-analysis-text">{esc(omission_reason or "该点未完整覆盖评分基准。")}</p>
        {missing_html}
      </section>

      {_annotations_html(item)}

      <section class="mistake-detail-section mistake-correction-section">
        <div class="mistake-section-heading"><h3>这一点应该怎么写</h3><span>优先采用材料原词和最短完整表达</span></div>
        <p>{esc(correction or "请结合上方材料依据补足该采分点。")}</p>
      </section>

      <details class="mistake-revised-answer"{' open' if not item.get('material_evidence') else ''}>
        <summary><span>整题修改版答案</span><small>展开查看批改时生成的完整修改稿</small></summary>
        <div class="mistake-revised-body">
          <button type="button" class="button ghost small" data-copy-mistake-answer>复制修改版答案</button>
          <div class="preline" data-mistake-revised-text>{esc(revised or "暂无结构化修改版答案。")}</div>
        </div>
      </details>

      <details class="mistake-original-answer">
        <summary><span>本次完整原答案</span><small>需要时回看上下文</small></summary>
        <div class="preline">{esc(answer_snapshot or "暂无原答案快照。")}</div>
      </details>

      <section class="mistake-detail-section mistake-note-section">
        <div class="mistake-section-heading"><h3>复盘笔记</h3><span data-mistake-note-status>自动保存在本机</span></div>
        <textarea data-mistake-note rows="3" placeholder="例如：以后看到双任务题，先画问题—对策对应箭头……">{esc(item.get("note") or "")}</textarea>
        <div class="mistake-note-actions"><button class="button small" type="button" data-mistake-save-note>保存笔记</button></div>
      </section>

      <footer class="mistake-detail-footer">
        <a class="button ghost small" href="/questions/{item['question_id']}">再做一次</a>
        <a class="button ghost small" href="/attempts/{item['attempt_id']}">查看本次作答</a>
        <a class="button ghost small" href="/grading-reports/{item['report_id']}">查看完整批改</a>
      </footer>
    </article>
    """


class MistakesController:
    def page_mistakes(self, query=None):
        query = query or {}
        page = requested_page(query)
        page_size = 18
        filters = {
            "scope": query.get("scope", ["all"])[0],
            "state": query.get("state", ["open"])[0],
            "question_type": query.get("question_type", [""])[0],
            "q": query.get("q", [""])[0].strip(),
            "sort": query.get("sort", ["recent"])[0],
        }
        with connect(self.db_path) as conn:
            all_items = load_mistake_items(conn)
        summary = mistake_summary(all_items)
        question_types = sorted({item.get("question_type") for item in all_items if item.get("question_type")})

        def matches(item):
            scope = filters["scope"]
            tags = set(item.get("tags") or [])
            if scope in {"omission", "error", "severe"} and scope not in tags:
                return False
            if scope == "repeated" and item.get("recurrence_count", 1) <= 1:
                return False
            if scope == "starred" and not item.get("is_starred"):
                return False
            state = filters["state"]
            if state == "open" and item.get("review_state") == "mastered":
                return False
            if state in {"active", "reviewing", "mastered"} and item.get("review_state") != state:
                return False
            if filters["question_type"] and item.get("question_type") != filters["question_type"]:
                return False
            keyword = filters["q"].lower()
            if keyword:
                haystack = " ".join(
                    [
                        str(item.get("label") or ""),
                        str(item.get("question_title") or ""),
                        str(item.get("paper_name") or ""),
                        str(item.get("answer_quote") or ""),
                        str(item.get("reason") or ""),
                        " ".join(str(value.get("quote") or "") for value in item.get("material_evidence") or []),
                    ]
                ).lower()
                if keyword not in haystack:
                    return False
            return True

        items = [item for item in all_items if matches(item)]
        severity_rank = {"high": 3, "medium": 2, "low": 1}
        if filters["sort"] == "severity":
            items.sort(key=lambda item: (severity_rank.get(item.get("severity"), 1), item.get("recurrence_count", 1), item.get("created_at") or ""), reverse=True)
        elif filters["sort"] == "repeated":
            items.sort(key=lambda item: (item.get("recurrence_count", 1), severity_rank.get(item.get("severity"), 1), item.get("created_at") or ""), reverse=True)
        else:
            items.sort(key=lambda item: item.get("created_at") or "", reverse=True)

        total_items = len(items)
        total_pages = max(1, math.ceil(total_items / page_size))
        page = min(page, total_pages)
        start = (page - 1) * page_size
        page_items = items[start : start + page_size]
        selected_key = query.get("selected", [""])[0]
        selected_index = next((index for index, item in enumerate(page_items) if item["issue_key"] == selected_key), 0)

        cards = []
        details = []
        for index, item in enumerate(page_items):
            selected = index == selected_index
            state = item.get("review_state") or "active"
            quote = item.get("answer_quote") or item.get("reason") or "未写出对应内容"
            material_preview = (item.get("material_evidence") or [{}])[0].get("quote") or ""
            cards.append(
                f"""
                <button type="button" class="mistake-card{' is-selected' if selected else ''}{' is-mastered' if state == 'mastered' else ''}" data-mistake-select="{esc(item['issue_key'])}">
                  <span class="mistake-card-impact {esc(item.get('severity') or 'medium')}"></span>
                  <span class="mistake-card-main">
                    <span class="mistake-card-top"><span>{_tag_html(item)}</span><time>{esc(format_beijing_time(item.get('created_at')))}</time></span>
                    <strong>{esc(item.get('label') or '错题')}</strong>
                    <small>{esc(item.get('year'))} · {esc(item.get('question_type'))} · {esc(item.get('question_title'))}</small>
                    <span class="mistake-card-quote">“{esc(quote)}”</span>
                    {f'<span class="mistake-card-material">材料：{esc(material_preview)}</span>' if material_preview else ''}
                    <span class="mistake-card-foot"><span>{esc(STATE_LABELS.get(state, '待复盘'))}</span>{_score_html(item)}</span>
                  </span>
                </button>
                """
            )
            details.append(_detail_panel(item, hidden=not selected))

        if not cards:
            list_html = """
            <div class="mistake-empty">
              <span>✓</span>
              <h2>当前筛选下没有错题</h2>
              <p>错题本只收录有结构化材料依据的遗漏、答错和高影响扣分点。</p>
            </div>
            """
            detail_html = ""
        else:
            list_html = "".join(cards)
            detail_html = "".join(details)

        scope_tabs = [
            ("all", "全部"),
            ("omission", "遗漏点"),
            ("error", "答错点"),
            ("severe", "严重扣分"),
            ("repeated", "重复出现"),
            ("starred", "重点收藏"),
        ]
        tab_links = []
        for value, label in scope_tabs:
            active = " active" if filters["scope"] == value else ""
            tab_query = urlencode(
                {
                    key: (value if key == "scope" else current)
                    for key, current in filters.items()
                    if current not in ("", None) or key == "scope"
                }
            )
            tab_links.append(
                f'<a class="mistake-scope-tab{active}" href="/mistakes?{esc(tab_query)}">{esc(label)}</a>'
            )

        type_options = '<option value="">全部题型</option>' + "".join(
            _filter_option(value, filters["question_type"], value) for value in question_types
        )
        state_options = "".join(
            [
                _filter_option("open", filters["state"], "未掌握"),
                _filter_option("active", filters["state"], "待复盘"),
                _filter_option("reviewing", filters["state"], "复盘中"),
                _filter_option("mastered", filters["state"], "已掌握"),
                _filter_option("all", filters["state"], "全部状态"),
            ]
        )
        sort_options = "".join(
            [
                _filter_option("recent", filters["sort"], "最近发现"),
                _filter_option("severity", filters["sort"], "影响优先"),
                _filter_option("repeated", filters["sort"], "重复优先"),
            ]
        )
        pager_filters = {**filters}
        pager = pagination_html("/mistakes", pager_filters, page, total_items, page_size)

        body = f"""
        <div class="mistake-book" data-mistake-book>
          <section class="mistake-page-head">
            <div>
              <p class="eyebrow">Mistake Notebook</p>
              <h1>错题本</h1>
              <p>从每次批改中自动收录遗漏、答错和高影响扣分点，只保留与该点直接相关的材料节选。</p>
            </div>
            <div class="mistake-head-note"><span>自动整理</span><strong>{summary['total']}</strong><small>条历史错点</small></div>
          </section>

          <section class="mistake-metrics" aria-label="错题概览">
            <article><span>待处理</span><strong>{summary['active']}</strong><small>尚未掌握</small></article>
            <article class="danger"><span>严重扣分</span><strong>{summary['severe']}</strong><small>优先复盘</small></article>
            <article><span>重复出现</span><strong>{summary['repeated']}</strong><small>同题同点再次发生</small></article>
            <article class="success"><span>已掌握</span><strong>{summary['mastered']}</strong><small>可随时重新加入</small></article>
          </section>

          <nav class="mistake-scope-tabs" aria-label="错题类型">{"".join(tab_links)}</nav>

          <form class="mistake-filter-bar" method="get" action="/mistakes">
            <input type="hidden" name="scope" value="{esc(filters['scope'])}">
            <label class="mistake-search"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-4-4"></path></svg><input type="search" name="q" value="{esc(filters['q'])}" placeholder="搜索题目、错点、材料原句"></label>
            <select name="question_type" aria-label="题型">{type_options}</select>
            <select name="state" aria-label="掌握状态">{state_options}</select>
            <select name="sort" aria-label="排序">{sort_options}</select>
            <button class="button small" type="submit">筛选</button>
            <a class="button ghost small" href="/mistakes">重置</a>
          </form>

          <section class="mistake-workspace">
            <aside class="mistake-list-pane">
              <div class="mistake-list-heading"><strong>{total_items} 个错点</strong><span>点击左侧错点查看材料与修改</span></div>
              <div class="mistake-list" data-mistake-list>{list_html}</div>
              {pager}
            </aside>
            <main class="mistake-detail-pane" data-mistake-detail-pane>{detail_html}</main>
          </section>
        </div>
        """
        self.send_html(layout("错题本 - 研申", body, "mistakes"))

    def handle_mistake_update(self):
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8") if length else ""
        form = parse_qs(data, keep_blank_values=True)
        issue_key = form.get("issue_key", [""])[0].strip()
        action = form.get("action", [""])[0].strip()
        note = form.get("note", [""])[0]
        if not issue_key or not action:
            self.send_json({"ok": False, "error": "缺少错题操作参数"}, status=400)
            return

        with connect(self.db_path) as conn:
            item = next((value for value in load_mistake_items(conn) if value["issue_key"] == issue_key), None)
            if not item:
                self.send_json({"ok": False, "error": "错题条目不存在或已失效"}, status=404)
                return
            conn.execute(
                """
                INSERT INTO mistake_reviews (
                    issue_key, report_id, attempt_id, question_id
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(issue_key) DO UPDATE SET
                    report_id = excluded.report_id,
                    attempt_id = excluded.attempt_id,
                    question_id = excluded.question_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (issue_key, item["report_id"], item["attempt_id"], item["question_id"]),
            )
            if action == "toggle_star":
                conn.execute(
                    """
                    UPDATE mistake_reviews
                       SET is_starred = CASE is_starred WHEN 1 THEN 0 ELSE 1 END,
                           updated_at = CURRENT_TIMESTAMP
                     WHERE issue_key = ?
                    """,
                    (issue_key,),
                )
            elif action == "review":
                conn.execute(
                    """
                    UPDATE mistake_reviews
                       SET state = CASE WHEN state = 'mastered' THEN state ELSE 'reviewing' END,
                           review_count = review_count + 1,
                           last_reviewed_at = CURRENT_TIMESTAMP,
                           updated_at = CURRENT_TIMESTAMP
                     WHERE issue_key = ?
                    """,
                    (issue_key,),
                )
            elif action in {"active", "reviewing", "mastered"}:
                conn.execute(
                    "UPDATE mistake_reviews SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE issue_key = ?",
                    (action, issue_key),
                )
            elif action == "restore":
                conn.execute(
                    "UPDATE mistake_reviews SET state = 'active', updated_at = CURRENT_TIMESTAMP WHERE issue_key = ?",
                    (issue_key,),
                )
            elif action == "note":
                conn.execute(
                    "UPDATE mistake_reviews SET note = ?, updated_at = CURRENT_TIMESTAMP WHERE issue_key = ?",
                    (note[:4000], issue_key),
                )
            else:
                self.send_json({"ok": False, "error": "未知错题操作"}, status=400)
                return
            state = conn.execute(
                "SELECT state, is_starred, note, review_count, last_reviewed_at FROM mistake_reviews WHERE issue_key = ?",
                (issue_key,),
            ).fetchone()
        self.send_json(
            {
                "ok": True,
                "issue_key": issue_key,
                "state": state["state"],
                "state_label": STATE_LABELS.get(state["state"], "待复盘"),
                "is_starred": bool(state["is_starred"]),
                "note": state["note"],
                "review_count": state["review_count"],
                "last_reviewed_at": state["last_reviewed_at"],
            }
        )
