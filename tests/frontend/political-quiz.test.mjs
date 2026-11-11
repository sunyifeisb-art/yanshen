import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

function installDom(mode = "practice", overridePack = null, options = {}) {
  const pack = overridePack || {
    meta: { title: "测试时政题", period_label: "2026年9月" },
    questions: [
      {
        type: "combo",
        stem: "组合题题干",
        ask: "下列组合正确的是",
        statements: ["甲", "乙", "丙", "丁"],
        truth: [1, 1, 0, 1],
        options: ["①②③", "①②④", "①③④", "②③④"],
        answer_index: 1,
        explanations: ["①正确。", "②正确。", "③错误。", "④正确。"],
        source: { name: "中国政府网", date: "2026-09-01", url: "https://www.gov.cn/a" },
        topic: "中央文件",
        trap_type: "偷换关键词",
        key_words: ["关键词甲"],
      },
      {
        type: "pick",
        stem: "四句式题干",
        ask: "下列说法正确的是",
        options: ["A错", "B错", "C对", "D错"],
        answer_index: 2,
        explanations: ["A错误。", "B错误。", "C正确。", "D错误。"],
        source: { name: "新华社", date: "2026-09-02", url: "https://www.news.cn/b" },
        topic: "重要会议",
        trap_type: "方向颠倒",
        key_words: ["从A到B"],
      },
    ],
    traps: [],
    cheat: { trap_methods: [], numbers: [] },
  };
  const escapedPack = JSON.stringify(pack).replaceAll("<", "\\u003c");
  const savedAnswers = JSON.stringify(options.savedAnswers || {});
  const initialAnalysis = options.initialAnalysis ? JSON.stringify(options.initialAnalysis) : "";
  const dom = new JSDOM(`<!doctype html><html><body>
    <div data-political-quiz-page="pack" data-quiz-mode="${mode}" data-attempt-id="7" data-attempt-completed="${options.completed ? "true" : "false"}" data-saved-answers='${savedAnswers}'>
      <script type="application/json" data-quiz-pack-json>${escapedPack}</script>
      ${initialAnalysis ? `<script type="application/json" data-quiz-analysis-json>${initialAnalysis}</script>` : ""}
      <div data-quiz-progress><span data-quiz-progress-text></span><i><b data-quiz-progress-fill></b></i><strong data-quiz-score-now></strong></div>
      <div data-quiz-root></div>
      <div data-quiz-nav></div>
      <section data-quiz-result hidden></section>
    </div>
  </body></html>`, { url: "http://localhost/political-quiz/packs/1" });
  ["AbortController", "Element", "Event", "HTMLElement", "MouseEvent", "Node"].forEach((name) => {
    globalThis[name] = dom.window[name];
  });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  window.scrollTo = () => {};
  return dom;
}

function installHomeDom() {
  const dom = new JSDOM(`<!doctype html><html><body>
    <div data-political-quiz-page="home">
      <form data-political-generator action="/political-quiz/generate" method="post">
        <select data-political-exam-scope name="exam_scope"><option value="national" selected>国考</option></select>
        <div data-political-province-field hidden><select name="province"><option value=""></option></select></div>
        <select data-political-period-preset><option value="this_month" selected>本月</option></select>
        <input data-political-period-start type="month" name="period_start">
        <input data-political-period-end type="month" name="period_end">
        <input type="hidden" name="period" value="custom">
        <input type="radio" name="mode" value="practice" checked>
        <select name="question_count"><option value="20" selected>20题</option></select>
        <button type="submit">生成</button>
        <div data-political-generation-progress hidden>
          <strong data-political-generation-message></strong>
          <span data-political-generation-percent></span>
          <i><b data-political-generation-fill></b></i>
          <small data-political-generation-phase></small>
        </div>
        <p data-political-generator-status></p>
      </form>
    </div>
  </body></html>`, { url: "http://localhost/political-quiz" });
  ["AbortController", "Element", "Event", "FormData", "HTMLElement", "MouseEvent", "Node", "URLSearchParams"].forEach((name) => {
    if (dom.window[name]) globalThis[name] = dom.window[name];
  });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  return dom;
}

function jsonResponse(payload) {
  return {
    ok: true,
    async json() { return payload; },
  };
}

async function flush() {
  await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

test("practice mode locks an answered question and reveals its explanation immediately", async () => {
  const dom = installDom("practice");
  globalThis.fetch = async (url) => {
    if (String(url).endsWith("/submit")) {
      return jsonResponse({ ok: true, analysis: { correct_count: 2, question_count: 2, accuracy: 100, topic_stats: {}, trap_errors: {}, key_words: [] } });
    }
    return jsonResponse({ ok: true });
  };
  const quiz = await import(`../../static/js/political-quiz.js?practice=${Date.now()}`);
  quiz.initializePoliticalQuiz(new AbortController().signal);

  const first = document.querySelector('[data-question-index="0"]');
  first.querySelector('[data-option-index="1"]').click();
  await flush();

  assert.equal(first.dataset.answered, "true");
  assert.equal(first.querySelector("[data-quiz-explanation]").hidden, false);
  assert.ok(first.querySelector('[data-option-index="1"]').classList.contains("is-correct"));

  first.querySelector('[data-option-index="0"]').click();
  await flush();
  assert.ok(first.querySelector('[data-option-index="1"]').classList.contains("is-selected"));
  assert.ok(!first.querySelector('[data-option-index="0"]').classList.contains("is-selected"));

  document.querySelector('[data-question-index="1"] [data-option-index="2"]').click();
  await flush();
  assert.equal(document.querySelector("[data-quiz-result]").hidden, false);
  dom.window.close();
});

test("practice multi-select toggles choices and reveals only after explicit confirmation", async () => {
  const multiPack = {
    meta: { title: "上海不定项", period_label: "2026年9月" },
    questions: [{
      type: "multi",
      stem: "不定项题干",
      ask: "正确的有",
      options: ["甲", "乙", "丙", "丁"],
      truth: [1, 0, 1, 0],
      explanations: ["甲正确。", "乙错误。", "丙正确。", "丁错误。"],
      source: { name: "中国政府网", date: "2026-09-01", url: "https://www.gov.cn/multi" },
      topic: "上海省情",
      trap_type: "张冠李戴",
      key_words: ["甲", "丙"],
    }],
    traps: [],
    cheat: { trap_methods: [], numbers: [] },
  };
  const dom = installDom("practice", multiPack);
  globalThis.fetch = async (url) => {
    if (String(url).endsWith("/submit")) {
      return jsonResponse({ ok: true, analysis: { correct_count: 1, question_count: 1, accuracy: 100, topic_stats: {}, trap_errors: {}, key_words: [] } });
    }
    return jsonResponse({ ok: true });
  };
  const quiz = await import(`../../static/js/political-quiz.js?multi=${Date.now()}`);
  quiz.initializePoliticalQuiz(new AbortController().signal);

  const first = document.querySelector('[data-question-index="0"]');
  first.querySelector('[data-option-index="0"]').click();
  first.querySelector('[data-option-index="2"]').click();
  assert.equal(first.querySelector("[data-quiz-explanation]").hidden, true);
  assert.ok(first.querySelector('[data-option-index="0"]').classList.contains("is-selected"));
  assert.ok(first.querySelector('[data-option-index="2"]').classList.contains("is-selected"));

  first.querySelector("[data-quiz-confirm-multi]").click();
  await flush();
  assert.equal(first.dataset.answered, "true");
  assert.equal(first.querySelector("[data-quiz-explanation]").hidden, false);
  assert.equal(document.querySelector("[data-quiz-result]").hidden, false);
  dom.window.close();
});

test("exam mode allows changing answers and hides explanations until submit", async () => {
  const dom = installDom("exam");
  globalThis.fetch = async (url) => {
    if (String(url).endsWith("/submit")) {
      return jsonResponse({ ok: true, analysis: { correct_count: 2, question_count: 2, accuracy: 100, topic_stats: {}, trap_errors: {}, key_words: [] } });
    }
    return jsonResponse({ ok: true });
  };
  const quiz = await import(`../../static/js/political-quiz.js?exam=${Date.now()}`);
  quiz.initializePoliticalQuiz(new AbortController().signal);

  const first = document.querySelector('[data-question-index="0"]');
  first.querySelector('[data-option-index="0"]').click();
  first.querySelector('[data-option-index="1"]').click();
  await flush();

  assert.ok(!first.querySelector('[data-option-index="0"]').classList.contains("is-selected"));
  assert.ok(first.querySelector('[data-option-index="1"]').classList.contains("is-selected"));
  assert.equal(first.querySelector("[data-quiz-explanation]").hidden, true);
  assert.equal(first.dataset.answered, undefined);
  assert.equal(document.querySelectorAll("[data-quiz-nav-index]").length, 2);
  assert.ok(document.querySelector('[data-quiz-nav-index="0"]').classList.contains("is-answered"));

  document.querySelector('[data-question-index="1"] [data-option-index="2"]').click();
  document.querySelector("[data-quiz-submit]").click();
  await flush();

  assert.equal(first.querySelector("[data-quiz-explanation]").hidden, false);
  assert.equal(document.querySelector("[data-quiz-result]").hidden, false);
  assert.ok(document.querySelector("[data-quiz-result]").textContent.includes("100%"));
  assert.ok(document.querySelector('[data-quiz-nav-index="0"]').classList.contains("is-correct"));
  dom.window.close();
});

test("completed attempt opens directly in locked review mode with result summary", async () => {
  const analysis = {
    correct_count: 1,
    question_count: 2,
    accuracy: 50,
    topic_stats: { 中央文件: { correct: 1, total: 1 }, 重要会议: { correct: 0, total: 1 } },
    trap_errors: { 方向颠倒: 1 },
    key_words: ["从A到B"],
  };
  const dom = installDom("exam", null, {
    completed: true,
    savedAnswers: { 0: 1, 1: 0 },
    initialAnalysis: analysis,
  });
  let fetchCalls = 0;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    return jsonResponse({ ok: true });
  };
  const quiz = await import(`../../static/js/political-quiz.js?review=${Date.now()}`);
  quiz.initializePoliticalQuiz(new AbortController().signal);

  const first = document.querySelector('[data-question-index="0"]');
  assert.equal(first.querySelector("[data-quiz-explanation]").hidden, false);
  assert.equal(first.querySelector('[data-option-index="1"]').disabled, true);
  assert.equal(document.querySelector("[data-quiz-submit]"), null);
  assert.equal(document.querySelector("[data-quiz-result]").hidden, false);
  assert.ok(document.querySelector("[data-quiz-result]").textContent.includes("50%"));

  first.querySelector('[data-option-index="0"]').click();
  await flush();
  assert.ok(first.querySelector('[data-option-index="1"]').classList.contains("is-selected"));
  assert.equal(fetchCalls, 0);
  dom.window.close();
});

test("home generation shows backend progress and surfaces a failed job without hanging", async () => {
  const dom = installHomeDom();
  const calls = [];
  globalThis.fetch = async (url, _options = {}) => {
    calls.push(String(url));
    if (String(url).includes("generation-jobs")) {
      return jsonResponse({
        ok: true,
        job_id: "abc",
        status: "failed",
        progress: 67,
        phase: "failed",
        message: "结构质检未通过",
        error: "结构质检未通过",
        resume_available: true,
        resume_phase: "validate",
      });
    }
    return {
      ok: true,
      status: 202,
      async json() {
        return {
          ok: true,
          status: "queued",
          progress: 3,
          phase: "queued",
          message: "生成任务已创建",
          status_url: "/political-quiz/generation-jobs/abc/status",
        };
      },
    };
  };
  const quiz = await import(`../../static/js/political-quiz.js?home-progress=${Date.now()}`);
  quiz.initializePoliticalQuiz(new AbortController().signal);
  document.querySelector("[data-political-generator]").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
  await flush();
  await flush();

  const progress = document.querySelector("[data-political-generation-progress]");
  assert.equal(progress.hidden, false);
  assert.ok(progress.classList.contains("is-failed"));
  assert.equal(document.querySelector("[data-political-generation-percent]").textContent, "67%");
  assert.equal(document.querySelector("[data-political-generation-message]").textContent, "结构质检未通过");
  assert.ok(calls.some((url) => url.includes("generation-jobs/abc/status")));
  assert.equal(document.querySelector('button[type="submit"]').disabled, false);
  const storedFailed = JSON.parse(window.sessionStorage.getItem("yanshen.politicalQuiz.activeGeneration"));
  assert.equal(storedFailed.job_id, "abc");
  assert.equal(storedFailed.status, "failed");
  assert.equal(storedFailed.progress, 67);
  assert.equal(storedFailed.resume_available, true);
  dom.window.close();
});

test("retrying a failed generation sends the previous job id and keeps the failed progress", async () => {
  const dom = installHomeDom();
  window.sessionStorage.setItem("yanshen.politicalQuiz.activeGeneration", JSON.stringify({
    status_url: "/political-quiz/generation-jobs/abc123/status",
    job_id: "abc123",
    status: "failed",
    progress: 94,
    phase: "failed",
    message: "Codex 请求超时，请稍后重试。",
    resume_available: true,
    resume_phase: "repair",
  }));
  let submittedBody = null;
  let statusReads = 0;
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).includes("generation-jobs")) {
      statusReads += 1;
      return jsonResponse({
        ok: true,
        job_id: "abc123",
        status: statusReads >= 1 ? "running" : "queued",
        progress: 94,
        phase: "repair",
        message: "已恢复上次进度，正在从 94% 继续…",
        resume_available: false,
      });
    }
    submittedBody = String(options.body || "");
    return {
      ok: true,
      status: 202,
      async json() {
        return {
          ok: true,
          job_id: "abc123",
          status: "queued",
          progress: 94,
          phase: "repair",
          message: "正在从上次 94% 的中断位置继续生成…",
          resumed: true,
          status_url: "/political-quiz/generation-jobs/abc123/status",
        };
      },
    };
  };
  const controller = new AbortController();
  const quiz = await import(`../../static/js/political-quiz.js?home-retry=${Date.now()}`);
  quiz.initializePoliticalQuiz(controller.signal);
  document.querySelector("[data-political-generator]").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
  await flush();
  await flush();

  assert.ok(submittedBody?.includes("resume_job_id=abc123"));
  assert.equal(document.querySelector("[data-political-generation-percent]").textContent, "94%");
  assert.equal(document.querySelector("[data-political-generation-phase]").textContent, "自动修复");
  const stored = JSON.parse(window.sessionStorage.getItem("yanshen.politicalQuiz.activeGeneration"));
  assert.equal(stored.job_id, "abc123");
  assert.equal(stored.progress, 94);

  controller.abort();
  dom.window.close();
});

test("home generation resumes the active backend job after navigating away and back", async () => {
  const dom = installHomeDom();
  window.sessionStorage.setItem("yanshen.politicalQuiz.activeGeneration", JSON.stringify({
    status_url: "/political-quiz/generation-jobs/resume123/status",
    status: "running",
    progress: 42,
    phase: "research",
    message: "正在核验官方来源",
  }));
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return jsonResponse({
      ok: true,
      status: "running",
      progress: 55,
      phase: "research",
      message: "正在核验官方来源 · 已完成 4 轮检索",
    });
  };
  const controller = new AbortController();
  const quiz = await import(`../../static/js/political-quiz.js?home-resume=${Date.now()}`);
  quiz.initializePoliticalQuiz(controller.signal);
  await flush();

  assert.equal(document.querySelector("[data-political-generation-progress]").hidden, false);
  assert.equal(document.querySelector("[data-political-generation-percent]").textContent, "55%");
  assert.equal(document.querySelector("[data-political-generation-phase]").textContent, "联网检索");
  assert.ok(calls.some((url) => url.includes("generation-jobs/resume123/status")));
  assert.equal(document.querySelector('button[type="submit"]').disabled, true);
  const stored = JSON.parse(window.sessionStorage.getItem("yanshen.politicalQuiz.activeGeneration"));
  assert.equal(stored.progress, 55);

  controller.abort();
  dom.window.close();
});
