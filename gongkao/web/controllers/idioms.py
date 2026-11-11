"""Local idiom-learning page and JSON endpoints."""

from __future__ import annotations

import json
import math

from ...idioms import (
    CORE_EXPECTED_ENTRIES,
    QUIZ_KINDS,
    build_quiz,
    comparison_groups,
    core_categories,
    filter_core_entries,
    filter_mistake_entries,
    load_progress,
    progress_summary,
    review_queue,
    update_progress,
)
from ..runtime import connect, layout

STATUS_LABELS = {
    "unseen": "未学习",
    "learning": "学习中",
    "mastered": "已掌握",
}


def _bounded_int(value, default, minimum, maximum):
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _review_due_count(progress):
    return sum(
        1
        for state in progress.values()
        if int(state.get("wrong_count", 0)) > 0 or state.get("status") == "learning" or state.get("is_starred")
    )


def _entry_payload(row, progress):
    state = progress.get(row["id"], {})
    correct = int(state.get("correct_count", 0))
    wrong = int(state.get("wrong_count", 0))
    reviewed = correct + wrong
    reasons = []
    if wrong:
        reasons.append(f"错 {wrong} 次")
    if state.get("status") == "learning":
        reasons.append("学习中")
    if state.get("is_starred"):
        reasons.append("已收藏")
    if reviewed and round(correct * 100 / reviewed) < 80:
        reasons.append(f"正确率 {round(correct * 100 / reviewed)}%")
    return {
        "id": row["id"],
        "term": row.get("term") or "",
        "category": row.get("category") or "未分类",
        "subcategory": row.get("subcategory") or "",
        "definition": row.get("definition") or "",
        "explanation": row.get("explanation") or "",
        "pinyin": row.get("pinyin") or "",
        "derivation": row.get("derivation") or "",
        "example": row.get("example") or "",
        "status": state.get("status", "unseen"),
        "status_label": STATUS_LABELS.get(state.get("status", "unseen"), "未学习"),
        "is_starred": bool(state.get("is_starred")),
        "review_count": int(state.get("review_count", 0)),
        "correct_count": correct,
        "wrong_count": wrong,
        "accuracy": round(correct * 100 / reviewed) if reviewed else None,
        "review_reason": " · ".join(reasons[:3]) or "新词首轮",
    }


def _contrast_entry_payload(row, progress):
    payload = _entry_payload(row, progress)
    payload["usage_analysis"] = row.get("usage_analysis") or {}
    return payload


class IdiomsController:
    def page_idioms(self, query=None):
        body = """
        <div class="idiom-page" data-idiom-page>
          <section class="idiom-hero">
            <div class="idiom-hero-copy">
              <p class="eyebrow">IDIOM LAB · 言语积累</p>
              <h1>成语学习</h1>
              <p>把“看过”变成“能辨析、会放进语境里”。核心词库、易混辨析、错题积累和小测放在同一个学习闭环里。</p>
            </div>
            <div class="idiom-hero-actions">
              <div><button class="button" type="button" data-idiom-flashcard-open>闪卡复习</button><button class="button primary" type="button" data-idiom-quiz-open>开始成语小测</button></div>
              <span>本机记录掌握度与答题正确率</span>
            </div>
          </section>

          <section class="idiom-overview" aria-label="成语学习概览">
            <article><span>词库覆盖</span><strong data-idiom-metric="total">700</strong><small>65 类核心词</small></article>
            <article><span>已掌握</span><strong data-idiom-metric="mastered">—</strong><small>可继续回炉</small></article>
            <article><span>学习中</span><strong data-idiom-metric="learning">—</strong><small>正在建立辨析</small></article>
            <article><span>待回炉</span><strong data-idiom-metric="review_due">—</strong><small>错词 · 学习中 · 收藏</small></article>
            <article><span>测验正确率</span><strong data-idiom-metric="accuracy">—</strong><small data-idiom-reviewed>暂无答题记录</small></article>
          </section>

          <section class="idiom-workbench">
            <aside class="idiom-control-panel">
              <div class="idiom-control-heading">
                <p class="eyebrow">LEARNING MODE</p>
                <h2>学习方式</h2>
              </div>
              <div class="idiom-view-switch" role="tablist" aria-label="成语学习方式">
                <button type="button" class="is-active" data-idiom-view="library" role="tab" aria-selected="true"><strong>词库</strong><span>按分类系统学习</span></button>
                <button type="button" data-idiom-view="review" role="tab" aria-selected="false"><strong>今日回炉</strong><span>优先看薄弱与收藏</span></button>
                <button type="button" data-idiom-view="contrast" role="tab" aria-selected="false"><strong>成语辨析</strong><span>161 组相近词逐组辨用法</span></button>
                <button type="button" data-idiom-view="mistakes" role="tab" aria-selected="false"><strong>错题积累</strong><span>《言语理解》全部词汇</span></button>
              </div>

              <form class="idiom-filter-form" data-idiom-filters>
                <label class="idiom-search-field"><span>搜索</span><input type="search" name="q" placeholder="成语、释义或分类" autocomplete="off"></label>
                <label data-idiom-core-only><span>分类</span><select name="category"><option value="">全部分类</option></select></label>
                <label data-idiom-library-only><span>掌握状态</span><select name="status"><option value="">全部状态</option><option value="unseen">未学习</option><option value="learning">学习中</option><option value="mastered">已掌握</option><option value="starred">仅看收藏</option></select></label>
                <div class="idiom-filter-actions"><button class="button small primary" type="submit">筛选</button><button class="button small ghost" type="button" data-idiom-filter-reset>清空</button></div>
              </form>
              <div class="idiom-filter-chips" data-idiom-filter-chips hidden></div>

              <div class="idiom-study-tip">
                <span>建议顺序</span>
                <strong>词义 → 辨析 → 语境 → 回炉</strong>
                <p>遇到似懂非懂的词先标“学习中”，不要急着标掌握。</p>
              </div>
              <button class="idiom-review-launch" type="button" data-idiom-flashcard-open><span>FLASH REVIEW</span><strong>开始闪卡回炉</strong><small>空格翻面 · ← → 切换 · 1/2 标记状态</small></button>
            </aside>

            <main class="idiom-content-panel">
              <header class="idiom-content-head">
                <div><p class="eyebrow" data-idiom-content-eyebrow>CORE LIBRARY</p><h2 data-idiom-content-title>核心词库</h2><p data-idiom-content-note>按释义、分类和掌握状态筛选；收藏的词可以单独回炉。</p></div>
                <span class="idiom-result-count" data-idiom-result-count>正在读取…</span>
              </header>
              <div class="idiom-loading" data-idiom-loading><i></i><span>正在整理词库…</span></div>
              <div class="idiom-error" data-idiom-error hidden><strong>词库暂时无法读取</strong><p data-idiom-error-message>请稍后重试。</p><button class="button small" type="button" data-idiom-retry>重新加载</button></div>
              <div class="idiom-list" data-idiom-list></div>
              <footer class="idiom-pagination" data-idiom-pagination hidden>
                <button class="button small ghost" type="button" data-idiom-page-prev>上一页</button>
                <span data-idiom-page-label>1 / 1</span>
                <button class="button small ghost" type="button" data-idiom-page-next>下一页</button>
              </footer>
            </main>
          </section>

          <div class="idiom-detail-layer" data-idiom-detail-layer hidden>
            <section class="idiom-detail-dialog" role="dialog" aria-modal="true" aria-labelledby="idiom-detail-title">
              <header class="idiom-detail-head">
                <div>
                  <p class="eyebrow">STUDY CARD · 成语学习卡</p>
                  <h2 id="idiom-detail-title" data-idiom-detail-title>成语学习卡</h2>
                </div>
                <button type="button" class="idiom-icon-button" data-idiom-detail-close aria-label="关闭成语学习卡">×</button>
              </header>
              <div class="idiom-detail-body" data-idiom-detail-body></div>
            </section>
          </div>

          <div class="idiom-flashcard-layer" data-idiom-flashcard-layer hidden>
            <section class="idiom-flashcard-dialog" role="dialog" aria-modal="true" aria-labelledby="idiom-flashcard-title">
              <header class="idiom-flashcard-head">
                <div><p class="eyebrow">FLASH REVIEW</p><h2 id="idiom-flashcard-title">闪卡回炉</h2><p>按薄弱程度排序，先回看答错、学习中和收藏的词。</p></div>
                <button type="button" class="idiom-icon-button" data-idiom-flashcard-close aria-label="关闭闪卡复习">×</button>
              </header>
              <div class="idiom-flashcard-progress"><span data-idiom-flashcard-progress>正在准备…</span><i><b data-idiom-flashcard-progress-fill></b></i></div>
              <div class="idiom-flashcard-stage" data-idiom-flashcard-stage></div>
              <footer class="idiom-flashcard-controls">
                <button class="button ghost" type="button" data-idiom-flashcard-prev>← 上一个</button>
                <div><button class="button" type="button" data-idiom-flashcard-learning>1 · 学习中</button><button class="button primary" type="button" data-idiom-flashcard-mastered>2 · 已掌握</button></div>
                <button class="button ghost" type="button" data-idiom-flashcard-next>下一个 →</button>
              </footer>
            </section>
          </div>

          <div class="idiom-quiz-layer" data-idiom-quiz-layer hidden>
            <section class="idiom-quiz-dialog" role="dialog" aria-modal="true" aria-labelledby="idiom-quiz-title">
              <header class="idiom-quiz-head">
                <div><p class="eyebrow">QUICK QUIZ</p><h2 id="idiom-quiz-title">成语小测</h2><p>每题作答后立即看资料释义，答题结果自动进入掌握度统计。</p></div>
                <button type="button" class="idiom-icon-button" data-idiom-quiz-close aria-label="关闭成语小测">×</button>
              </header>
              <div class="idiom-quiz-setup" data-idiom-quiz-setup>
                <div class="idiom-quiz-kind-grid" role="radiogroup" aria-label="测验类型">
                  <button type="button" class="is-active" data-idiom-quiz-kind="meaning"><strong>释义辨认</strong><span>看释义选成语</span></button>
                  <button type="button" data-idiom-quiz-kind="context"><strong>语境选词</strong><span>把词放回句子里</span></button>
                  <button type="button" data-idiom-quiz-kind="contrast"><strong>易混辨析</strong><span>专练相近成语</span></button>
                  <button type="button" data-idiom-quiz-kind="usage"><strong>使用判断</strong><span>判断释义是否匹配</span></button>
                </div>
                <div class="idiom-quiz-start-row"><label><span>题量</span><select data-idiom-quiz-count><option value="10">10 题</option><option value="15">15 题</option><option value="20">20 题</option></select></label><button class="button primary" type="button" data-idiom-quiz-start>开始答题</button></div>
                <p class="idiom-quiz-setup-status" data-idiom-quiz-status></p>
              </div>
              <div class="idiom-quiz-play" data-idiom-quiz-play hidden>
                <div class="idiom-quiz-progress"><div><span data-idiom-quiz-progress-text>1 / 10</span><strong data-idiom-quiz-score>0 正确</strong></div><i><b data-idiom-quiz-progress-fill></b></i></div>
                <div data-idiom-quiz-question></div>
              </div>
              <div class="idiom-quiz-result" data-idiom-quiz-result hidden></div>
            </section>
          </div>
        </div>
        """
        self.send_html(layout("成语学习 - 研申", body, "idioms"))

    def page_idiom_data(self, query=None):
        query = query or {}
        view = query.get("view", ["library"])[0]
        if view not in {"library", "review", "contrast", "mistakes"}:
            view = "library"
        q = query.get("q", [""])[0].strip()
        category = query.get("category", [""])[0].strip()
        status = query.get("status", [""])[0].strip()
        page = _bounded_int(query.get("page", ["1"])[0], 1, 1, 9999)
        page_size = 28
        try:
            with connect(self.db_path) as conn:
                progress = load_progress(conn)
            summary = progress_summary(progress)
            summary["review_due"] = _review_due_count(progress)
            payload = {
                "ok": True,
                "view": view,
                "summary": {**summary, "total": CORE_EXPECTED_ENTRIES},
                "categories": [],
                "page": 1,
                "pages": 1,
                "total": 0,
            }
            if view == "mistakes":
                rows = filter_mistake_entries(q)
                total = len(rows)
                page_size = 40
                pages = max(1, math.ceil(total / page_size))
                page = min(page, pages)
                start = (page - 1) * page_size
                payload.update(
                    {
                        "mistakes": rows[start : start + page_size],
                        "total": total,
                        "page": page,
                        "pages": pages,
                    }
                )
            else:
                categories = [{"name": name, "count": count} for name, count in core_categories()]
                payload["categories"] = categories
                payload["summary"]["total"] = sum(row["count"] for row in categories)
            if view == "library":
                rows = filter_core_entries(query=q, category=category, status=status, progress=progress)
                total = len(rows)
                pages = max(1, math.ceil(total / page_size))
                page = min(page, pages)
                start = (page - 1) * page_size
                payload.update(
                    {
                        "entries": [_entry_payload(row, progress) for row in rows[start : start + page_size]],
                        "total": total,
                        "page": page,
                        "pages": pages,
                    }
                )
            elif view == "review":
                rows = review_queue(progress=progress, query=q, category=category)
                total = len(rows)
                page_size = 28
                pages = max(1, math.ceil(total / page_size))
                page = min(page, pages)
                start = (page - 1) * page_size
                payload.update(
                    {
                        "entries": [_entry_payload(row, progress) for row in rows[start : start + page_size]],
                        "total": total,
                        "page": page,
                        "pages": pages,
                    }
                )
            elif view == "contrast":
                groups = comparison_groups()
                if category:
                    groups = [group for group in groups if group.get("category") == category]
                if q:
                    lowered = q.lower()
                    groups = [
                        group
                        for group in groups
                        if lowered in (
                            f"{group.get('category', '')} {group.get('subcategory', '')} "
                            + " ".join(f"{entry.get('term', '')} {entry.get('definition', '')}" for entry in group.get("entries", []))
                        ).lower()
                    ]
                total = len(groups)
                page_size = 16
                pages = max(1, math.ceil(total / page_size))
                page = min(page, pages)
                start = (page - 1) * page_size
                payload.update(
                    {
                        "groups": [
                            {
                                "key": group["key"],
                                "category": group["category"],
                                "subcategory": group["subcategory"],
                                "common_ground": group.get("common_ground") or "",
                                "comparison_tip": group.get("comparison_tip") or "",
                                "entries": [_contrast_entry_payload(row, progress) for row in group["entries"]],
                            }
                            for group in groups[start : start + page_size]
                        ],
                        "total": total,
                        "page": page,
                        "pages": pages,
                    }
                )
            self.send_json(payload)
        except Exception:
            self.send_json(
                {
                    "ok": False,
                    "error": "成语词库数据当前无法读取。页面与学习记录接口已经就绪，词库数据恢复后会自动正常显示。",
                    "error_code": "corpus_unavailable",
                },
                status=503,
            )

    def page_idiom_quiz_data(self, query=None):
        query = query or {}
        kind = query.get("kind", ["meaning"])[0]
        if kind not in QUIZ_KINDS:
            kind = "meaning"
        count = _bounded_int(query.get("count", ["10"])[0], 10, 1, 30)
        seed = _bounded_int(query.get("seed", ["0"])[0], 0, 0, 2_147_483_647)
        try:
            questions = build_quiz(kind=kind, count=count, seed=seed)
        except Exception:
            self.send_json({"ok": False, "error": "成语题库暂时无法读取，请稍后重试。"}, status=503)
            return
        self.send_json({"ok": True, "kind": kind, "questions": questions})

    def handle_idiom_progress(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "请求 JSON 无法解析"}, status=400)
            return
        if not isinstance(payload, dict):
            self.send_json({"ok": False, "error": "请求体必须是 JSON 对象"}, status=400)
            return
        entry_id = str(payload.get("entry_id") or "").strip()
        if not entry_id:
            self.send_json({"ok": False, "error": "缺少成语条目编号"}, status=400)
            return
        status = payload.get("status")
        is_starred = payload.get("is_starred") if "is_starred" in payload else None
        quiz_result = payload.get("quiz_result")
        try:
            with connect(self.db_path) as conn:
                state = update_progress(
                    conn,
                    entry_id,
                    status=status,
                    is_starred=is_starred,
                    quiz_result=quiz_result,
                )
                progress = load_progress(conn)
                summary = progress_summary(progress)
                summary["review_due"] = _review_due_count(progress)
        except ValueError:
            self.send_json({"ok": False, "error": "成语条目不存在"}, status=404)
            return
        except Exception:
            self.send_json({"ok": False, "error": "学习记录暂时无法保存"}, status=503)
            return
        self.send_json({"ok": True, "entry_id": entry_id, "progress": state, "summary": summary})
