"""Personal standardized-expression library controllers."""

from urllib.parse import urlencode

from ...normative_expressions import (
    CATEGORY_RULES,
    add_custom_normative_expression,
    export_normative_expressions_markdown,
    list_normative_expressions,
    normative_expression_summary,
    sync_normative_expression_history,
    top_normative_verbs,
    update_normative_expression,
)
from ..runtime import connect, esc, form_value, layout

SOURCE_LABELS = {
    "rubric": "评分基准",
    "revision": "答案修订",
    "imported": "导入报告",
    "manual": "手工整理",
}


def _selected(query, key):
    value = (query or {}).get(key, [""])
    return value[0] if value else ""


def _option(value, label, selected):
    marker = " selected" if value == selected else ""
    return f'<option value="{esc(value)}"{marker}>{esc(label)}</option>'


def _query_url(filters, **updates):
    values = {key: value for key, value in filters.items() if value}
    for key, value in updates.items():
        if value in {None, ""}:
            values.pop(key, None)
        else:
            values[key] = value
    return "/normative-expressions" + (f"?{urlencode(values)}" if values else "")


def _expression_card(item, selected_id, filters):
    active = " is-selected" if item["id"] == selected_id else ""
    starred = " ★" if item.get("is_starred") else ""
    status = "已掌握" if item.get("status") == "mastered" else "学习中"
    types = " · ".join(item.get("question_types") or []) or "通用"
    wrong = (item.get("wrong_expressions") or [""])[0]
    source_note = f"{item.get('question_count', 0)} 题 · {item.get('source_count', 0)} 份报告"
    coverage_bits = []
    if item.get("miss_count"):
        coverage_bits.append(f"遗漏 {item['miss_count']}")
    if item.get("partial_count"):
        coverage_bits.append(f"部分 {item['partial_count']}")
    if item.get("hit_count"):
        coverage_bits.append(f"命中 {item['hit_count']}")
    coverage_note = " · ".join(coverage_bits)
    detail_url = _query_url(filters, selected=str(item["id"]))
    return f"""
    <a class="norm-card{active}" href="{esc(detail_url)}">
      <span class="norm-card-priority" aria-hidden="true"></span>
      <span class="norm-card-main">
        <span class="norm-card-top"><span class="norm-chip">{esc(item['category'])}</span><small>{esc(status)}{starred}</small></span>
        <strong>{esc(item['expression'])}</strong>
        <small>{esc(types)} · {esc(source_note)}</small>
        {f'<span class="norm-card-coverage">{esc(coverage_note)}</span>' if coverage_note else ''}
        {f'<span class="norm-card-wrong">原表述：{esc(wrong)}</span>' if wrong else ''}
      </span>
    </a>
    """


def _detail_evidence(item):
    evidence = item.get("evidence") or []
    if not evidence:
        return '<p class="norm-empty-inline">这是手工补录词条，暂时没有自动来源。</p>'
    rows = []
    seen = set()
    for row in evidence[:14]:
        key = (row.get("report_id"), row.get("source_kind"), row.get("trigger_signal"), row.get("wrong_expression"))
        if key in seen:
            continue
        seen.add(key)
        code = row.get("question_code") or "题目"
        qid = row.get("question_id")
        title = row.get("title") or row.get("source_label") or ""
        source = SOURCE_LABELS.get(row.get("source_kind"), row.get("source_kind") or "来源")
        status = row.get("match_status") or ""
        status_label = {"miss": "本题遗漏", "partial": "部分命中", "hit": "已命中"}.get(status, status)
        trigger = row.get("trigger_signal") or "未记录材料触发句"
        wrong = row.get("wrong_expression") or ""
        rows.append(
            f"""
            <article class="norm-source-row">
              <div class="norm-source-head">
                <span>{f'<a href="/questions/{qid}">{esc(code)}</a>' if qid else esc(code)} · {esc(source)}</span>
                {f'<em>{esc(status_label)}</em>' if status_label else ''}
              </div>
              {f'<strong>{esc(title)}</strong>' if title else ''}
              <p><span>材料触发</span>{esc(trigger)}</p>
              {f'<p class="norm-source-wrong"><span>原表述</span><del>{esc(wrong)}</del></p>' if wrong else ''}
            </article>
            """
        )
    return "".join(rows)


def _detail_panel(item, filters):
    if not item:
        return """
        <div class="norm-detail-empty">
          <span>词</span><h2>还没有可展示的规范表达</h2>
          <p>完成批改后会自动从评分基准、遗漏建议和答案修订中整理；也可以先手工补一条。</p>
        </div>
        """
    triggers = item.get("trigger_signals") or []
    contexts = item.get("contexts") or []
    wrongs = item.get("wrong_expressions") or []
    source_kinds = [SOURCE_LABELS.get(value, value) for value in item.get("source_kinds") or []]
    return_to = _query_url(filters, selected=str(item["id"]))
    mastered = item.get("status") == "mastered"
    return f"""
    <article class="norm-detail">
      <header class="norm-detail-head">
        <div>
          <div class="norm-detail-tags"><span>{esc(item['category'])}</span>{''.join(f'<span>{esc(value)}</span>' for value in item.get('question_types') or [])}</div>
          <h2>{esc(item['expression'])}</h2>
          <p>来自 {item.get('question_count', 0)} 道题、{item.get('source_count', 0)} 份批改报告 · {esc(' / '.join(source_kinds) or '手工整理')}</p>
        </div>
        <div class="norm-detail-actions">
          <form method="post" action="/normative-expressions/update">
            <input type="hidden" name="entry_id" value="{item['id']}"><input type="hidden" name="action" value="star"><input type="hidden" name="return_to" value="{esc(return_to)}">
            <button class="norm-icon-button{' is-starred' if item.get('is_starred') else ''}" type="submit" title="收藏">★</button>
          </form>
          <form method="post" action="/normative-expressions/update">
            <input type="hidden" name="entry_id" value="{item['id']}"><input type="hidden" name="action" value="status"><input type="hidden" name="value" value="{'learning' if mastered else 'mastered'}"><input type="hidden" name="return_to" value="{esc(return_to)}">
            <button class="button ghost" type="submit">{'继续学习' if mastered else '标记已掌握'}</button>
          </form>
        </div>
      </header>

      <section class="norm-detail-grid">
        <div><span>材料触发信号</span>{''.join(f'<p>{esc(value)}</p>' for value in triggers[:4]) or '<p class="muted">暂无</p>'}</div>
        <div><span>适用情境</span>{''.join(f'<p>{esc(value)}</p>' for value in contexts[:4]) or '<p class="muted">暂无</p>'}</div>
      </section>

      <section class="norm-wrong-section">
        <div class="norm-section-heading"><div><p class="eyebrow">BEFORE → AFTER</p><h3>不规范 → 规范</h3></div><span>{len(wrongs)} 组</span></div>
        {''.join(f'<div class="norm-wrong-row"><del>{esc(value)}</del><span>→</span><ins>{esc(item["expression"])}</ins></div>' for value in wrongs[:8]) or '<p class="norm-empty-inline">当前词条来自评分基准，尚没有你的原答案修订对照。</p>'}
      </section>

      <section class="norm-evidence-section">
        <div class="norm-section-heading"><div><p class="eyebrow">SOURCE TRACE</p><h3>来源追溯</h3></div><span>最多展示 14 条</span></div>
        {_detail_evidence(item)}
      </section>

      <section class="norm-note-section">
        <div class="norm-section-heading"><div><p class="eyebrow">PERSONAL NOTE</p><h3>我的备注</h3></div></div>
        <form method="post" action="/normative-expressions/update">
          <input type="hidden" name="entry_id" value="{item['id']}"><input type="hidden" name="action" value="note"><input type="hidden" name="return_to" value="{esc(return_to)}">
          <textarea name="value" rows="3" placeholder="例如：主体层级容易写错；看到“各管一段”优先想到联动机制。">{esc(item.get('note') or '')}</textarea>
          <button class="button ghost" type="submit">保存备注</button>
        </form>
      </section>
    </article>
    """


class NormativeExpressionsController:
    def page_normative_expressions(self, query=None):
        query = query or {}
        filters = {
            "q": _selected(query, "q"),
            "category": _selected(query, "category"),
            "question_type": _selected(query, "question_type"),
            "status": _selected(query, "status"),
            "source_kind": _selected(query, "source_kind"),
            "coverage": _selected(query, "coverage"),
            "selected": _selected(query, "selected"),
        }
        with connect(self.db_path) as conn:
            sync_normative_expression_history(conn)
            summary = normative_expression_summary(conn)
            entries = list_normative_expressions(conn, filters)

        selected_id = 0
        try:
            selected_id = int(filters["selected"] or 0)
        except ValueError:
            selected_id = 0
        selected_item = next((item for item in entries if item["id"] == selected_id), entries[0] if entries else None)
        if selected_item:
            selected_id = selected_item["id"]
        verbs = top_normative_verbs(entries, 18)
        categories = [item["category"] for item in summary["categories"]]
        for category, _keywords in CATEGORY_RULES:
            if category not in categories:
                categories.append(category)
        if "其他规范表达" not in categories:
            categories.append("其他规范表达")

        category_options = _option("", "全部类别", filters["category"]) + "".join(
            _option(value, value, filters["category"]) for value in categories
        )
        type_options = _option("", "全部题型", filters["question_type"]) + "".join(
            _option(value, value, filters["question_type"]) for value in summary["question_types"]
        )
        status_options = "".join(
            _option(value, label, filters["status"])
            for value, label in (("", "全部掌握状态"), ("learning", "学习中"), ("mastered", "已掌握"), ("hidden", "已隐藏"))
        )
        source_options = "".join(
            _option(value, label, filters["source_kind"])
            for value, label in (("", "全部来源"), ("rubric", "评分基准"), ("revision", "答案修订"), ("imported", "导入报告"))
        )
        coverage_options = "".join(
            _option(value, label, filters["coverage"])
            for value, label in (
                ("", "全部命中状态"),
                ("needs_work", "待补漏（遗漏/部分）"),
                ("miss", "本题遗漏"),
                ("partial", "部分命中"),
                ("hit", "已验证命中"),
            )
        )
        cards = "".join(_expression_card(item, selected_id, filters) for item in entries)
        if not cards:
            cards = '<div class="norm-list-empty"><strong>当前筛选下没有词条</strong><span>调整筛选，或重整一次历史数据。</span></div>'
        verb_html = "".join(
            f'<span><strong>{esc(verb)}</strong><small>{count}</small></span>' for verb, count in verbs
        ) or '<span><strong>暂无</strong><small>0</small></span>'

        body = f"""
        <div class="norm-page">
          <section class="norm-page-head">
            <div><p class="eyebrow">STANDARD PHRASE LIBRARY</p><h1>规范词整理</h1><p>自动从历史批改和每次新答题中持续提取、归类规范表达，保留材料触发信号、适用情境和你的原表述修订。</p></div>
            <div class="norm-head-actions">
              <span class="norm-auto-status" title="应用启动、新批改完成以及打开本页时都会自动同步">自动整理已开启</span>
              <a class="button ghost" href="/normative-expressions/export.md" data-native-download>导出 Markdown</a>
              <form method="post" action="/normative-expressions/sync"><button class="button ghost" type="submit">重新扫描</button></form>
            </div>
          </section>

          <section class="norm-metrics" aria-label="规范表达概览">
            <article><span>规范表达</span><strong>{summary['total']}</strong><small>持续随批改增长</small></article>
            <article><span>来源题目</span><strong>{summary['questions']}</strong><small>{summary['reports']} 份批改报告</small></article>
            <article><span>已掌握</span><strong>{summary['mastered']}</strong><small>收藏 {summary['starred']} 条</small></article>
            <article><span>表达依据</span><strong>{summary['evidence']}</strong><small>评分基准 + 修订痕</small></article>
          </section>

          <section class="norm-verb-strip"><div><p class="eyebrow">ACTION VERBS</p><h2>高频动词</h2></div><div class="norm-verb-cloud">{verb_html}</div></section>

          <form class="norm-filter-bar" method="get" action="/normative-expressions">
            <label class="norm-search"><span>⌕</span><input type="search" name="q" value="{esc(filters['q'])}" placeholder="搜索规范词、材料触发句、原表述或题号"></label>
            <select name="question_type">{type_options}</select>
            <select name="category">{category_options}</select>
            <select name="source_kind">{source_options}</select>
            <select name="coverage">{coverage_options}</select>
            <select name="status">{status_options}</select>
            <button class="button ghost" type="submit">筛选</button>
            <a class="button ghost" href="/normative-expressions">重置</a>
          </form>

          <section class="norm-workspace">
            <div class="norm-list-pane">
              <div class="norm-list-heading"><strong>表达库</strong><span>当前 {len(entries)} 条 · 遗漏频率高的优先</span></div>
              <div class="norm-list">{cards}</div>
            </div>
            <div class="norm-detail-pane">{_detail_panel(selected_item, filters)}</div>
          </section>

          <details class="norm-custom-add">
            <summary><span>＋ 手工补充规范表达</span><small>自动整理之外，也可把课堂或复盘里遇到的词加入个人库</small></summary>
            <form method="post" action="/normative-expressions/add">
              <label><span>规范表述</span><input name="expression" required placeholder="例如：建立部门联动机制"></label>
              <label><span>类别</span><select name="category">{''.join(_option(value, value, '') for value in categories)}</select></label>
              <label class="wide"><span>备注</span><input name="note" placeholder="可写触发条件、易错点或适用边界"></label>
              <button class="button primary" type="submit">加入词库</button>
            </form>
          </details>
        </div>
        """
        self.send_html(layout("规范词整理 - 研申", body, "normative_expressions"))

    def export_normative_expressions(self):
        with connect(self.db_path) as conn:
            sync_normative_expression_history(conn)
            content = export_normative_expressions_markdown(conn)
        self.send_text(content, "申论规范表达-个人整理库.md", "text/markdown; charset=utf-8")

    def handle_normative_expression_sync(self):
        with connect(self.db_path) as conn:
            sync_normative_expression_history(conn, force=True)
        self.redirect("/normative-expressions")

    def handle_normative_expression_add(self):
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8")
        expression = form_value(data, "expression")
        category = form_value(data, "category")
        note = form_value(data, "note")
        with connect(self.db_path) as conn:
            entry_id = add_custom_normative_expression(conn, expression, category, note)
        target = f"/normative-expressions?selected={entry_id}" if entry_id else "/normative-expressions"
        self.redirect(target)

    def handle_normative_expression_update(self):
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8")
        try:
            entry_id = int(form_value(data, "entry_id") or 0)
        except ValueError:
            entry_id = 0
        action = form_value(data, "action")
        value = form_value(data, "value")
        return_to = form_value(data, "return_to") or f"/normative-expressions?selected={entry_id}"
        if not return_to.startswith("/normative-expressions"):
            return_to = "/normative-expressions"
        with connect(self.db_path) as conn:
            update_normative_expression(conn, entry_id, action=action, value=value)
        self.redirect(return_to)
