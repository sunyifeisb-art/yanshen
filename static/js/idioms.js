const VIEW_COPY = {
  library: {
    eyebrow: "CORE LIBRARY",
    title: "核心词库",
    note: "按释义、分类和掌握状态筛选；收藏的词可以单独回炉。",
  },
  review: {
    eyebrow: "SMART REVIEW",
    title: "今日回炉",
    note: "按答错次数、正确率、学习状态和收藏自动排序；优先处理最容易再次出错的词。",
  },
  contrast: {
    eyebrow: "CONTRAST STUDY",
    title: "成语辨析",
    note: "覆盖全部相近成语分组，逐词比较共同语义、核心侧重、适用对象/语境和易错边界。",
  },
  mistakes: {
    eyebrow: "MISTAKE COLLECTION",
    title: "错题积累",
    note: "独立收录《言语理解.pdf》中的全部词汇；不并入700核心词库，也不参与核心词库掌握率。",
  },
};

const QUIZ_KIND_LABELS = {
  meaning: "释义辨认",
  context: "语境选词",
  contrast: "易混辨析",
  usage: "使用判断",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.error || "请求失败，请重试");
  }
  return payload;
}

function statusClass(status) {
  if (status === "mastered") return "mastered";
  if (status === "learning") return "learning";
  return "unseen";
}

function progressHint(entry) {
  const reviewed = Number(entry.correct_count || 0) + Number(entry.wrong_count || 0);
  if (!reviewed) return "尚未测验";
  return `${reviewed} 次测验 · ${entry.accuracy ?? 0}% 正确`;
}

function fullMeaning(entry) {
  return String(entry?.explanation || entry?.definition || "").trim();
}

function detailStudyNotes(definition) {
  const text = String(definition || "").replace(/\s+/g, " ").trim();
  if (!text) return { contrast: [], examples: [] };
  const fragments = text
    .split(/(?<=[。！？；])\s*|\s+(?=(?:eg|例|辨析|区别|侧重|语境|适用|用于|多指|强调|不能|只用于)[:：]?)/i)
    .map((item) => item.trim())
    .filter(Boolean);
  const examples = fragments.filter((item) => /(?:\beg\b|例如|比如|例[:：])/i.test(item)).slice(0, 3);
  const contrast = fragments.filter((item) => /辨析|区别|侧重|语境|适用|用于|多指|强调|不能|只用于|对象/.test(item)).slice(0, 4);
  return { contrast, examples };
}

function detailCardHtml(entry, related = [], relatedState = "loading", navigation = {}) {
  const notes = detailStudyNotes(entry.definition);
  const state = statusClass(entry.status);
  const meaning = fullMeaning(entry) || "暂无释义";
  const sourceDefinition = String(entry.definition || "").trim();
  const hasSeparateSourceNote = Boolean(sourceDefinition && sourceDefinition !== meaning);
  const relatedRows = related.filter((item) => item.id !== entry.id).slice(0, 6);
  const relatedHtml = relatedState === "loading"
    ? '<div class="idiom-detail-related-state">正在匹配同组易混词…</div>'
    : relatedRows.length
      ? relatedRows.map((item) => `
          <button type="button" class="idiom-detail-related-item" data-idiom-related-entry="${escapeHtml(item.id)}">
            <span><strong>${escapeHtml(item.term)}</strong><small>${escapeHtml(item.status_label || "未学习")}</small></span>
            <p>${escapeHtml(fullMeaning(item))}</p>
          </button>`).join("")
      : '<div class="idiom-detail-related-state">当前资料未单列同组易混词。</div>';
  const noteSections = [
    hasSeparateSourceNote ? `
      <section class="idiom-detail-note-card idiom-detail-source-note">
        <span>备考资料速记</span>
        <p>${escapeHtml(sourceDefinition)}</p>
      </section>` : "",
    notes.contrast.length ? `
      <section class="idiom-detail-note-card">
        <span>辨析与使用边界</span>
        ${notes.contrast.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}
      </section>` : "",
    (entry.example || notes.examples.length) ? `
      <section class="idiom-detail-note-card">
        <span>例句 / 语境</span>
        ${entry.example ? `<p>${escapeHtml(entry.example)}</p>` : ""}
        ${notes.examples.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}
      </section>` : "",
    entry.derivation ? `
      <section class="idiom-detail-note-card">
        <span>出处 / 来源</span>
        <p>${escapeHtml(entry.derivation)}</p>
      </section>` : "",
  ].join("");
  return `
    <article class="idiom-detail-card" data-idiom-detail-entry="${escapeHtml(entry.id)}">
      <div class="idiom-detail-hero">
        <div>
          <div class="idiom-entry-kicker"><span>${escapeHtml(entry.category)}</span>${entry.subcategory ? `<i>${escapeHtml(entry.subcategory)}</i>` : ""}</div>
          <h3>${escapeHtml(entry.term)}</h3>
          ${entry.pinyin ? `<p class="idiom-detail-pinyin">${escapeHtml(entry.pinyin)}</p>` : ""}
          <p>完整学习卡 · 点击相关词可继续展开</p>
        </div>
        <div class="idiom-detail-statuses">
          <span class="idiom-status ${state}">${escapeHtml(entry.status_label || "未学习")}</span>
          <span>${escapeHtml(progressHint(entry))}</span>
        </div>
      </div>

      <section class="idiom-detail-definition">
        <div class="idiom-detail-definition-head"><span>完整释义</span><button type="button" data-idiom-detail-copy="meaning">复制释义</button></div>
        <p>${escapeHtml(meaning)}</p>
      </section>

      ${noteSections ? `<div class="idiom-detail-note-grid">${noteSections}</div>` : ""}

      <section class="idiom-detail-related">
        <div class="idiom-detail-section-head"><div><span>同组联想</span><strong>易混 / 相近成语</strong></div><small>来自现有 700 词库分组</small></div>
        <div class="idiom-detail-related-list" data-idiom-detail-related>${relatedHtml}</div>
      </section>

      <footer class="idiom-detail-actions">
        <div class="idiom-detail-nav">
          <button type="button" data-idiom-detail-prev${navigation.prev ? "" : " disabled"}>← 上一个</button>
          <button type="button" data-idiom-detail-next${navigation.next ? "" : " disabled"}>下一个 →</button>
        </div>
        <div class="idiom-detail-learning-actions">
          <button type="button" class="idiom-detail-star${entry.is_starred ? " is-starred" : ""}" data-idiom-detail-star aria-pressed="${entry.is_starred ? "true" : "false"}">${entry.is_starred ? "★ 已收藏" : "☆ 收藏"}</button>
          <button type="button" data-idiom-detail-status="learning" class="${entry.status === "learning" ? "is-active" : ""}">学习中</button>
          <button type="button" data-idiom-detail-status="mastered" class="${entry.status === "mastered" ? "is-active" : ""}">已掌握</button>
        </div>
        <div class="idiom-detail-primary-actions">
          ${(entry.example || notes.examples.length) ? '<button type="button" class="button ghost" data-idiom-detail-copy="example">复制例句</button>' : ""}
          <button type="button" class="button ghost" data-idiom-detail-contrast>去易混辨析</button>
          <button type="button" class="button primary" data-idiom-detail-quiz>用小测巩固</button>
        </div>
      </footer>
    </article>`;
}

function reviewEntryCardHtml(entry) {
  return entryCardHtml(entry).replace(
    '<div class="idiom-entry-open-hint">',
    `<div class="idiom-review-reason"><span>回炉原因</span><strong>${escapeHtml(entry.review_reason || "学习中")}</strong></div><div class="idiom-entry-open-hint">`,
  );
}

function entryCardHtml(entry) {
  const state = statusClass(entry.status);
  const meaning = fullMeaning(entry);
  const sourceDefinition = String(entry.definition || "").trim();
  const hasShortcut = Boolean(entry.explanation && sourceDefinition && sourceDefinition !== meaning);
  return `
    <article class="idiom-entry-card" data-idiom-entry="${escapeHtml(entry.id)}" tabindex="0" aria-label="查看${escapeHtml(entry.term)}学习卡片">
      <header class="idiom-entry-head">
        <div>
          <div class="idiom-entry-kicker"><span>${escapeHtml(entry.category)}</span>${entry.subcategory ? `<i>${escapeHtml(entry.subcategory)}</i>` : ""}</div>
          <h3>${escapeHtml(entry.term)}</h3>
        </div>
        <button class="idiom-star-button${entry.is_starred ? " is-starred" : ""}" type="button" data-idiom-star aria-pressed="${entry.is_starred ? "true" : "false"}" title="${entry.is_starred ? "取消收藏" : "收藏"}">★</button>
      </header>
      <div class="idiom-entry-copy">
        <p class="idiom-definition">${escapeHtml(meaning)}</p>
        ${hasShortcut ? `<p class="idiom-source-shortcut"><span>速记</span>${escapeHtml(sourceDefinition)}</p>` : ""}
      </div>
      <div class="idiom-entry-open-hint"><span>查看完整释义与辨析</span><b aria-hidden="true">→</b></div>
      <footer class="idiom-entry-footer">
        <div class="idiom-entry-progress"><span class="idiom-status ${state}">${escapeHtml(entry.status_label)}</span><small>${escapeHtml(progressHint(entry))}</small></div>
        <div class="idiom-state-actions" aria-label="学习状态">
          <button type="button" data-idiom-status="learning" class="${entry.status === "learning" ? "is-active" : ""}">学习中</button>
          <button type="button" data-idiom-status="mastered" class="${entry.status === "mastered" ? "is-active" : ""}">已掌握</button>
        </div>
      </footer>
    </article>`;
}

function contrastGroupHtml(group) {
  const entries = Array.isArray(group.entries) ? group.entries : [];
  const quick = entries.map((entry) => {
    const analysis = entry.usage_analysis || {};
    const focus = analysis.focus || detailStudyNotes(entry.definition).contrast[0] || entry.definition || fullMeaning(entry);
    return `<span><strong>${escapeHtml(entry.term)}</strong>${escapeHtml(focus)}</span>`;
  }).join("");
  return `
    <details class="idiom-contrast-group" open>
      <summary>
        <div class="idiom-contrast-summary-title"><span>${escapeHtml(group.category)}</span><h3>${escapeHtml(group.subcategory || "同组辨析")}</h3></div>
        <div class="idiom-contrast-summary-meta"><small>${entries.length} 个相近词</small><b aria-hidden="true">⌄</b></div>
      </summary>
      <div class="idiom-contrast-overview">
        <div><span>共同语义</span><p>${escapeHtml(group.common_ground || `这一组都与“${group.subcategory || group.category}”相关。`)}</p></div>
        <div><span>辨析抓手</span><p>${escapeHtml(group.comparison_tip || "重点比较语义侧重、适用对象/语境和使用边界。")}</p></div>
      </div>
      <div class="idiom-contrast-quick"><span>一眼区分</span><div>${quick}</div></div>
      <div class="idiom-contrast-entries">
        ${entries.map((entry) => {
          const analysis = entry.usage_analysis || {};
          const notes = detailStudyNotes(entry.definition);
          const focus = analysis.focus || notes.contrast[0] || entry.definition || fullMeaning(entry);
          const usage = analysis.usage || entry.definition || fullMeaning(entry);
          const warning = analysis.warning || "按本组其他词的侧重、对象和语境排除；资料未单列额外禁用条件。";
          const tone = analysis.tone ? `<span class="idiom-contrast-tone">${escapeHtml(analysis.tone)}</span>` : "";
          const example = analysis.example || entry.example || "";
          return `
            <section class="idiom-contrast-entry" data-idiom-entry="${escapeHtml(entry.id)}" tabindex="0" aria-label="查看${escapeHtml(entry.term)}学习卡片">
              <div class="idiom-contrast-term"><div><strong>${escapeHtml(entry.term)}</strong>${tone}</div><span class="idiom-status ${statusClass(entry.status)}">${escapeHtml(entry.status_label)}</span></div>
              <div class="idiom-contrast-block"><span>核心释义</span><p>${escapeHtml(fullMeaning(entry))}</p></div>
              <div class="idiom-contrast-block is-focus"><span>核心侧重</span><p>${escapeHtml(focus)}</p></div>
              <div class="idiom-contrast-block"><span>适用对象 / 语境</span><p>${escapeHtml(usage)}</p></div>
              <div class="idiom-contrast-block is-warning"><span>易错边界</span><p>${escapeHtml(warning)}</p></div>
              ${example ? `<div class="idiom-contrast-example"><span>例句</span><p>${escapeHtml(example)}</p></div>` : ""}
              <div class="idiom-contrast-open"><span>打开完整学习卡</span><b aria-hidden="true">→</b></div>
            </section>`;
        }).join("")}
      </div>
    </details>`;
}

function mistakeCardHtml(entry, index) {
  const term = entry.term || entry.title || `词汇 ${index + 1}`;
  const note = entry.note || entry.definition || entry.content || "";
  return `
    <article class="idiom-misuse-card">
      <div><span>${String(index + 1).padStart(2, "0")}</span><h3>${escapeHtml(term)}</h3></div>
      <p>${escapeHtml(note)}</p>
    </article>`;
}

function emptyHtml(view) {
  const copy = view === "contrast" ? "没有匹配的辨析组" : view === "mistakes" ? "没有匹配的错题积累词汇" : "没有匹配的成语";
  return `<div class="idiom-empty"><strong>${copy}</strong><p>换一个关键词或清空筛选条件再看。</p></div>`;
}

function initializeLibrary(root, signal) {
  const list = root.querySelector("[data-idiom-list]");
  const loading = root.querySelector("[data-idiom-loading]");
  const error = root.querySelector("[data-idiom-error]");
  const errorMessage = root.querySelector("[data-idiom-error-message]");
  const resultCount = root.querySelector("[data-idiom-result-count]");
  const pagination = root.querySelector("[data-idiom-pagination]");
  const pageLabel = root.querySelector("[data-idiom-page-label]");
  const prev = root.querySelector("[data-idiom-page-prev]");
  const next = root.querySelector("[data-idiom-page-next]");
  const form = root.querySelector("[data-idiom-filters]");
  const categorySelect = form?.querySelector('[name="category"]');
  const statusSelect = form?.querySelector('[name="status"]');
  const search = form?.querySelector('[name="q"]');
  const libraryOnly = root.querySelectorAll("[data-idiom-library-only]");
  const coreOnly = root.querySelectorAll("[data-idiom-core-only]");
  const filterChips = root.querySelector("[data-idiom-filter-chips]");
  const contentEyebrow = root.querySelector("[data-idiom-content-eyebrow]");
  const contentTitle = root.querySelector("[data-idiom-content-title]");
  const contentNote = root.querySelector("[data-idiom-content-note]");
  const detailLayer = root.querySelector("[data-idiom-detail-layer]");
  const detailBody = root.querySelector("[data-idiom-detail-body]");
  const detailTitle = root.querySelector("[data-idiom-detail-title]");
  const state = {
    view: "library",
    page: 1,
    pages: 1,
    loading: false,
    categoriesLoaded: false,
    entryCache: new Map(),
    detailEntry: null,
    detailRelated: [],
    detailRequest: 0,
    detailReturnFocus: null,
    visibleEntryIds: [],
    searchTimer: null,
  };

  function updateSummary(summary = {}) {
    const values = {
      total: summary.total ?? 700,
      mastered: summary.mastered ?? 0,
      learning: summary.learning ?? 0,
      review_due: summary.review_due ?? 0,
      accuracy: summary.reviewed ? `${summary.accuracy ?? 0}%` : "—",
    };
    Object.entries(values).forEach(([key, value]) => {
      const node = root.querySelector(`[data-idiom-metric="${key}"]`);
      if (node) node.textContent = String(value);
    });
    const reviewed = root.querySelector("[data-idiom-reviewed]");
    if (reviewed) reviewed.textContent = summary.reviewed ? `累计 ${summary.reviewed} 次答题` : "暂无答题记录";
  }

  function updateCategories(categories = []) {
    if (!categorySelect || state.categoriesLoaded) return;
    categorySelect.innerHTML = '<option value="">全部分类</option>' + categories.map((item) => (
      `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)} · ${Number(item.count || 0)}</option>`
    )).join("");
    state.categoriesLoaded = true;
  }

  function updateViewChrome() {
    const copy = VIEW_COPY[state.view] || VIEW_COPY.library;
    if (contentEyebrow) contentEyebrow.textContent = copy.eyebrow;
    if (contentTitle) contentTitle.textContent = copy.title;
    if (contentNote) contentNote.textContent = copy.note;
    libraryOnly.forEach((node) => { node.hidden = state.view !== "library"; });
    coreOnly.forEach((node) => { node.hidden = !["library", "review", "contrast"].includes(state.view); });
    root.querySelectorAll("[data-idiom-view]").forEach((button) => {
      const active = button.dataset.idiomView === state.view;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    });
  }

  function updateFilterChips() {
    if (!filterChips) return;
    const chips = [];
    if (search?.value.trim()) chips.push(["关键词", search.value.trim(), "q"]);
    if (["library", "review", "contrast"].includes(state.view) && categorySelect?.value) chips.push(["分类", categorySelect.value, "category"]);
    if (state.view === "library" && statusSelect?.value) chips.push(["状态", statusSelect.options[statusSelect.selectedIndex]?.textContent || statusSelect.value, "status"]);
    filterChips.hidden = chips.length === 0;
    filterChips.innerHTML = chips.map(([label, value, key]) => (
      `<button type="button" data-idiom-filter-chip="${escapeHtml(key)}"><span>${escapeHtml(label)}</span>${escapeHtml(value)} <b aria-hidden="true">×</b></button>`
    )).join("");
  }

  function showLoading() {
    state.loading = true;
    if (loading) loading.hidden = false;
    if (error) error.hidden = true;
    if (list) list.innerHTML = "";
    if (pagination) pagination.hidden = true;
    if (resultCount) resultCount.textContent = "正在读取…";
  }

  function showError(message) {
    state.loading = false;
    if (loading) loading.hidden = true;
    if (error) error.hidden = false;
    if (errorMessage) errorMessage.textContent = message || "请稍后重试。";
    if (resultCount) resultCount.textContent = "读取失败";
  }

  function renderPayload(payload) {
    state.loading = false;
    if (loading) loading.hidden = true;
    if (error) error.hidden = true;
    updateSummary(payload.summary || {});
    updateCategories(payload.categories || []);
    const total = Number(payload.total || 0);
    if (resultCount) resultCount.textContent = state.view === "contrast" ? `${total} 组` : `${total} 项`;
    if (!list) return;
    if (state.view === "library") {
      const entries = Array.isArray(payload.entries) ? payload.entries : [];
      entries.forEach((entry) => state.entryCache.set(entry.id, entry));
      state.visibleEntryIds = entries.map((entry) => entry.id);
      list.className = "idiom-list idiom-entry-list";
      list.innerHTML = entries.length ? entries.map(entryCardHtml).join("") : emptyHtml(state.view);
      state.page = Number(payload.page || 1);
      state.pages = Number(payload.pages || 1);
      if (pagination) pagination.hidden = state.pages <= 1;
      if (pageLabel) pageLabel.textContent = `${state.page} / ${state.pages}`;
      if (prev) prev.disabled = state.page <= 1;
      if (next) next.disabled = state.page >= state.pages;
      return;
    }
    if (state.view === "review") {
      const entries = Array.isArray(payload.entries) ? payload.entries : [];
      entries.forEach((entry) => state.entryCache.set(entry.id, entry));
      state.visibleEntryIds = entries.map((entry) => entry.id);
      list.className = "idiom-list idiom-entry-list idiom-review-list";
      list.innerHTML = entries.length ? entries.map(reviewEntryCardHtml).join("") : emptyHtml(state.view);
    } else if (state.view === "contrast") {
      const groups = Array.isArray(payload.groups) ? payload.groups : [];
      groups.forEach((group) => {
        (group.entries || []).forEach((entry) => state.entryCache.set(entry.id, entry));
      });
      state.visibleEntryIds = groups.flatMap((group) => (group.entries || []).map((entry) => entry.id));
      list.className = "idiom-list idiom-contrast-list";
      list.innerHTML = groups.length ? groups.map(contrastGroupHtml).join("") : emptyHtml(state.view);
    } else {
      const mistakes = Array.isArray(payload.mistakes) ? payload.mistakes : [];
      state.visibleEntryIds = [];
      list.className = "idiom-list idiom-misuse-list";
      list.innerHTML = mistakes.length ? mistakes.map(mistakeCardHtml).join("") : emptyHtml(state.view);
    }
    state.page = Number(payload.page || 1);
    state.pages = Number(payload.pages || 1);
    if (pagination) pagination.hidden = state.pages <= 1;
    if (pageLabel) pageLabel.textContent = `${state.page} / ${state.pages}`;
    if (prev) prev.disabled = state.page <= 1;
    if (next) next.disabled = state.page >= state.pages;
  }

  async function loadData() {
    if (state.loading) return;
    updateViewChrome();
    showLoading();
    const params = new URLSearchParams({ view: state.view, page: String(state.page) });
    if (search?.value.trim()) params.set("q", search.value.trim());
    if (["library", "review", "contrast"].includes(state.view) && categorySelect?.value) params.set("category", categorySelect.value);
    if (state.view === "library" && statusSelect?.value) params.set("status", statusSelect.value);
    updateFilterChips();
    try {
      const payload = await fetchJson(`/idioms/data?${params.toString()}`, { signal });
      renderPayload(payload);
    } catch (fetchError) {
      if (fetchError?.name !== "AbortError") showError(fetchError?.message || "词库读取失败");
    }
  }

  function detailNavigation(entryId) {
    const index = state.visibleEntryIds.indexOf(entryId);
    return {
      prev: index > 0 ? state.visibleEntryIds[index - 1] : null,
      next: index >= 0 && index < state.visibleEntryIds.length - 1 ? state.visibleEntryIds[index + 1] : null,
    };
  }

  async function updateEntry(entryId, patch, trigger) {
    if (!entryId || trigger?.disabled) return;
    if (trigger) trigger.disabled = true;
    try {
      const payload = await fetchJson("/idioms/progress", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entry_id: entryId, ...patch }),
        signal,
      });
      updateSummary(payload.summary || {});
      state.loading = false;
      await loadData();
      return payload;
    } catch (saveError) {
      if (saveError?.name !== "AbortError") showError(saveError?.message || "学习记录保存失败");
      return null;
    } finally {
      if (trigger?.isConnected) trigger.disabled = false;
    }
  }

  function closeDetail({ restoreFocus = true } = {}) {
    if (!detailLayer) return;
    state.detailRequest += 1;
    detailLayer.hidden = true;
    state.detailEntry = null;
    state.detailRelated = [];
    document.body.classList.remove("idiom-modal-open");
    if (restoreFocus && state.detailReturnFocus?.isConnected) state.detailReturnFocus.focus();
    state.detailReturnFocus = null;
  }

  function renderDetail(relatedState = "ready") {
    if (!state.detailEntry || !detailBody) return;
    if (detailTitle) detailTitle.textContent = `${state.detailEntry.term} · 学习卡`;
    detailBody.innerHTML = detailCardHtml(state.detailEntry, state.detailRelated, relatedState, detailNavigation(state.detailEntry.id));
  }

  async function loadRelated(entry, requestId) {
    try {
      const params = new URLSearchParams({ view: "contrast", q: entry.term, page: "1" });
      const payload = await fetchJson(`/idioms/data?${params.toString()}`, { signal });
      if (requestId !== state.detailRequest || state.detailEntry?.id !== entry.id) return;
      const groups = Array.isArray(payload.groups) ? payload.groups : [];
      const matched = groups.find((group) => (group.entries || []).some((item) => item.id === entry.id));
      state.detailRelated = matched?.entries || [];
      state.detailRelated.forEach((item) => state.entryCache.set(item.id, item));
      renderDetail("ready");
    } catch (relatedError) {
      if (relatedError?.name === "AbortError" || requestId !== state.detailRequest) return;
      state.detailRelated = [];
      renderDetail("ready");
    }
  }

  function openDetail(entry, trigger = null) {
    if (!detailLayer || !detailBody || !entry) return;
    state.detailEntry = entry;
    state.detailRelated = [];
    state.detailReturnFocus = trigger || document.activeElement;
    state.detailRequest += 1;
    const requestId = state.detailRequest;
    renderDetail("loading");
    detailLayer.hidden = false;
    document.body.classList.add("idiom-modal-open");
    root.querySelector("[data-idiom-detail-close]")?.focus();
    void loadRelated(entry, requestId);
  }

  async function updateDetailEntry(patch, trigger) {
    const current = state.detailEntry;
    if (!current) return;
    const payload = await updateEntry(current.id, patch, trigger);
    if (!payload || !state.detailEntry || state.detailEntry.id !== current.id) return;
    const fresh = state.entryCache.get(current.id) || current;
    state.detailEntry = {
      ...fresh,
      status: payload.progress?.status || fresh.status,
      status_label: payload.progress?.status === "mastered" ? "已掌握" : payload.progress?.status === "learning" ? "学习中" : "未学习",
      is_starred: Boolean(payload.progress?.is_starred),
      correct_count: Number(payload.progress?.correct_count || 0),
      wrong_count: Number(payload.progress?.wrong_count || 0),
      accuracy: Number(payload.progress?.correct_count || 0) + Number(payload.progress?.wrong_count || 0)
        ? Math.round(Number(payload.progress?.correct_count || 0) * 100 / (Number(payload.progress?.correct_count || 0) + Number(payload.progress?.wrong_count || 0)))
        : null,
    };
    state.entryCache.set(current.id, state.detailEntry);
    renderDetail("ready");
  }

  root.querySelectorAll("[data-idiom-view]").forEach((button) => {
    button.addEventListener("click", () => {
      const nextView = button.dataset.idiomView;
      if (!VIEW_COPY[nextView] || nextView === state.view) return;
      state.view = nextView;
      state.page = 1;
      state.loading = false;
      void loadData();
    }, { signal });
  });

  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    state.page = 1;
    state.loading = false;
    void loadData();
  }, { signal });

  search?.addEventListener("input", () => {
    if (state.searchTimer) window.clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(() => {
      state.page = 1;
      state.loading = false;
      void loadData();
    }, 280);
  }, { signal });

  filterChips?.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-idiom-filter-chip]");
    if (!chip) return;
    const key = chip.dataset.idiomFilterChip;
    if (key === "q" && search) search.value = "";
    if (key === "category" && categorySelect) categorySelect.value = "";
    if (key === "status" && statusSelect) statusSelect.value = "";
    state.page = 1;
    state.loading = false;
    void loadData();
  }, { signal });

  root.querySelector("[data-idiom-filter-reset]")?.addEventListener("click", () => {
    form?.reset();
    state.page = 1;
    state.loading = false;
    void loadData();
  }, { signal });

  root.querySelector("[data-idiom-retry]")?.addEventListener("click", () => {
    state.loading = false;
    void loadData();
  }, { signal });

  prev?.addEventListener("click", () => {
    if (state.page <= 1) return;
    state.page -= 1;
    state.loading = false;
    void loadData();
  }, { signal });
  next?.addEventListener("click", () => {
    if (state.page >= state.pages) return;
    state.page += 1;
    state.loading = false;
    void loadData();
  }, { signal });

  list?.addEventListener("click", (event) => {
    const card = event.target.closest("[data-idiom-entry]");
    if (!card) return;
    const entryId = card.dataset.idiomEntry;
    const star = event.target.closest("[data-idiom-star]");
    if (star) {
      void updateEntry(entryId, { is_starred: star.getAttribute("aria-pressed") !== "true" }, star);
      return;
    }
    const status = event.target.closest("[data-idiom-status]");
    if (status) {
      void updateEntry(entryId, { status: status.dataset.idiomStatus }, status);
      return;
    }
    openDetail(state.entryCache.get(entryId), card);
  }, { signal });

  list?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const card = event.target.closest("[data-idiom-entry]");
    if (!card || event.target !== card) return;
    event.preventDefault();
    openDetail(state.entryCache.get(card.dataset.idiomEntry), card);
  }, { signal });

  detailLayer?.addEventListener("click", (event) => {
    if (event.target === detailLayer || event.target.closest("[data-idiom-detail-close]")) {
      closeDetail();
      return;
    }
    const related = event.target.closest("[data-idiom-related-entry]");
    if (related) {
      const entry = state.entryCache.get(related.dataset.idiomRelatedEntry);
      if (entry) openDetail(entry, state.detailReturnFocus);
      return;
    }
    const star = event.target.closest("[data-idiom-detail-star]");
    if (star) {
      void updateDetailEntry({ is_starred: star.getAttribute("aria-pressed") !== "true" }, star);
      return;
    }
    const status = event.target.closest("[data-idiom-detail-status]");
    if (status) {
      void updateDetailEntry({ status: status.dataset.idiomDetailStatus }, status);
      return;
    }
    const copyButton = event.target.closest("[data-idiom-detail-copy]");
    if (copyButton && state.detailEntry) {
      const notes = detailStudyNotes(state.detailEntry.definition);
      const text = copyButton.dataset.idiomDetailCopy === "example"
        ? String(state.detailEntry.example || notes.examples[0] || "").trim()
        : fullMeaning(state.detailEntry);
      if (text && navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(text).then(() => {
          const original = copyButton.textContent;
          copyButton.textContent = "已复制";
          window.setTimeout(() => { if (copyButton.isConnected) copyButton.textContent = original; }, 1100);
        }).catch(() => {});
      }
      return;
    }
    const navButton = event.target.closest("[data-idiom-detail-prev], [data-idiom-detail-next]");
    if (navButton && state.detailEntry) {
      const nav = detailNavigation(state.detailEntry.id);
      const targetId = navButton.matches("[data-idiom-detail-prev]") ? nav.prev : nav.next;
      const targetEntry = targetId ? state.entryCache.get(targetId) : null;
      if (targetEntry) openDetail(targetEntry, state.detailReturnFocus);
      return;
    }
    if (event.target.closest("[data-idiom-detail-contrast]")) {
      const term = state.detailEntry?.term || "";
      closeDetail({ restoreFocus: false });
      state.view = "contrast";
      state.page = 1;
      state.loading = false;
      if (search) search.value = term;
      void loadData();
      return;
    }
    if (event.target.closest("[data-idiom-detail-quiz]")) {
      closeDetail({ restoreFocus: false });
      root.querySelector("[data-idiom-quiz-open]")?.click();
    }
  }, { signal });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && detailLayer && !detailLayer.hidden) closeDetail();
  }, { signal });

  signal.addEventListener("abort", () => {
    if (state.searchTimer) window.clearTimeout(state.searchTimer);
    if (detailLayer) detailLayer.hidden = true;
    document.body.classList.remove("idiom-modal-open");
  }, { once: true });

  void loadData();
  return {
    loadData,
    updateSummary,
    showView(view) {
      if (!VIEW_COPY[view]) return;
      state.view = view;
      state.page = 1;
      state.loading = false;
      void loadData();
    },
  };
}

function initializeFlashcards(root, signal, library) {
  const layer = root.querySelector("[data-idiom-flashcard-layer]");
  const stage = root.querySelector("[data-idiom-flashcard-stage]");
  const progress = root.querySelector("[data-idiom-flashcard-progress]");
  const progressFill = root.querySelector("[data-idiom-flashcard-progress-fill]");
  const prev = root.querySelector("[data-idiom-flashcard-prev]");
  const next = root.querySelector("[data-idiom-flashcard-next]");
  const learning = root.querySelector("[data-idiom-flashcard-learning]");
  const mastered = root.querySelector("[data-idiom-flashcard-mastered]");
  const state = { entries: [], index: 0, flipped: false, loading: false };

  function current() {
    return state.entries[state.index] || null;
  }

  function render() {
    const entry = current();
    if (progress) progress.textContent = entry ? `${state.index + 1} / ${state.entries.length} · ${entry.review_reason || "回炉"}` : "暂无可复习词汇";
    if (progressFill) progressFill.style.width = entry && state.entries.length ? `${Math.round((state.index + 1) * 100 / state.entries.length)}%` : "0%";
    if (prev) prev.disabled = state.index <= 0;
    if (next) next.disabled = !entry || state.index >= state.entries.length - 1;
    if (learning) learning.disabled = !entry;
    if (mastered) mastered.disabled = !entry;
    if (!stage) return;
    if (!entry) {
      stage.innerHTML = '<div class="idiom-flashcard-empty"><strong>当前没有待回炉词汇</strong><p>先在词库里标记“学习中”、收藏词汇或完成一组成语小测。</p></div>';
      return;
    }
    const notes = detailStudyNotes(entry.definition);
    const extra = entry.example || notes.examples[0] || "";
    stage.innerHTML = `
      <button class="idiom-flashcard-card${state.flipped ? " is-flipped" : ""}" type="button" data-idiom-flashcard-flip aria-label="翻转${escapeHtml(entry.term)}闪卡">
        <span class="idiom-flashcard-face idiom-flashcard-front">
          <small>${escapeHtml(entry.category)}${entry.subcategory ? ` · ${escapeHtml(entry.subcategory)}` : ""}</small>
          <strong>${escapeHtml(entry.term)}</strong>
          ${entry.pinyin ? `<em>${escapeHtml(entry.pinyin)}</em>` : ""}
          <i>点击或按空格查看释义</i>
        </span>
        <span class="idiom-flashcard-face idiom-flashcard-back">
          <small>完整释义</small>
          <strong>${escapeHtml(entry.term)}</strong>
          <p>${escapeHtml(fullMeaning(entry) || "暂无释义")}</p>
          ${extra ? `<blockquote>${escapeHtml(extra)}</blockquote>` : ""}
          <i>空格翻回正面</i>
        </span>
      </button>`;
  }

  function move(delta) {
    const target = state.index + delta;
    if (target < 0 || target >= state.entries.length) return;
    state.index = target;
    state.flipped = false;
    render();
  }

  async function saveStatus(status) {
    const entry = current();
    if (!entry) return;
    if (learning) learning.disabled = true;
    if (mastered) mastered.disabled = true;
    try {
      const payload = await fetchJson("/idioms/progress", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entry_id: entry.id, status }),
        signal,
      });
      entry.status = payload.progress?.status || status;
      entry.status_label = entry.status === "mastered" ? "已掌握" : entry.status === "learning" ? "学习中" : "未学习";
      library?.updateSummary(payload.summary || {});
      if (status === "mastered" && state.index < state.entries.length - 1) move(1);
      else render();
    } catch (saveError) {
      if (saveError?.name !== "AbortError" && progress) progress.textContent = saveError?.message || "学习状态保存失败";
    } finally {
      if (learning?.isConnected) learning.disabled = false;
      if (mastered?.isConnected) mastered.disabled = false;
    }
  }

  async function loadDeck() {
    if (state.loading) return;
    state.loading = true;
    if (stage) stage.innerHTML = '<div class="idiom-flashcard-empty"><i class="idiom-flashcard-spinner"></i><p>正在按薄弱程度整理回炉词…</p></div>';
    if (progress) progress.textContent = "正在准备…";
    try {
      const payload = await fetchJson("/idioms/data?view=review&page=1", { signal });
      state.entries = Array.isArray(payload.entries) ? payload.entries : [];
      state.index = 0;
      state.flipped = false;
      library?.updateSummary(payload.summary || {});
      render();
    } catch (loadError) {
      if (loadError?.name !== "AbortError" && stage) stage.innerHTML = `<div class="idiom-flashcard-empty"><strong>闪卡暂时无法读取</strong><p>${escapeHtml(loadError?.message || "请稍后重试")}</p></div>`;
    } finally {
      state.loading = false;
    }
  }

  function open() {
    if (!layer) return;
    layer.hidden = false;
    document.body.classList.add("idiom-modal-open");
    root.querySelector("[data-idiom-flashcard-close]")?.focus();
    void loadDeck();
  }

  function close() {
    if (!layer) return;
    layer.hidden = true;
    document.body.classList.remove("idiom-modal-open");
    library?.loadData?.();
  }

  root.querySelectorAll("[data-idiom-flashcard-open]").forEach((button) => button.addEventListener("click", open, { signal }));
  root.querySelector("[data-idiom-flashcard-close]")?.addEventListener("click", close, { signal });
  layer?.addEventListener("click", (event) => {
    if (event.target === layer) close();
    if (event.target.closest("[data-idiom-flashcard-flip]")) {
      state.flipped = !state.flipped;
      render();
    }
  }, { signal });
  prev?.addEventListener("click", () => move(-1), { signal });
  next?.addEventListener("click", () => move(1), { signal });
  learning?.addEventListener("click", () => void saveStatus("learning"), { signal });
  mastered?.addEventListener("click", () => void saveStatus("mastered"), { signal });
  document.addEventListener("keydown", (event) => {
    if (!layer || layer.hidden) return;
    if (event.key === "Escape") { close(); return; }
    if (event.key === " ") { event.preventDefault(); state.flipped = !state.flipped; render(); return; }
    if (event.key === "ArrowLeft") { event.preventDefault(); move(-1); return; }
    if (event.key === "ArrowRight") { event.preventDefault(); move(1); return; }
    if (event.key === "1") { event.preventDefault(); void saveStatus("learning"); return; }
    if (event.key === "2") { event.preventDefault(); void saveStatus("mastered"); }
  }, { signal });
  signal.addEventListener("abort", () => {
    if (layer) layer.hidden = true;
    document.body.classList.remove("idiom-modal-open");
  }, { once: true });
}

function initializeQuiz(root, signal, library) {
  const layer = root.querySelector("[data-idiom-quiz-layer]");
  const setup = root.querySelector("[data-idiom-quiz-setup]");
  const play = root.querySelector("[data-idiom-quiz-play]");
  const result = root.querySelector("[data-idiom-quiz-result]");
  const questionRoot = root.querySelector("[data-idiom-quiz-question]");
  const setupStatus = root.querySelector("[data-idiom-quiz-status]");
  const progressText = root.querySelector("[data-idiom-quiz-progress-text]");
  const progressFill = root.querySelector("[data-idiom-quiz-progress-fill]");
  const score = root.querySelector("[data-idiom-quiz-score]");
  const count = root.querySelector("[data-idiom-quiz-count]");
  const state = { kind: "meaning", questions: [], index: 0, correct: 0, answered: false, starting: false, wrongs: [] };

  function open() {
    if (!layer) return;
    layer.hidden = false;
    document.body.classList.add("idiom-modal-open");
    root.querySelector("[data-idiom-quiz-close]")?.focus();
  }

  function close() {
    if (!layer) return;
    layer.hidden = true;
    document.body.classList.remove("idiom-modal-open");
  }

  function showSetup() {
    if (setup) setup.hidden = false;
    if (play) play.hidden = true;
    if (result) result.hidden = true;
    if (setupStatus) setupStatus.textContent = "";
  }

  function renderProgress() {
    const total = state.questions.length;
    if (progressText) progressText.textContent = `${Math.min(state.index + 1, total)} / ${total}`;
    if (score) score.textContent = `${state.correct} 正确`;
    if (progressFill) progressFill.style.width = `${total ? Math.round(state.index * 100 / total) : 0}%`;
  }

  function renderQuestion() {
    const question = state.questions[state.index];
    if (!question || !questionRoot) return;
    state.answered = false;
    renderProgress();
    questionRoot.innerHTML = `
      <article class="idiom-quiz-question-card" data-idiom-current-question>
        <div class="idiom-quiz-question-meta"><span>${escapeHtml(QUIZ_KIND_LABELS[question.kind] || "成语测验")}</span><small>第 ${state.index + 1} 题</small></div>
        <h3>${escapeHtml(question.prompt)}</h3>
        <div class="idiom-quiz-choices">
          ${(question.choices || []).map((choice, index) => `<button type="button" data-idiom-quiz-choice="${index}"><span>${String.fromCharCode(65 + index)}</span><strong>${escapeHtml(choice)}</strong></button>`).join("")}
        </div>
        <section class="idiom-quiz-feedback" data-idiom-quiz-feedback hidden>
          <div><strong data-idiom-quiz-feedback-title></strong><span>资料释义</span></div>
          <p>${escapeHtml(question.explanation || "")}</p>
          <button class="button primary" type="button" data-idiom-quiz-next>${state.index + 1 >= state.questions.length ? "查看成绩" : "下一题"}</button>
        </section>
      </article>`;
  }

  async function recordQuizResult(question, correct) {
    if (!question?.entry_id) return;
    try {
      const payload = await fetchJson("/idioms/progress", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entry_id: question.entry_id, quiz_result: correct ? "correct" : "wrong" }),
        signal,
      });
      library?.updateSummary(payload.summary || {});
    } catch (recordError) {
      if (recordError?.name !== "AbortError" && setupStatus) setupStatus.textContent = "本题结果未能写入学习记录。";
    }
  }

  function finish() {
    const total = state.questions.length;
    const accuracy = total ? Math.round(state.correct * 100 / total) : 0;
    const wrongReview = state.wrongs.length ? `
      <section class="idiom-quiz-wrong-review">
        <div><span>本组错词</span><strong>${state.wrongs.length} 个需要回炉</strong></div>
        ${state.wrongs.map((item) => `
          <article><strong>${escapeHtml(item.term || item.answer || "成语")}</strong><p>${escapeHtml(item.explanation || "")}</p></article>`).join("")}
      </section>` : "";
    if (play) play.hidden = true;
    if (!result) return;
    result.hidden = false;
    result.innerHTML = `
      <div class="idiom-quiz-result-score"><span>本组成绩</span><strong>${accuracy}%</strong><small>${state.correct} / ${total} 题</small></div>
      <p>${accuracy >= 85 ? "这一组辨析比较稳定，可以换一种题型继续测。" : accuracy >= 60 ? "有一部分词义已经建立，再做一组易混辨析会更有价值。" : "这组暴露出的词先回到词库标记学习中，再进行回炉。"}</p>
      ${wrongReview}
      <div class="idiom-quiz-result-actions"><button class="button ghost" type="button" data-idiom-quiz-back>返回词库</button>${state.wrongs.length ? '<button class="button" type="button" data-idiom-quiz-review>回炉错词</button>' : ""}<button class="button primary" type="button" data-idiom-quiz-again>再来一组</button></div>`;
  }

  async function start() {
    if (state.starting) return;
    state.starting = true;
    const startButton = root.querySelector("[data-idiom-quiz-start]");
    if (startButton) startButton.disabled = true;
    if (setupStatus) setupStatus.textContent = "正在抽取题目…";
    const seed = Date.now() % 2147483647;
    try {
      const payload = await fetchJson(`/idioms/quiz-data?kind=${encodeURIComponent(state.kind)}&count=${encodeURIComponent(count?.value || "10")}&seed=${seed}`, { signal });
      state.questions = Array.isArray(payload.questions) ? payload.questions : [];
      if (!state.questions.length) throw new Error("当前题型没有可用题目");
      state.index = 0;
      state.correct = 0;
      state.wrongs = [];
      if (setup) setup.hidden = true;
      if (result) result.hidden = true;
      if (play) play.hidden = false;
      if (setupStatus) setupStatus.textContent = "";
      renderQuestion();
    } catch (startError) {
      if (startError?.name !== "AbortError" && setupStatus) setupStatus.textContent = startError?.message || "题目读取失败";
    } finally {
      state.starting = false;
      if (startButton?.isConnected) startButton.disabled = false;
    }
  }

  root.querySelector("[data-idiom-quiz-open]")?.addEventListener("click", () => {
    showSetup();
    open();
  }, { signal });
  root.querySelector("[data-idiom-quiz-close]")?.addEventListener("click", close, { signal });
  layer?.addEventListener("click", (event) => {
    if (event.target === layer) close();
  }, { signal });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && layer && !layer.hidden) close();
  }, { signal });

  root.querySelectorAll("[data-idiom-quiz-kind]").forEach((button) => {
    button.addEventListener("click", () => {
      state.kind = button.dataset.idiomQuizKind || "meaning";
      root.querySelectorAll("[data-idiom-quiz-kind]").forEach((item) => item.classList.toggle("is-active", item === button));
    }, { signal });
  });
  root.querySelector("[data-idiom-quiz-start]")?.addEventListener("click", () => void start(), { signal });

  questionRoot?.addEventListener("click", (event) => {
    const choice = event.target.closest("[data-idiom-quiz-choice]");
    if (choice && !state.answered) {
      const question = state.questions[state.index];
      const chosen = question?.choices?.[Number(choice.dataset.idiomQuizChoice)];
      const correct = chosen === question?.answer;
      state.answered = true;
      if (correct) state.correct += 1;
      else if (question) state.wrongs.push(question);
      questionRoot.querySelectorAll("[data-idiom-quiz-choice]").forEach((button) => {
        const value = question?.choices?.[Number(button.dataset.idiomQuizChoice)];
        button.disabled = true;
        button.classList.toggle("is-correct", value === question?.answer);
        button.classList.toggle("is-wrong", button === choice && !correct);
      });
      const feedback = questionRoot.querySelector("[data-idiom-quiz-feedback]");
      const title = questionRoot.querySelector("[data-idiom-quiz-feedback-title]");
      if (feedback) feedback.hidden = false;
      if (title) title.textContent = correct ? "回答正确" : `回答错误 · 正确答案：${question?.answer || ""}`;
      if (score) score.textContent = `${state.correct} 正确`;
      void recordQuizResult(question, correct);
      return;
    }
    const nextButton = event.target.closest("[data-idiom-quiz-next]");
    if (!nextButton || !state.answered) return;
    state.index += 1;
    if (state.index >= state.questions.length) {
      if (progressFill) progressFill.style.width = "100%";
      finish();
    } else {
      renderQuestion();
    }
  }, { signal });

  result?.addEventListener("click", (event) => {
    if (event.target.closest("[data-idiom-quiz-again]")) {
      showSetup();
      return;
    }
    if (event.target.closest("[data-idiom-quiz-back]")) {
      close();
      library?.loadData?.();
      return;
    }
    if (event.target.closest("[data-idiom-quiz-review]")) {
      close();
      library?.showView?.("review");
    }
  }, { signal });

  signal.addEventListener("abort", () => document.body.classList.remove("idiom-modal-open"), { once: true });
}

export function initializeIdioms(signal) {
  const root = document.querySelector("[data-idiom-page]");
  if (!root) return;
  const library = initializeLibrary(root, signal);
  initializeFlashcards(root, signal, library);
  initializeQuiz(root, signal, library);
}
