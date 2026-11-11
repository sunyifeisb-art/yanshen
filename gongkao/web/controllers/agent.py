"""AI coach workspace controllers."""

import time

from ...ai_config import load_effective_agent_settings
from ..runtime import (
    MODULES,
    AgentRunError,
    AiConfigError,
    AiRequestError,
    _cleanup_orphaned_pending_messages,
    _int_or_none,
    active_memories,
    add_feedback,
    agent_response_html,
    agent_summary_for_display,
    back_link,
    chat_completion,
    clear_memories,
    connect,
    conversation_payload,
    delete_conversation,
    delete_memory,
    esc,
    first_screen_judgement,
    form_value,
    format_beijing_time,
    get_feedback,
    get_run,
    get_run_steps,
    json,
    latest_eval_results,
    layout,
    load_knowledge_items,
    logging,
    module_definition,
    option_list,
    parse_qs,
    re,
    recent_conversations,
    recent_runs,
    render_agent_message_row,
    render_agent_pending_status,
    render_agent_rag_panel,
    render_agent_thread_list,
    resolve_api_key,
    return_path_from_form,
    return_path_from_query,
    run_agent,
    run_eval_suite,
    select_options,
    sort_question_types,
    sort_regions,
    start_or_continue_chat_async,
    valid_module_id,
)


class AgentController:
    def page_agent(self, query, flashes=None):
        mode = query.get("mode", [""])[0].strip()
        attempt_id = query.get("attempt_id", [""])[0].strip()
        selected_module = valid_module_id(query.get("module", ["overview"])[0].strip())
        visible_modules = ("overview", "summary", "analysis", "countermeasure", "document", "essay")
        if selected_module not in visible_modules:
            selected_module = "overview"
        selected_definition = module_definition(selected_module)
        selected_scope_label = "全部题型" if selected_module == "overview" else selected_definition["label"]
        with connect(self.db_path) as conn:
            settings = load_effective_agent_settings(conn)
            judgement = first_screen_judgement(conn)
            conversations = recent_conversations(conn, 50)
            runs = recent_runs(conn, 4)
            question_types = sort_question_types(
                [row["question_type"] for row in conn.execute("SELECT DISTINCT question_type FROM questions")]
            )
            regions = sort_regions([row["region"] for row in conn.execute("SELECT DISTINCT region FROM questions")])
        default_question_type = selected_definition["question_type"]
        type_options = option_list(question_types, default_question_type)
        region_options = option_list(regions, "")
        status_options = select_options(
            [
                ("", "不限"),
                ("unattempted", "未作答"),
                ("attempted", "已作答"),
                ("ungraded", "未批改"),
                ("graded", "已批改"),
            ],
            "",
        )
        conversation_rows = render_agent_thread_list(conversations)
        run_rows = []
        for run in runs:
            run_summary = agent_summary_for_display(run["input_summary"] or run["user_goal"] or "尚无摘要")
            run_rows.append(f"""
            <article class="agent-run-row">
              <a href="/agent/runs/{run["id"]}"><strong>{esc(run["task_type"])}</strong><span>{esc(run["status"])} · {esc(format_beijing_time(run["created_at"]))}</span></a>
              <p>{esc(run_summary)}</p>
            </article>""")
        if not run_rows:
            run_rows.append('<p class="muted">暂无分析记录。</p>')
        mode_label = "分析可用" if settings and resolve_api_key(settings) else "尚未配置"
        summary = judgement["context"]["summary"]
        weakness_profile = judgement["context"].get("weakness_profile") or []
        skill_gaps = judgement["context"].get("skill_gaps") or []
        recent_attempts = judgement["context"].get("recent_attempts") or []
        recent_notes = judgement["context"].get("recent_notes") or []
        candidates = judgement.get("candidates") or []
        type_context = "".join(
            f"""
            <div class="agent-context-row">
              <span>{esc(item["name"])}</span>
              <strong>{item["questions"]} / {item["total"]}</strong>
            </div>"""
            for item in judgement["context"]["type_stats"]
        )
        module_tabs = "".join(
            f'<a class="agent-module-tab{" active" if module_id == selected_module else ""}" href="/agent?module={esc(module_id)}">'
            f"<strong>{esc('全部题型' if module_id == 'overview' else definition['label'])}</strong><span>{esc(definition['focus'])}</span></a>"
            for module_id, definition in MODULES.items()
            if module_id in visible_modules
        )
        module_workbench = f"""
        <section class="agent-module-workbench">
          <div class="agent-section-heading">
            <div><p class="eyebrow">专项分析</p><h3>按题型分析</h3></div>
            <span>选择题型后，只分析对应题型的作答与批改</span>
          </div>
          <div class="agent-module-tabs">{module_tabs}</div>
        </section>"""

        average_score = summary.get("average_score")
        average_score_text = f"{float(average_score):.1f}" if average_score is not None else "—"
        metric_cards = "".join(
            (
                f'<article><span>{esc(label)}</span><strong>{esc(str(value))}</strong><small>{esc(note)}</small></article>'
                for label, value, note in (
                    ("累计作答", summary.get("attempt_count", 0), "已保存的作答"),
                    ("批改报告", summary.get("report_count", 0), "已保存的批改"),
                    ("平均得分", average_score_text, "按有分数的报告统计"),
                    ("连续训练", f'{summary.get("streak", 0)} 天', f'累计活跃 {summary.get("active_days", 0)} 天'),
                )
            )
        )

        quick_actions = [
            (
                "诊断",
                "分析主要失分问题",
                "overview",
                "结合我的全部作答、批改、笔记和长期记忆，诊断当前最影响分数的三个问题，按优先级说明证据、原因和下一步训练动作。",
                "today",
            ),
            (
                "失分",
                "查看反复出现的失分点",
                "top_loss",
                "分析我所有训练里最反复、最严重、最影响得分的失分点。必须给出代表证据，并区分偶发问题和稳定短板。",
                "today",
            ),
            (
                "训练",
                "按短板推荐练习题",
                "improvement",
                "结合我的薄弱项、最近训练和题库情况，推荐下一道最值得做的题，并说明训练目标、做题时重点观察什么。",
                "next_question",
            ),
        ]
        if judgement.get("latest_attempt_id"):
            quick_actions.insert(
                1,
                (
                    "复盘",
                    "复盘最近一次作答",
                    selected_module,
                    "复盘最近一次作答：先判断最大失分原因，再指出原答案具体怎么改，最后给一条可迁移到下一题的规则。",
                    "recent_review",
                ),
            )
        quick_action_html = []
        for badge, title, module_id, prompt, entrypoint in quick_actions:
            attempt_input = (
                f'<input type="hidden" name="attempt_id" value="{judgement["latest_attempt_id"]}">'
                if entrypoint == "recent_review" and judgement.get("latest_attempt_id")
                else ""
            )
            quick_action_html.append(
                f"""
                <form method="post" action="/agent/conversations" class="agent-mission-card">
                  <input type="hidden" name="module" value="{esc(module_id)}">
                  <input type="hidden" name="entrypoint" value="{esc(entrypoint)}">
                  <input type="hidden" name="message" value="{esc(prompt)}">
                  {attempt_input}
                  <button type="submit">
                    <span>{esc(badge)}</span>
                    <strong>{esc(title)}</strong>
                    <small>开始分析 →</small>
                  </button>
                </form>"""
            )

        weakness_rows = []
        for item in weakness_profile[:5]:
            severity = max(0.0, min(1.0, float(item.get("severity") or 0)))
            impact_label = "高影响" if severity >= 0.72 else "中影响" if severity >= 0.42 else "低影响"
            label = str(item.get("problem_type") or item.get("question_type") or "待归类短板")
            question_type = str(
                item.get("question_type")
                or ("全部题型" if item.get("module") == "overview" else module_definition(item.get("module"))["label"])
                or "全部题型"
            )
            weakness_rows.append(
                f"""
                <div class="agent-weakness-row">
                  <div><strong>{esc(label)}</strong><span>{esc(question_type)} · 出现 {int(item.get("frequency") or 0)} 次</span></div>
                  <i aria-label="影响度：{esc(impact_label)}"><b style="width:{round(severity * 100)}%"></b></i>
                  <small>{esc(impact_label)}</small>
                </div>"""
            )
        if not weakness_rows:
            weakness_rows.append(
                '<div class="agent-insight-empty"><strong>暂无稳定短板</strong><span>批改积累后，这里会显示反复出现的问题。</span></div>'
            )

        recent_attempt_rows = []
        for item in recent_attempts[:4]:
            review_suffix = " · 已批改" if int(item.get("report_count") or 0) else " · 待批改"
            recent_attempt_rows.append(
                f"""
                <a class="agent-review-row" href="/agent?mode=review&attempt_id={int(item['id'])}">
                  <span>{esc(item.get('question_type') or '申论')} · {esc(str(item.get('region') or ''))}</span>
                  <strong>{esc(item.get('title') or '最近作答')}</strong>
                  <small>{esc(format_beijing_time(item.get('created_at')))}{esc(review_suffix)}</small>
                </a>"""
            )
        if not recent_attempt_rows:
            recent_attempt_rows.append(
                '<div class="agent-insight-empty"><strong>还没有作答记录</strong><span>完成一题后，可从这里一键进入带材料、答案和批改的复盘。</span></div>'
            )

        candidate_rows = []
        for item in candidates[:3]:
            candidate_rows.append(
                f"""
                <a class="agent-candidate-row" href="/questions/{int(item['id'])}">
                  <div><span>{esc(item.get('question_type') or '申论')}</span><small>{esc(str(item.get('year') or ''))} · {esc(item.get('region') or '')}</small></div>
                  <strong>{esc(item.get('title') or item.get('question_code') or '推荐训练题')}</strong>
                  <em>去作答 →</em>
                </a>"""
            )
        if not candidate_rows:
            candidate_rows.append(
                '<div class="agent-insight-empty"><strong>暂无符合条件的题目</strong><span>可调整题型、地区或作答状态后再试。</span></div>'
            )

        skill_rows = []
        for item in skill_gaps[:4]:
            name = item.get("label") or item.get("skill_name") or item.get("name") or item.get("title") or item.get("skill_key") or "能力项"
            skill_rows.append(
                f'<span class="agent-skill-chip"><strong>{esc(name)}</strong><small>{esc(item.get("problem_type") or "关联短板")}</small></span>'
            )
        if not skill_rows and recent_notes:
            for item in recent_notes[:3]:
                skill_rows.append(
                    f'<span class="agent-skill-chip"><strong>个人复盘</strong><small>{esc(item.get("note") or "已有复盘笔记")}</small></span>'
                )
        if not skill_rows:
            skill_rows.append('<span class="agent-skill-chip is-empty"><strong>暂无对应能力项</strong><small>需要更多批改记录</small></span>')

        dashboard = f"""
        <section class="agent-command-center">
          <div class="agent-command-hero">
            <div>
              <p class="eyebrow">训练概览</p>
              <h2>{esc(judgement["lead"])}</h2>
              <p>可直接分析历史作答、复盘最近一题或按短板推荐练习；分析会使用已有作答、批改和笔记。</p>
            </div>
            <span class="agent-mode-pill">{esc(mode_label)}</span>
          </div>
          <div class="agent-metric-grid">{metric_cards}</div>
          <section class="agent-mission-section">
            <div class="agent-section-heading">
              <div><p class="eyebrow">快捷功能</p><h3>常用分析</h3></div>
              <span>选择一项直接开始</span>
            </div>
            <div class="agent-mission-grid">{"".join(quick_action_html)}</div>
          </section>
          {module_workbench}
          <section class="agent-training-board">
            <div class="agent-section-heading">
              <div><p class="eyebrow">训练入口</p><h3>作答与推荐</h3></div>
              <a href="/statistics">训练统计 →</a>
            </div>
            <div class="agent-training-columns">
              <div><h4>最近作答</h4>{"".join(recent_attempt_rows)}</div>
              <div><h4>推荐训练题</h4>{"".join(candidate_rows)}</div>
            </div>
          </section>
        </section>"""
        current_attempt_review = ""
        composer_hidden_scope = ""
        composer_context_note = f"当前范围：{esc(selected_scope_label)}"
        composer_placeholder = (
            f"输入问题，例如：{esc(selected_scope_label)}最常见的失分点是什么？下一步怎么练？"
        )
        if mode == "review" and attempt_id:
            composer_hidden_scope = f"""
              <input type="hidden" name="entrypoint" value="recent_review">
              <input type="hidden" name="attempt_id" value="{esc(attempt_id)}">"""
            composer_context_note = "当前作答：本题复盘"
            composer_placeholder = "输入问题，例如：这次作答最大失分点在哪里？原答案该怎么改？"
            current_attempt_review = f"""
            <form method="post" action="/agent/conversations" class="agent-inline-action">
              <input type="hidden" name="entrypoint" value="recent_review">
              <input type="hidden" name="attempt_id" value="{esc(attempt_id)}">
              <input type="hidden" name="message" value="复盘本题">
              <button class="agent-review-button" type="submit">
                <strong>复盘本题</strong>
                <span>已带入当前作答、题目材料和批改报告</span>
              </button>
            </form>"""
        body = f"""
        <section class="agent-workspace agent-workspace-home">
          <aside class="agent-thread-rail" aria-label="分析记录">
            <div class="agent-rail-head">
              <div><p class="eyebrow">训练分析</p><h1>训练分析</h1><span>作答分析 · 复盘 · 推荐</span></div>
              <a class="button small primary" href="/agent">新对话</a>
            </div>
            <div class="agent-thread-list">{conversation_rows}</div>
            <nav class="agent-rail-tools" aria-label="相关功能">
              <a href="/statistics"><strong>训练统计</strong><span>查看分数、题型与训练天数</span></a>
              <a href="/agent/memories"><strong>个人偏好</strong><span>目标考试、回答方式等</span></a>
              <a href="/agent/evals"><strong>回答质量</strong><span>查看准确性和引用情况</span></a>
            </nav>
          </aside>
          <article class="agent-main">
            <section class="agent-message-stream">
              {dashboard}
              {current_attempt_review}
            </section>
            <form class="agent-composer agent-composer-prominent" method="post" action="/agent/conversations" data-agent-composer>
              <input type="hidden" name="module" value="{esc(selected_module)}">
              {composer_hidden_scope}
              <div class="agent-composer-title"><strong>自由提问</strong><span>默认使用当前题型和已有训练记录</span></div>
              <details class="agent-scope-settings">
                <summary>筛选范围</summary>
                <div class="agent-scope-grid">
                  <label><span>题型</span><select name="question_type">{type_options}</select></label>
                  <label><span>地区</span><select name="region">{region_options}</select></label>
                  <label><span>作答状态</span><select name="work_status">{status_options}</select></label>
                  <label><span>关键词</span><input name="q" placeholder="题目、试卷或训练主题"></label>
                </div>
              </details>
              <textarea name="message" rows="3" placeholder="{esc(composer_placeholder)}"></textarea>
              <div class="agent-composer-footer">
                <span class="muted">{composer_context_note}</span>
                <button class="button primary" type="submit">发送</button>
              </div>
            </form>
          </article>
          <aside class="agent-insight-rail" aria-label="训练分析结果">
            <section>
              <div class="agent-section-heading compact"><div><p class="eyebrow">问题统计</p><h3>常见失分问题</h3></div></div>
              <div class="agent-weakness-list">{"".join(weakness_rows)}</div>
            </section>
            <section>
              <div class="agent-section-heading compact"><div><p class="eyebrow">能力项</p><h3>需要加强的能力</h3></div></div>
              <div class="agent-skill-list">{"".join(skill_rows)}</div>
            </section>
            <section class="agent-data-scope">
              <div class="agent-section-heading compact"><div><p class="eyebrow">数据范围</p><h3>本次分析数据</h3></div></div>
              <div class="agent-context-metrics">
                <div><strong>{summary.get('attempt_count', 0)}</strong><span>作答</span></div>
                <div><strong>{summary.get('report_count', 0)}</strong><span>报告</span></div>
                <div><strong>{len(recent_notes)}</strong><span>近期笔记</span></div>
              </div>
              <div class="agent-context-list">{type_context or '<p class="muted">暂无题型统计。</p>'}</div>
            </section>
          </aside>
        </section>
        """
        self.send_html(layout("训练分析 - 研申", body, "agent", flashes))

    def page_agent_conversation(self, path):
        try:
            conversation_id = int(path.strip("/").split("/")[2])
        except (ValueError, IndexError):
            self.send_error(404)
            return
        with connect(self.db_path) as conn:
            conversation, messages = conversation_payload(conn, conversation_id)
            if not conversation:
                self.send_error(404)
                return
            conversations = recent_conversations(conn, 50)
            run_ids = [message["run_id"] for message in messages if message["run_id"]]
            run_steps_by_id = {}
            run_rows = []
            for run_id in run_ids[-5:]:
                run = get_run(conn, run_id)
                if run:
                    run_rows.append(run)
                run_steps_by_id[run_id] = get_run_steps(conn, run_id)
            conversation_module = ""
            for message in reversed(messages):
                try:
                    metadata = json.loads(message["metadata_json"] or "{}")
                except json.JSONDecodeError:
                    metadata = {}
                if metadata.get("module"):
                    conversation_module = metadata["module"]
                    break
        message_rows = []
        has_pending_message = False
        last_role = messages[-1]["role"] if messages else ""
        for message in messages:
            is_pending = message["message_type"] == "pending"
            if is_pending:
                has_pending_message = True
            message_rows.append(
                render_agent_message_row(message, self.path, run_steps_by_id.get(message["run_id"], []))
            )
            continue
            try:
                message_metadata = json.loads(message["metadata_json"] or "{}")
            except json.JSONDecodeError:
                message_metadata = {}
            pending_status = (
                render_agent_pending_status(run_steps_by_id.get(message["run_id"], [])) if is_pending else ""
            )
            run_link = (
                f"""
                <div class="agent-message-actions">
                  <a class="button small" href="/agent/runs/{message["run_id"]}">执行详情</a>
                </div>"""
                if message["run_id"]
                else ""
            )
            message_rows.append(f"""
            <article class="agent-message {esc(message["role"])}{" is-pending" if is_pending else ""}"{' data-agent-pending="1"' if is_pending else ""}>
              <header><strong>{"我" if message["role"] == "user" else "分析结果"}</strong><span>{"分析中" if is_pending else esc(format_beijing_time(message["created_at"]))}</span></header>
              <div class="report-body">{agent_response_html(message["content"], self.path) if message["role"] == "assistant" else f"<p>{esc(message['content'])}</p>"}</div>
              {render_agent_rag_panel(message_metadata, self.path) if message["role"] == "assistant" and not is_pending else ""}
              {pending_status}
              {('<div class="agent-thinking-dots" aria-label="生成中"><span></span><span></span><span></span></div>' if is_pending else "")}
              {run_link}
            </article>""")
        conversation_rows = render_agent_thread_list(
            conversations,
            active_conversation_id=conversation_id,
            return_to=f"/agent/conversations/{conversation_id}",
        )
        trace_rows = []
        for run in run_rows:
            run_summary = agent_summary_for_display(run["input_summary"] or run["user_goal"] or "查看执行详情")
            trace_rows.append(f"""
            <article class="agent-run-row">
              <a href="/agent/runs/{run["id"]}"><strong>{esc(run["task_type"])}</strong><span>{esc(run["status"])}</span></a>
              <p>{esc(run_summary)}</p>
            </article>""")
        if not trace_rows:
            trace_rows.append('<p class="muted">本线程还没有关联的运行轨迹。</p>')
        awaiting_attr = ' data-agent-awaiting="1"' if last_role == "user" else ""
        body = f"""
        <section class="agent-workspace">
          <aside class="agent-thread-rail" aria-label="分析记录">
            <div class="agent-rail-head">
              <div><p class="eyebrow">对话记录</p><h1>历史对话</h1></div>
              <a class="button small primary" href="/agent">新对话</a>
            </div>
            <div class="agent-thread-list">{conversation_rows}</div>
          </aside>
          <article class="agent-main">
            <header class="agent-main-head">
              <div>
                <span class="agent-mode-pill">对话 #{conversation_id}{" · " + esc(module_definition(conversation_module)["label"]) if conversation_module else ""}</span>
                <h2>{esc(conversation["title"] or "未命名对话")}</h2>
                <p>继续提问会保留本次对话中的题型和筛选条件。</p>
              </div>
              <div class="agent-head-actions"><a class="button ghost" href="/agent">返回首页</a></div>
            </header>
            <section class="agent-message-stream"{awaiting_attr}>
              {"".join(message_rows)}
            </section>
            <form class="agent-composer" method="post" action="/agent/conversations/{conversation_id}/messages" data-agent-composer>
              <input type="hidden" name="module" value="{esc(conversation_module)}">
              <textarea name="message" rows="3" placeholder="继续追问…"></textarea>
              <div class="agent-composer-footer"><span class="muted">保留本次对话的题型与筛选条件</span><button class="button primary" type="submit">发送</button></div>
            </form>
          </article>
        </section>
        """
        self.send_html(layout("训练分析对话 - 研申", body, "agent"))

    def page_agent_knowledge(self, path, query):
        knowledge_id = path.removeprefix("/agent/knowledge/").strip()
        card = next((item for item in load_knowledge_items() if item.get("id") == knowledge_id), None)
        if not card:
            self.send_error(404)
            return
        return_to = return_path_from_query(query, "/agent")

        def list_block(title, values):
            rows = "".join(f"<li>{esc(value)}</li>" for value in (values or []) if str(value).strip())
            return f"<h3>{esc(title)}</h3><ul>{rows}</ul>" if rows else ""

        body = f"""
        <section class="page-head">
          <div><p class="eyebrow">引用依据</p><h1>{esc(card.get("title"))}</h1></div>
          <div class="actions">{back_link(return_to, "返回引用位置")}</div>
        </section>
        <section class="settings-panel report-body">
          <p class="muted">资料编号：{esc(card.get("id"))} · 题型：{esc(module_definition(card.get("module"))["label"])}</p>
          <p>{esc(card.get("content"))}</p>
          {list_block("示例", card.get("examples"))}
          {list_block("常见误区", card.get("pitfalls"))}
          {list_block("适用范围", card.get("applicable_when"))}
          {list_block("不适用范围", card.get("not_applicable_when"))}
        </section>
        """
        self.send_html(layout(f"教材依据 - {card.get('title')}", body, "agent"))

    def page_agent_memories(self, flashes=None):
        with connect(self.db_path) as conn:
            rows = active_memories(conn, limit=100)
        type_labels = {
            "semantic": "用户信息",
            "episodic": "训练事件",
            "procedural": "交互偏好",
        }
        cards = []
        for row in rows:
            source_link = (
                f'<a href="/agent/conversations/{row["source_conversation_id"]}">来源对话</a>'
                if row["source_conversation_id"]
                else "无来源对话"
            )
            cards.append(f"""
            <article class="settings-panel">
              <div class="section-head">
                <div><p class="eyebrow">{esc(type_labels.get(row["memory_type"], row["memory_type"]))}</p><h2>{esc(row["memory_key"])}</h2></div>
                <form method="post" action="/agent/memories/{row["id"]}/delete" data-confirm="删除这条个人偏好吗？">
                  <button class="button small ghost" type="submit">删除</button>
                </form>
              </div>
              <p>{esc(row["content"])}</p>
              <p class="muted">记录可信度 {float(row["confidence"]):.2f} · {source_link} · 更新于 {esc(format_beijing_time(row["updated_at"]))}</p>
            </article>
            """)
        if not cards:
            cards.append(
                '<section class="text-block"><p class="muted">暂无已保存的个人偏好。目标考试、训练节奏和回答方式等信息可在后续对话中自动记录。</p></section>'
            )
        body = f"""
        <section class="page-head">
          <div><p class="eyebrow">个人设置</p><h1>个人偏好</h1><p>保存目标考试、回答方式和训练节奏等信息，后续对话会自动使用。</p></div>
          <div class="actions">
            <a class="button ghost" href="/agent">返回训练分析</a>
            <form method="post" action="/agent/memories/clear" data-confirm="清空全部个人偏好吗？对话记录不会被删除。">
              <button class="button ghost" type="submit">清空个人偏好</button>
            </form>
          </div>
        </section>
        <section class="settings-grid">{"".join(cards)}</section>
        """
        self.send_html(layout("个人偏好 - 研申", body, "agent", flashes))

    def page_agent_setup(self, query, flashes=None):
        self.redirect("/settings#ai-coach-settings")

    def page_agent_evals(self, flashes=None):
        with connect(self.db_path) as conn:
            rows = latest_eval_results(conn, 20)
        result_rows = []

        def fmt_metric(value):
            if value is None:
                return "-"
            try:
                return f"{float(value):.2f}"
            except (TypeError, ValueError):
                return esc(value)

        for row in rows:
            try:
                metrics = json.loads(row["metrics_json"] or "{}")
            except json.JSONDecodeError:
                metrics = {}
            ragas_scores = metrics.get("ragas_scores") or {}
            ragas_status = metrics.get("ragas_status") or "not_run"
            ragas_average = fmt_metric(metrics.get("ragas_average"))
            layers = metrics.get("layers") or {}
            planner_layer = layers.get("planner") or {}
            retriever_layer = layers.get("retriever") or {}
            tool_layer = layers.get("tool") or {}
            answer_layer = layers.get("answer") or {}
            runtime = metrics.get("runtime") or {}
            token_usage = runtime.get("token_usage") or {}
            run_match = re.search(r"\brun_id=(\d+)", row["notes"] or "")
            trace_link = (
                f'<a class="button small ghost" href="/agent/runs/{run_match.group(1)}">查看执行详情</a>'
                if run_match
                else ""
            )
            result_rows.append(f"""
            <article class="agent-eval-row">
              <header><strong>{esc(row["case_title"])}</strong><span>{row["score"]:.1f}</span></header>
              <p>{esc(row["suite_name"])} · {esc(row["task_type"])} · {esc(format_beijing_time(row["created_at"]))}</p>
              <div class="agent-eval-metrics">
                <span>数据读取 {metrics.get("tool_call_accuracy", 0)}</span>
                <span>任务完成 {metrics.get("agent_goal_accuracy", 0)}</span>
                <span>回答完整 {metrics.get("response_completeness", 0)}</span>
                <span>引用依据 {metrics.get("evidence_proxy", 0)}</span>
                <span>综合评分 {metrics.get("internal_score", row["score"])}</span>
                <span>问题理解 {fmt_metric(planner_layer.get("accuracy"))}</span>
                <span>资料查找 {fmt_metric(retriever_layer.get("recall_at_10"))}</span>
                <span>资料排序 {fmt_metric(retriever_layer.get("mrr"))}</span>
                <span>数据读取 {fmt_metric(tool_layer.get("accuracy"))}</span>
                <span>任务完成 {fmt_metric(answer_layer.get("goal_accuracy"))}</span>
                <span>事实核对 {esc(ragas_status)} · {ragas_average}</span>
                <span>依据一致 {fmt_metric(ragas_scores.get("faithfulness"))}</span>
                <span>引用准确 {fmt_metric(ragas_scores.get("context_precision"))}</span>
                <span>依据覆盖 {fmt_metric(ragas_scores.get("context_recall"))}</span>
                <span>事实正确 {fmt_metric(ragas_scores.get("factual_correctness"))}</span>
              </div>
              <div class="agent-eval-footer">
                <small>{esc(row["notes"])}</small>
                <details><summary>技术信息</summary><small>{esc(runtime.get("model") or "未记录模型")} · {fmt_metric(runtime.get("duration_ms"))} ms · {token_usage.get("total_tokens", "-")} tokens · prompt {esc(runtime.get("prompt_version") or "-")} · retrieval {esc(runtime.get("retrieval_version") or "-")}</small></details>
                {trace_link}
                <form method="post" action="/agent/evals/{row["id"]}/delete" data-confirm="删除这条评测记录吗？">
                  <button class="button small ghost" type="submit">删除</button>
                </form>
              </div>
            </article>""")
        if not result_rows:
            result_rows.append('<p class="muted">暂无质量检查结果。运行一次固定测试集即可生成。</p>')
        body = f"""
        <section class="page-head">
          <div><p class="eyebrow">高级功能</p><h1>回答质量检查</h1></div>
          <div class="actions"><a class="button ghost" href="/agent">返回训练分析</a></div>
        </section>
        <section class="agent-result-layout">
          <article class="agent-result-main">
            <section class="text-block">
              <h2>快速检查</h2>
              <p class="muted">共准备 60 条可复核用例。页面每次抽取 5 条检查；完整检查可通过项目脚本运行。</p>
              <div class="actions">
              <form method="post" action="/agent/evals/run">
                <button class="button primary" type="submit">开始检查</button>
              </form>
              <form method="post" action="/agent/evals/clear" data-confirm="清空所有评测记录吗？这不会删除教练对话或运行记录。">
                <button class="button ghost" type="submit">清空记录</button>
              </form>
              </div>
            </section>
            <section class="agent-eval-list">
              {"".join(result_rows)}
            </section>
          </article>
          <aside class="agent-side">
            <section class="tool-panel">
              <h2>使用场景</h2>
              <p class="muted">修改训练分析逻辑、调整模型或发现回答质量下降时运行；日常做题和复盘无需使用。</p>
              <h2>指标说明</h2>
              <p class="muted">问题理解看是否读懂用户目标；资料查找和排序看是否找到正确材料；数据读取看训练数据是否调用正确；任务完成看是否回答到位；事实核对看回答是否符合引用依据。</p>
            </section>
          </aside>
        </section>
        """
        self.send_html(layout("回答质量检查 - 研申", body, "agent", flashes))

    def page_agent_run(self, path):
        try:
            run_id = int(path.strip("/").split("/")[2])
        except (ValueError, IndexError):
            self.send_error(404)
            return
        with connect(self.db_path) as conn:
            run = get_run(conn, run_id)
            if not run:
                self.send_error(404)
                return
            steps = get_run_steps(conn, run_id)
            feedback = get_feedback(conn, run_id)
        step_rows = []
        for step in steps:
            try:
                output = json.loads(step["output_json"] or "{}")
            except json.JSONDecodeError:
                output = {}
            preview = json.dumps(output, ensure_ascii=False, indent=2)[:1200]
            step_rows.append(f"""
            <details class="agent-step">
              <summary><strong>{step["step_index"]}. {esc(step["step_type"])}</strong><span>{esc(step["tool_name"])}</span></summary>
              <pre>{esc(preview)}</pre>
            </details>""")
        feedback_html = (
            "".join(f'<p class="muted">评分 {item["rating"]}/5：{esc(item["note"])}</p>' for item in feedback)
            or '<p class="muted">还没有反馈。</p>'
        )
        body = f"""
        <section class="page-head">
          <div><p class="eyebrow">分析记录 #{run_id} · {esc(run["status"])}</p><h1>分析结果</h1></div>
          <div class="actions">
            <a class="button ghost" href="/agent">返回训练分析</a>
          </div>
        </section>
        <section class="agent-result-layout">
          <article class="agent-result-main">
            <section class="report-card">
              <header><strong>{esc(run["task_type"])}</strong><span>{esc(run["provider"])} / {esc(run["model"])} · {esc(format_beijing_time(run["created_at"]))}</span></header>
              <div class="report-body">{agent_response_html(run["final_text"], f"/agent/runs/{run_id}")}</div>
            </section>
          </article>
          <aside class="agent-side">
            <section class="tool-panel">
              <h2>运行摘要</h2>
              <p class="muted">{esc(agent_summary_for_display(run["input_summary"] or run["user_goal"] or "无摘要"))}</p>
            </section>
            <section class="tool-panel">
              <h2>执行详情</h2>
              <div class="agent-step-list">{"".join(step_rows) or '<p class="muted">暂无执行详情。</p>'}</div>
            </section>
            <section class="tool-panel">
              <h2>反馈</h2>
              {feedback_html}
              <form method="post" action="/agent/runs/{run_id}/feedback">
                <label><span>评分</span><select name="rating"><option value="5">5 - 有用</option><option value="4">4</option><option value="3">3</option><option value="2">2</option><option value="1">1 - 没用</option></select></label>
                <label><span>备注</span><textarea name="note" rows="4"></textarea></label>
                <button class="button primary" type="submit">保存反馈</button>
              </form>
            </section>
          </aside>
        </section>
        """
        self.send_html(layout("分析结果 - 研申", body, "agent"))

    def handle_agent_run(self):
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8") if length else ""
        form = parse_qs(data, keep_blank_values=True)
        task_type = form.get("task_type", ["diagnosis"])[0]
        attempt_id = _int_or_none(form.get("attempt_id", [""])[0])
        subject_id = attempt_id if task_type == "review" else None
        filters = {
            "question_type": form.get("question_type", [""])[0].strip(),
            "region": form.get("region", [""])[0].strip(),
            "work_status": form.get("work_status", [""])[0].strip(),
            "q": form.get("q", [""])[0].strip(),
        }
        user_goal = form.get("user_goal", [""])[0].strip()
        auto_approve = form.get("save_plan", [""])[0] == "1"
        try:
            run_id = run_agent(
                self.db_path,
                task_type,
                subject_id=subject_id,
                user_goal=user_goal,
                filters=filters,
                auto_approve=auto_approve,
                module=form.get("module", [""])[0].strip(),
            )
        except AgentRunError as exc:
            self.page_agent({}, [("error", f"训练分析失败：{exc}")])
            return
        self.redirect(f"/agent/runs/{run_id}")

    def handle_agent_conversation(self):
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8") if length else ""
        form = parse_qs(data, keep_blank_values=True)
        filters = {
            "question_type": form.get("question_type", [""])[0].strip(),
            "region": form.get("region", [""])[0].strip(),
            "work_status": form.get("work_status", [""])[0].strip(),
            "q": form.get("q", [""])[0].strip(),
        }
        try:
            conversation_id, _ = start_or_continue_chat_async(
                self.db_path,
                user_text=form.get("message", [""])[0].strip(),
                entrypoint=form.get("entrypoint", ["chat"])[0].strip() or "chat",
                filters=filters,
                module=form.get("module", [""])[0].strip(),
                auto_approve=False,
                review_attempt_id=_int_or_none(form.get("attempt_id", [""])[0]),
            )
        except AgentRunError as exc:
            self.page_agent({}, [("error", f"训练分析失败：{exc}")])
            return
        self.redirect(f"/agent/conversations/{conversation_id}")

    def handle_agent_message(self, path):
        try:
            conversation_id = int(path.strip("/").split("/")[2])
        except (ValueError, IndexError):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            conversation_id, _ = start_or_continue_chat_async(
                self.db_path,
                conversation_id=conversation_id,
                user_text=form_value(data, "message", ""),
                module=form_value(data, "module", ""),
                entrypoint="chat",
                auto_approve=False,
            )
        except AgentRunError as exc:
            self.page_agent({}, [("error", f"训练分析失败：{exc}")])
            return
        self.redirect(f"/agent/conversations/{conversation_id}")

    def handle_agent_conversation_status(self, path):
        try:
            conversation_id = int(path.strip("/").split("/")[2])
        except (ValueError, IndexError):
            self.send_json({"error": "not_found"}, status=404)
            return
        try:
            with connect(self.db_path) as conn:
                conversation = conn.execute(
                    "SELECT id FROM agent_conversations WHERE id = ?",
                    (conversation_id,),
                ).fetchone()
                if not conversation:
                    self.send_json({"conversation_id": conversation_id, "pending": False, "awaiting": False, "deleted": True})
                    return
                _cleanup_orphaned_pending_messages(conn, conversation_id)
                latest = conn.execute(
                    """
                    SELECT *
                      FROM agent_messages
                     WHERE conversation_id = ?
                  ORDER BY id DESC LIMIT 1
                    """,
                    (conversation_id,),
                ).fetchone()
                if not latest:
                    self.send_json({"conversation_id": conversation_id, "pending": False, "awaiting": False})
                    return
                is_pending = latest["message_type"] == "pending"
                steps = []
                if latest["run_id"]:
                    steps = [
                        {"tool_name": step["tool_name"], "step_type": step["step_type"], "index": step["step_index"]}
                        for step in get_run_steps(conn, latest["run_id"])
                    ]
                self.send_json(
                    {
                        "conversation_id": conversation_id,
                        "latest_message_id": latest["id"],
                        "latest_role": latest["role"],
                        "latest_type": latest["message_type"],
                        "run_id": latest["run_id"],
                        "pending": is_pending,
                        "awaiting": latest["role"] == "user",
                        "steps": steps,
                        "message_html": ""
                        if is_pending
                        else render_agent_message_row(latest, f"/agent/conversations/{conversation_id}", steps),
                    }
                )
        except Exception as exc:
            logging.exception("Agent conversation status failed")
            self.send_json({"error": str(exc), "retry": True}, status=503)

    def handle_agent_conversation_delete(self, path):
        try:
            conversation_id = int(path.strip("/").split("/")[2])
        except (ValueError, IndexError):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8") if length else ""
        return_to = return_path_from_form(data, "/agent")
        for attempt in range(5):
            try:
                with connect(self.db_path) as conn:
                    delete_conversation(conn, conversation_id)
                break
            except Exception as exc:
                if "locked" in str(exc).lower() and attempt < 4:
                    time.sleep(0.15 * (attempt + 1))
                else:
                    raise
        if return_to.startswith(f"/agent/conversations/{conversation_id}"):
            return_to = "/agent"
        self.redirect(return_to)

    def handle_agent_setup_save(self):
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8") if length else ""
        provider_name = form_value(data, "provider_name", "DeepSeek")
        api_base_url = form_value(data, "api_base_url", "https://api.deepseek.com")
        model = form_value(data, "model", "deepseek-v4-pro")
        api_key = form_value(data, "api_key", "")
        api_key_env = form_value(data, "api_key_env", "")
        with connect(self.db_path) as conn:
            current = conn.execute("SELECT * FROM agent_ai_settings WHERE id = 1").fetchone()
            stored_key = api_key or current["api_key"]
            conn.execute(
                """
                UPDATE agent_ai_settings
                   SET use_grading_api = 0,
                       provider_name = ?,
                       api_base_url = ?,
                       api_key = ?,
                       api_key_env = ?,
                       model = ?,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = 1
                """,
                (provider_name, api_base_url, stored_key, api_key_env, model),
            )
        self.redirect("/agent")

    def handle_agent_setup_test(self):
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8") if length else ""
        settings = {
            "provider_name": form_value(data, "provider_name", "DeepSeek"),
            "api_base_url": form_value(data, "api_base_url", "https://api.deepseek.com"),
            "model": form_value(data, "model", "deepseek-v4-pro"),
            "api_key": form_value(data, "api_key", ""),
            "api_key_env": form_value(data, "api_key_env", ""),
            "temperature": 0,
        }
        try:
            chat_completion(settings, "请只回复 OK，用于测试连接。")
        except (AiConfigError, AiRequestError) as exc:
            self.page_agent_setup({"provider": [settings["provider_name"]]}, [("error", f"连接测试失败：{exc}")])
            return
        self.page_agent_setup({"provider": [settings["provider_name"]]}, [("success", "连接测试成功，可以保存配置。")])

    def handle_agent_memory_delete(self, path):
        try:
            memory_id = int(path.strip("/").split("/")[2])
        except (ValueError, IndexError):
            self.send_error(404)
            return
        with connect(self.db_path) as conn:
            if not delete_memory(conn, memory_id):
                self.send_error(404)
                return
        self.redirect("/agent/memories")

    def handle_agent_memories_clear(self):
        with connect(self.db_path) as conn:
            clear_memories(conn)
        self.redirect("/agent/memories")

    def handle_agent_eval_run(self):
        try:
            results = run_eval_suite(self.db_path)
        except AgentRunError as exc:
            self.page_agent_evals([("error", f"评测运行失败：{exc}")])
            return
        self.page_agent_evals([("success", f"评测完成：{len(results)} 个用例。")])

    def handle_agent_eval_delete(self, path):
        try:
            eval_id = int(path.strip("/").split("/")[2])
        except (ValueError, IndexError):
            self.send_error(404)
            return
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM agent_eval_results WHERE id = ?", (eval_id,))
        self.redirect("/agent/evals")

    def handle_agent_eval_clear(self):
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM agent_eval_results")
        self.redirect("/agent/evals")

    def handle_agent_feedback(self, path):
        try:
            run_id = int(path.strip("/").split("/")[2])
        except (ValueError, IndexError):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8") if length else ""
        with connect(self.db_path) as conn:
            add_feedback(
                conn,
                run_id,
                form_value(data, "rating", "5"),
                form_value(data, "note", ""),
            )
        self.redirect(f"/agent/runs/{run_id}")
