const LETTERS = ["A", "B", "C", "D"];
const TYPE_LABELS = {
  combo: "组合型",
  count: "计数型",
  count5: "计数型·5条",
  pick: "综合分析",
  map: "对应关系",
  multi: "不定项",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function safeHttpUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function parseJsonScript(root) {
  const script = root.querySelector("[data-quiz-pack-json]");
  if (!script) return null;
  try {
    return JSON.parse(script.textContent || "{}");
  } catch {
    return null;
  }
}

function parseAnswers(root) {
  try {
    const value = JSON.parse(root.dataset.savedAnswers || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

function parseAnalysis(root) {
  const script = root.querySelector("[data-quiz-analysis-json]");
  if (!script) return null;
  try {
    const value = JSON.parse(script.textContent || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch {
    return null;
  }
}

function statementHtml(question) {
  const statements = Array.isArray(question.statements) ? question.statements : [];
  if (!statements.length) return "";
  return `
    <ol class="political-statements">
      ${statements.map((text, index) => `
        <li data-statement-index="${index}">
          <span class="political-statement-number">${"①②③④⑤"[index] || index + 1}</span>
          <span>${escapeHtml(text)}</span>
          <b data-statement-flag aria-hidden="true"></b>
        </li>
      `).join("")}
    </ol>`;
}

function explanationHtml(question) {
  const source = question.source || {};
  const href = safeHttpUrl(source.url);
  const sourceName = escapeHtml(source.name || "官方来源");
  const sourceDate = escapeHtml(source.date || "");
  const sourceHtml = href
    ? `<a href="${escapeHtml(href)}" target="_blank" rel="noreferrer">${sourceName}${sourceDate ? ` · ${sourceDate}` : ""} ↗</a>`
    : `<span>${sourceName}${sourceDate ? ` · ${sourceDate}` : ""}</span>`;
  const keyWords = (question.key_words || []).map((word) => `<mark>${escapeHtml(word)}</mark>`).join("");
  return `
    <section class="political-explanation" data-quiz-explanation hidden>
      <div class="political-explanation-head">
        <div><span>逐条解析</span><strong data-answer-summary></strong></div>
        <span class="political-trap-badge">${escapeHtml(question.trap_type || "规范表述")}</span>
      </div>
      <ol>${(question.explanations || []).map((text) => `<li>${escapeHtml(text)}</li>`).join("")}</ol>
      ${keyWords ? `<div class="political-keywords"><span>圈出关键字</span>${keyWords}</div>` : ""}
      <div class="political-source-note"><span>来源</span>${sourceHtml}</div>
    </section>`;
}

function questionHtml(question, index) {
  return `
    <article class="political-question" data-question-index="${index}">
      <header class="political-question-head">
        <span class="political-question-number">${String(index + 1).padStart(2, "0")}</span>
        <span class="political-question-type">${escapeHtml(TYPE_LABELS[question.type] || "政治理论")}</span>
        <span class="political-question-topic">${escapeHtml(question.topic || "综合")}</span>
      </header>
      <div class="political-question-copy">
        <p class="political-question-stem">${escapeHtml(question.stem || "")}</p>
        ${statementHtml(question)}
        <p class="political-question-ask">${escapeHtml(question.ask || "请选择正确答案")}</p>
      </div>
      <div class="political-options">
        ${(question.options || []).map((option, optionIndex) => `
          <button type="button" class="political-option" data-quiz-option data-option-index="${optionIndex}">
            <span>${LETTERS[optionIndex]}</span><strong>${escapeHtml(option)}</strong><i aria-hidden="true"></i>
          </button>
        `).join("")}
      </div>
      ${question.type === "multi" ? `
        <div class="political-multi-confirm">
          <span>不定项：可选择多项，多选、少选、错选均不得分</span>
          <button class="button small" type="button" data-quiz-confirm-multi>确认答案</button>
        </div>` : ""}
      ${explanationHtml(question)}
    </article>`;
}

function resultHtml(analysis) {
  const accuracy = Number(analysis?.accuracy || 0);
  const correct = Number(analysis?.correct_count || 0);
  const total = Number(analysis?.question_count || 0);
  const topicRows = Object.entries(analysis?.topic_stats || {}).map(([topic, stat]) => {
    const value = stat.total ? Math.round(stat.correct * 100 / stat.total) : 0;
    return `<div><span>${escapeHtml(topic)}</span><i><b style="width:${value}%"></b></i><strong>${stat.correct}/${stat.total}</strong></div>`;
  }).join("");
  const trapRows = Object.entries(analysis?.trap_errors || {})
    .filter(([, count]) => Number(count) > 0)
    .sort((left, right) => Number(right[1]) - Number(left[1]))
    .map(([name, count]) => `<span>${escapeHtml(name)} <b>${count}</b></span>`)
    .join("");
  const words = (analysis?.key_words || []).map((word) => `<mark>${escapeHtml(word)}</mark>`).join("");
  let level = "这一轮先把错项关键词记牢，再重做一遍。";
  if (accuracy >= 90) level = "表述辨析已经很稳，下一轮重点保持对细微关键词的敏感度。";
  else if (accuracy >= 75) level = "整体不错，优先复盘错题对应的干扰手法。";
  else if (accuracy >= 60) level = "基础判断已形成，但政策成套搭配和方向词仍需加固。";
  return `
    <div class="political-result-score"><span>本次成绩</span><strong>${accuracy}%</strong><small>${correct} / ${total} 题</small></div>
    <p class="political-result-comment">${level}</p>
    ${words ? `<div class="political-result-words"><span>最该记住的关键词</span>${words}</div>` : ""}
    <div class="political-result-grid">
      <section><h3>考点正确率</h3><div class="political-result-topics">${topicRows || "<p>暂无分组数据</p>"}</div></section>
      <section><h3>本次掉坑</h3><div class="political-result-traps">${trapRows || "<span>本次未命中高频干扰项</span>"}</div></section>
    </div>
    <div class="political-result-actions"><a class="button ghost" href="/political-quiz">返回时政首页</a><button class="button primary" type="button" data-quiz-restart>再做一遍</button></div>`;
}

function initializePack(root, signal) {
  const pack = parseJsonScript(root);
  if (!pack || !Array.isArray(pack.questions)) return;
  const mode = root.dataset.quizMode === "exam" ? "exam" : "practice";
  const attemptId = root.dataset.attemptId;
  const answers = parseAnswers(root);
  const completed = root.dataset.attemptCompleted === "true";
  const initialAnalysis = parseAnalysis(root);
  const revealed = new Set(completed
    ? pack.questions.map((_, index) => index)
    : (mode === "practice" ? Object.keys(answers).map(Number) : []));
  const quizRoot = root.querySelector("[data-quiz-root]");
  const result = root.querySelector("[data-quiz-result]");
  const nav = root.querySelector("[data-quiz-nav]");
  const progressText = root.querySelector("[data-quiz-progress-text]");
  const progressFill = root.querySelector("[data-quiz-progress-fill]");
  const scoreNow = root.querySelector("[data-quiz-score-now]");
  const startedAt = Date.now();
  let submitted = completed;
  let submitting = false;

  quizRoot.innerHTML = pack.questions.map(questionHtml).join("") + (mode === "exam" && !completed
    ? '<div class="political-submit-row"><button class="button primary" type="button" data-quiz-submit>交卷并查看解析</button><span data-quiz-submit-note>未答题也可以交卷</span></div>'
    : "");
  if (nav) {
    nav.innerHTML = pack.questions.map((_, index) => (
      `<button type="button" data-quiz-nav-index="${index}" aria-label="跳转到第${index + 1}题">${String(index + 1).padStart(2, "0")}</button>`
    )).join("");
  }

  function elapsedSeconds() {
    return Math.max(0, Math.round((Date.now() - startedAt) / 1000));
  }

  function selectedIndexes(question, answer) {
    if (question.type !== "multi") return [];
    return Array.isArray(answer)
      ? [...new Set(answer.map(Number).filter((value) => Number.isInteger(value) && value >= 0 && value < 4))].sort((a, b) => a - b)
      : [];
  }

  function expectedIndexes(question) {
    return (question.truth || []).reduce((values, truth, index) => {
      if (truth) values.push(index);
      return values;
    }, []);
  }

  function isCorrect(question, answer) {
    if (question.type === "multi") {
      return JSON.stringify(selectedIndexes(question, answer)) === JSON.stringify(expectedIndexes(question));
    }
    return Number(answer) === Number(question.answer_index);
  }

  function hasAnswer(question, answer) {
    if (question.type === "multi") return selectedIndexes(question, answer).length > 0;
    return answer !== null && answer !== undefined;
  }

  function currentCorrectCount() {
    return pack.questions.reduce((total, question, index) => (
      isCorrect(question, answers[index]) ? total + 1 : total
    ), 0);
  }

  function updateProgress() {
    const answered = pack.questions.reduce((count, question, index) => (
      hasAnswer(question, answers[index]) ? count + 1 : count
    ), 0);
    const total = pack.questions.length;
    if (progressText) progressText.textContent = `${answered} / ${total}`;
    if (progressFill) progressFill.style.width = `${total ? Math.round(answered * 100 / total) : 0}%`;
    if (scoreNow) scoreNow.textContent = mode === "exam" && !submitted ? "考试中" : `${currentCorrectCount()} 正确`;
  }

  function applyQuestionState(index) {
    const article = quizRoot.querySelector(`[data-question-index="${index}"]`);
    const question = pack.questions[index];
    if (!article || !question) return;
    const chosen = answers[index];
    const selected = question.type === "multi" ? selectedIndexes(question, chosen) : [];
    const reveal = submitted || revealed.has(index);
    article.querySelectorAll("[data-quiz-option]").forEach((button) => {
      const optionIndex = Number(button.dataset.optionIndex);
      const optionSelected = question.type === "multi"
        ? selected.includes(optionIndex)
        : Number(chosen) === optionIndex;
      const optionCorrect = question.type === "multi"
        ? Boolean(question.truth?.[optionIndex])
        : optionIndex === Number(question.answer_index);
      button.classList.toggle("is-selected", optionSelected);
      button.classList.toggle("is-correct", reveal && optionCorrect);
      button.classList.toggle("is-wrong", reveal && optionSelected && !optionCorrect);
      button.disabled = ((mode === "practice" && reveal) || submitted);
    });
    const multiConfirm = article.querySelector("[data-quiz-confirm-multi]");
    if (multiConfirm) {
      multiConfirm.hidden = mode === "exam" || submitted;
      multiConfirm.disabled = reveal || selected.length === 0;
    }
    const explanation = article.querySelector("[data-quiz-explanation]");
    if (explanation) {
      explanation.hidden = !reveal;
      const summary = explanation.querySelector("[data-answer-summary]");
      if (summary && reveal) {
        if (question.type === "multi") {
          const correctLetters = expectedIndexes(question).map((value) => LETTERS[value]).join("、");
          const chosenLetters = selected.length ? selected.map((value) => LETTERS[value]).join("、") : "未作答";
          summary.textContent = isCorrect(question, chosen)
            ? `回答正确 · ${correctLetters}`
            : `正确答案 ${correctLetters} · 你的选择 ${chosenLetters}`;
        } else {
          const correctLetter = LETTERS[Number(question.answer_index)] || "";
          const chosenLetter = chosen === undefined ? "未作答" : LETTERS[Number(chosen)] || "";
          summary.textContent = isCorrect(question, chosen)
            ? `回答正确 · ${correctLetter}`
            : `正确答案 ${correctLetter} · 你的选择 ${chosenLetter}`;
        }
      }
    }
    article.querySelectorAll("[data-statement-index]").forEach((item) => {
      const statementIndex = Number(item.dataset.statementIndex);
      const flag = item.querySelector("[data-statement-flag]");
      const truth = Boolean(question.truth?.[statementIndex]);
      item.classList.toggle("is-true", reveal && truth);
      item.classList.toggle("is-false", reveal && !truth);
      if (flag) flag.textContent = reveal ? (truth ? "✓" : "×") : "";
    });
    const navButton = nav?.querySelector(`[data-quiz-nav-index="${index}"]`);
    if (navButton) {
      const answered = hasAnswer(question, chosen);
      navButton.classList.toggle("is-answered", answered);
      navButton.classList.toggle("is-correct", reveal && answered && isCorrect(question, chosen));
      navButton.classList.toggle("is-wrong", reveal && answered && !isCorrect(question, chosen));
    }
    if (reveal) article.dataset.answered = "true";
    else delete article.dataset.answered;
  }

  function applyAllStates() {
    pack.questions.forEach((_, index) => applyQuestionState(index));
    updateProgress();
  }

  async function saveAttempt() {
    if (!attemptId) return;
    try {
      await fetch(`/political-quiz/attempts/${attemptId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answers, elapsed_seconds: elapsedSeconds() }),
        signal,
      });
    } catch (error) {
      if (error?.name !== "AbortError") console.warn("political quiz autosave failed", error);
    }
  }

  async function submitAttempt() {
    if (submitting || submitted) return;
    submitting = true;
    const submitButton = quizRoot.querySelector("[data-quiz-submit]");
    if (submitButton) submitButton.disabled = true;
    try {
      const response = await fetch(`/political-quiz/attempts/${attemptId}/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answers, elapsed_seconds: elapsedSeconds() }),
        signal,
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "交卷失败");
      submitted = true;
      pack.questions.forEach((_, index) => revealed.add(index));
      applyAllStates();
      if (result) {
        result.innerHTML = resultHtml(payload.analysis || {});
        result.hidden = false;
      }
    } catch (error) {
      if (error?.name !== "AbortError") {
        const note = quizRoot.querySelector("[data-quiz-submit-note]");
        if (note) note.textContent = error.message || "交卷失败，请重试";
      }
      if (submitButton) submitButton.disabled = false;
    } finally {
      submitting = false;
    }
  }

  quizRoot.addEventListener("click", (event) => {
    const option = event.target.closest("[data-quiz-option]");
    if (option) {
      const article = option.closest("[data-question-index]");
      const index = Number(article?.dataset.questionIndex);
      if (!Number.isInteger(index) || submitted) return;
      if (mode === "practice" && revealed.has(index)) return;
      const question = pack.questions[index];
      const optionIndex = Number(option.dataset.optionIndex);
      if (question.type === "multi") {
        const selected = selectedIndexes(question, answers[index]);
        const position = selected.indexOf(optionIndex);
        if (position >= 0) selected.splice(position, 1);
        else selected.push(optionIndex);
        answers[index] = selected.sort((a, b) => a - b);
      } else {
        answers[index] = optionIndex;
        if (mode === "practice") revealed.add(index);
      }
      applyQuestionState(index);
      updateProgress();
      if (question.type !== "multi" || mode === "exam") void saveAttempt();
      if (
        mode === "practice"
        && question.type !== "multi"
        && pack.questions.every((item, questionIndex) => hasAnswer(item, answers[questionIndex]))
      ) {
        void submitAttempt();
      }
      return;
    }
    const multiConfirm = event.target.closest("[data-quiz-confirm-multi]");
    if (multiConfirm) {
      const article = multiConfirm.closest("[data-question-index]");
      const index = Number(article?.dataset.questionIndex);
      const question = pack.questions[index];
      if (
        mode !== "practice"
        || !question
        || question.type !== "multi"
        || revealed.has(index)
        || !hasAnswer(question, answers[index])
      ) return;
      revealed.add(index);
      applyQuestionState(index);
      updateProgress();
      void saveAttempt();
      if (pack.questions.every((item, questionIndex) => hasAnswer(item, answers[questionIndex]) && revealed.has(questionIndex))) {
        void submitAttempt();
      }
      return;
    }
    const submit = event.target.closest("[data-quiz-submit]");
    if (submit) {
      void submitAttempt();
      return;
    }
    const restart = event.target.closest("[data-quiz-restart]");
    if (restart) {
      window.location.href = `/political-quiz/packs/${root.dataset.packId || ""}?mode=${mode}`;
    }
  }, { signal });

  nav?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-quiz-nav-index]");
    if (!button) return;
    const index = Number(button.dataset.quizNavIndex);
    const article = quizRoot.querySelector(`[data-question-index="${index}"]`);
    article?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, { signal });

  applyAllStates();
  if (completed && result && initialAnalysis) {
    result.innerHTML = resultHtml(initialAnalysis);
    result.hidden = false;
  }
}

function initializeHome(root, signal) {
  const GENERATION_STORAGE_KEY = "yanshen.politicalQuiz.activeGeneration";
  const scope = root.querySelector("[data-political-exam-scope]");
  const provinceField = root.querySelector("[data-political-province-field]");
  const periodPreset = root.querySelector("[data-political-period-preset]");
  const periodStart = root.querySelector("[data-political-period-start]");
  const periodEnd = root.querySelector("[data-political-period-end]");
  const form = root.querySelector("[data-political-generator]");
  const status = root.querySelector("[data-political-generator-status]");
  const startLink = root.querySelector("[data-political-start]");
  const generationProgress = root.querySelector("[data-political-generation-progress]");
  const generationMessage = root.querySelector("[data-political-generation-message]");
  const generationPercent = root.querySelector("[data-political-generation-percent]");
  const generationFill = root.querySelector("[data-political-generation-fill]");
  const generationPhase = root.querySelector("[data-political-generation-phase]");
  const phaseLabels = {
    queued: "等待启动",
    prepare: "准备命题",
    connecting: "连接 Codex",
    research: "联网检索",
    compose: "编排题目",
    batch_generate: "分批联网命题",
    parse: "解析题包",
    parse_repair: "修复返回格式",
    normalize: "整理结构",
    validate: "结构质检",
    repair: "自动修复",
    save: "保存题包",
    completed: "生成完成",
    failed: "生成失败",
  };
  let generationJobUrl = "";

  function readStoredGeneration() {
    try {
      const raw = window.sessionStorage?.getItem(GENERATION_STORAGE_KEY);
      if (!raw) return null;
      const value = JSON.parse(raw);
      if (!value || typeof value !== "object" || !value.status_url) return null;
      return value;
    } catch {
      return null;
    }
  }

  function storeGeneration(url, payload = {}) {
    if (!url) return;
    try {
      window.sessionStorage?.setItem(GENERATION_STORAGE_KEY, JSON.stringify({
        status_url: url,
        job_id: payload.job_id || "",
        status: payload.status || "running",
        progress: Number(payload.progress || 0),
        phase: payload.phase || "queued",
        message: payload.message || "正在生成题包…",
        resume_available: Boolean(payload.resume_available),
        resume_phase: payload.resume_phase || "",
      }));
    } catch {
      // Generation still works when session storage is unavailable.
    }
  }

  function clearStoredGeneration() {
    try {
      window.sessionStorage?.removeItem(GENERATION_STORAGE_KEY);
    } catch {
      // Ignore storage failures; the backend job remains authoritative.
    }
  }

  const monthValue = (year, monthIndex) => `${year}-${String(monthIndex + 1).padStart(2, "0")}`;

  function shiftedMonth(date, delta) {
    return new Date(date.getFullYear(), date.getMonth() + delta, 1);
  }

  function setPeriodRange(startDate, endDate) {
    if (periodStart) periodStart.value = monthValue(startDate.getFullYear(), startDate.getMonth());
    if (periodEnd) periodEnd.value = monthValue(endDate.getFullYear(), endDate.getMonth());
  }

  function applyPeriodPreset() {
    if (!periodPreset) return;
    const now = new Date();
    const year = now.getFullYear();
    if (periodPreset.value === "custom") return;
    if (periodPreset.value === "last_month") {
      const last = shiftedMonth(now, -1);
      setPeriodRange(last, last);
      return;
    }
    if (periodPreset.value === "last_3_months") {
      setPeriodRange(shiftedMonth(now, -2), now);
      return;
    }
    if (periodPreset.value === "first_half") {
      setPeriodRange(new Date(year, 0, 1), new Date(year, 5, 1));
      return;
    }
    if (periodPreset.value === "second_half") {
      setPeriodRange(new Date(year, 6, 1), new Date(year, 11, 1));
      return;
    }
    if (periodPreset.value === "this_year") {
      setPeriodRange(new Date(year, 0, 1), new Date(year, 11, 1));
      return;
    }
    setPeriodRange(now, now);
  }

  function syncProvince() {
    if (provinceField && scope) provinceField.hidden = scope.value !== "province";
  }

  function syncStartMode() {
    if (!startLink) return;
    const checked = root.querySelector('input[name="mode"]:checked');
    const url = new URL(startLink.href, window.location.origin);
    url.searchParams.set("mode", checked?.value === "exam" ? "exam" : "practice");
    startLink.href = `${url.pathname}${url.search}`;
  }

  function renderGenerationProgress(payload = {}) {
    const progress = Math.max(0, Math.min(100, Number(payload.progress || 0)));
    if (generationProgress) generationProgress.hidden = false;
    if (generationMessage) generationMessage.textContent = payload.message || "正在生成题包…";
    if (generationPercent) generationPercent.textContent = `${Math.round(progress)}%`;
    if (generationFill) generationFill.style.width = `${progress}%`;
    if (generationPhase) generationPhase.textContent = phaseLabels[payload.phase] || "生成中";
    if (status && payload.message) status.textContent = payload.message;
    generationProgress?.classList.toggle("is-failed", payload.status === "failed");
    generationProgress?.classList.toggle("is-complete", payload.status === "completed");
  }

  async function pollGenerationJob(url, button) {
    generationJobUrl = url;
    while (generationJobUrl === url && !signal?.aborted) {
      const response = await fetch(url, { cache: "no-store", signal });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "读取生成进度失败");
      storeGeneration(url, payload);
      renderGenerationProgress(payload);
      if (payload.status === "completed") {
        generationJobUrl = "";
        clearStoredGeneration();
        if (!payload.redirect) throw new Error("题包已生成，但没有可打开的题包地址");
        window.location.href = payload.redirect;
        return;
      }
      if (payload.status === "failed") {
        generationJobUrl = "";
        throw new Error(payload.error || payload.message || "生成失败，请重试");
      }
      await new Promise((resolve) => setTimeout(resolve, 650));
    }
    if (button) button.disabled = false;
  }

  scope?.addEventListener("change", syncProvince, { signal });
  periodPreset?.addEventListener("change", applyPeriodPreset, { signal });
  [periodStart, periodEnd].forEach((input) => {
    input?.addEventListener("change", () => {
      if (periodPreset) periodPreset.value = "custom";
    }, { signal });
  });
  root.querySelectorAll('input[name="mode"]').forEach((radio) => {
    radio.addEventListener("change", syncStartMode, { signal });
  });
  syncProvince();
  syncStartMode();
  applyPeriodPreset();

  const storedGeneration = readStoredGeneration();
  if (storedGeneration?.status_url) {
    const button = form?.querySelector('button[type="submit"]');
    renderGenerationProgress({
      ...storedGeneration,
      message: storedGeneration.message || "正在恢复生成进度…",
    });
    if (storedGeneration.status === "failed") {
      if (button) button.disabled = false;
    } else {
      if (button) button.disabled = true;
      generationJobUrl = storedGeneration.status_url;
      void pollGenerationJob(storedGeneration.status_url, button).catch((error) => {
        if (error?.name === "AbortError") return;
        const message = error.message || "读取生成进度失败，请重试";
        if (!generationProgress?.classList.contains("is-failed")) {
          renderGenerationProgress({ status: "failed", phase: "failed", message });
        }
        if (status) status.textContent = message;
        if (button) button.disabled = false;
      });
    }
  }

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    if (button) button.disabled = true;
    const previousGeneration = readStoredGeneration();
    const resumingFailedJob = previousGeneration?.status === "failed" && Boolean(previousGeneration.status_url);
    renderGenerationProgress(resumingFailedJob ? {
      ...previousGeneration,
      status: "queued",
      phase: previousGeneration.resume_phase || previousGeneration.phase || "queued",
      message: `正在从上次 ${Math.round(Number(previousGeneration.progress || 0))}% 的失败位置恢复…`,
    } : {
      status: "queued",
      progress: 1,
      phase: "queued",
      message: "正在创建生成任务…",
    });
    try {
      if (periodStart?.value && periodEnd?.value && periodStart.value > periodEnd.value) {
        throw new Error("起始月份不能晚于结束月份");
      }
      const body = new URLSearchParams(new FormData(form));
      if (resumingFailedJob) {
        const resumeJobId = previousGeneration.job_id
          || previousGeneration.status_url.match(/generation-jobs\/([0-9a-f]+)\/status/)?.[1]
          || "";
        if (resumeJobId) body.set("resume_job_id", resumeJobId);
      }
      const response = await fetch(form.action, { method: "POST", body, signal });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "生成失败");
      if (payload.status_url) storeGeneration(payload.status_url, payload);
      renderGenerationProgress(payload);
      if (payload.redirect) {
        clearStoredGeneration();
        window.location.href = payload.redirect;
        return;
      }
      if (!payload.status_url) throw new Error("生成任务已创建，但缺少进度地址");
      await pollGenerationJob(payload.status_url, button);
    } catch (error) {
      if (error?.name !== "AbortError") {
        const message = error.message || "生成失败，请重试";
        if (!generationProgress?.classList.contains("is-failed")) {
          renderGenerationProgress({ status: "failed", phase: "failed", message });
        }
        if (status) status.textContent = message;
      }
      if (button) button.disabled = false;
    }
  }, { signal });
}

export function initializePoliticalQuiz(signal) {
  const root = document.querySelector("[data-political-quiz-page]");
  if (!root) return;
  if (root.dataset.politicalQuizPage === "pack") initializePack(root, signal);
  if (root.dataset.politicalQuizPage === "home") initializeHome(root, signal);
}
