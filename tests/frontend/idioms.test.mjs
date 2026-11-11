import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

function installDom() {
  const dom = new JSDOM(`<!doctype html><html><body>
    <div data-idiom-page>
      <button data-idiom-flashcard-open>闪卡复习</button>
      <button data-idiom-quiz-open>开始成语小测</button>
      <strong data-idiom-metric="total">700</strong>
      <strong data-idiom-metric="mastered">—</strong>
      <strong data-idiom-metric="learning">—</strong>
      <strong data-idiom-metric="review_due">—</strong>
      <strong data-idiom-metric="accuracy">—</strong>
      <small data-idiom-reviewed></small>
      <button class="is-active" data-idiom-view="library" aria-selected="true">词库</button>
      <button data-idiom-view="review" aria-selected="false">回炉</button>
      <button data-idiom-view="contrast" aria-selected="false">辨析</button>
      <button data-idiom-view="mistakes" aria-selected="false">误用</button>
      <form data-idiom-filters>
        <input name="q">
        <label data-idiom-core-only><select name="category"><option value="">全部</option></select></label>
        <label data-idiom-library-only><select name="status"><option value="">全部</option><option value="starred">收藏</option></select></label>
        <button type="submit">筛选</button>
        <button type="button" data-idiom-filter-reset>清空</button>
      </form>
      <div data-idiom-filter-chips hidden></div>
      <p data-idiom-content-eyebrow></p><h2 data-idiom-content-title></h2><p data-idiom-content-note></p>
      <span data-idiom-result-count></span>
      <div data-idiom-loading></div>
      <div data-idiom-error hidden><p data-idiom-error-message></p><button data-idiom-retry>重试</button></div>
      <div data-idiom-list></div>
      <footer data-idiom-pagination hidden>
        <button data-idiom-page-prev>上一页</button><span data-idiom-page-label></span><button data-idiom-page-next>下一页</button>
      </footer>
      <div data-idiom-detail-layer hidden>
        <section>
          <h2 data-idiom-detail-title></h2>
          <button data-idiom-detail-close>×</button>
          <div data-idiom-detail-body></div>
        </section>
      </div>
      <div data-idiom-flashcard-layer hidden>
        <section>
          <button data-idiom-flashcard-close>×</button>
          <span data-idiom-flashcard-progress></span><i><b data-idiom-flashcard-progress-fill></b></i>
          <div data-idiom-flashcard-stage></div>
          <button data-idiom-flashcard-prev>上一个</button>
          <button data-idiom-flashcard-learning>学习中</button>
          <button data-idiom-flashcard-mastered>已掌握</button>
          <button data-idiom-flashcard-next>下一个</button>
        </section>
      </div>
      <div data-idiom-quiz-layer hidden>
        <section>
          <button data-idiom-quiz-close>×</button>
          <div data-idiom-quiz-setup>
            <button class="is-active" data-idiom-quiz-kind="meaning">释义</button>
            <button data-idiom-quiz-kind="context">语境</button>
            <select data-idiom-quiz-count><option value="10">10</option></select>
            <button data-idiom-quiz-start>开始</button>
            <p data-idiom-quiz-status></p>
          </div>
          <div data-idiom-quiz-play hidden>
            <span data-idiom-quiz-progress-text></span><strong data-idiom-quiz-score></strong><i><b data-idiom-quiz-progress-fill></b></i>
            <div data-idiom-quiz-question></div>
          </div>
          <div data-idiom-quiz-result hidden></div>
        </section>
      </div>
    </div>
  </body></html>`, { url: "http://localhost/idioms" });
  ["AbortController", "Element", "Event", "HTMLElement", "KeyboardEvent", "MouseEvent", "Node"].forEach((name) => {
    globalThis[name] = dom.window[name];
  });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  return dom;
}

function jsonResponse(payload, ok = true) {
  return {
    ok,
    async json() { return payload; },
  };
}

async function flush() {
  await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

const libraryPayload = {
  ok: true,
  view: "library",
  summary: { total: 700, mastered: 12, learning: 7, starred: 3, review_due: 8, reviewed: 20, accuracy: 80 },
  categories: [{ name: "言语表达", count: 20 }],
  total: 2,
  page: 1,
  pages: 1,
  entries: [
    {
      id: "idiom-1",
      term: "举重若轻",
      category: "言语表达",
      subcategory: "从容",
      definition: "形容处理繁难问题或进行繁重工作很轻松。",
      explanation: "比喻能力强，处理繁重或困难的事情显得很轻松。",
      pinyin: "jǔ zhòng ruò qīng",
      derivation: "示例出处",
      example: "面对复杂任务，他仍能举重若轻。",
      status: "learning",
      status_label: "学习中",
      is_starred: false,
      correct_count: 2,
      wrong_count: 1,
      accuracy: 67,
      review_reason: "错 1 次 · 学习中 · 正确率 67%",
    },
    {
      id: "idiom-2",
      term: "游刃有余",
      category: "言语表达",
      subcategory: "从容",
      definition: "比喻工作熟练，有实际经验，解决问题毫不费事。",
      explanation: "比喻技术熟练，经验丰富，解决问题毫不费力。",
      pinyin: "yóu rèn yǒu yú",
      derivation: "",
      example: "",
      status: "unseen",
      status_label: "未学习",
      is_starred: true,
      correct_count: 0,
      wrong_count: 0,
      accuracy: null,
      review_reason: "已收藏",
    },
  ],
};

test("idiom page renders library metrics and switches to contrast view", async () => {
  const dom = installDom();
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    if (String(url).includes("view=contrast")) {
      return jsonResponse({
        ok: true,
        view: "contrast",
        summary: libraryPayload.summary,
        categories: libraryPayload.categories,
        total: 1,
        groups: [{
          key: "言语表达::从容",
          category: "言语表达",
          subcategory: "从容",
          common_ground: "这一组都与“从容”相关，重点比较处理复杂任务时的不同侧重。",
          comparison_tip: "重点比较：语义侧重、适用对象/语境、易错边界。",
          entries: libraryPayload.entries.map((entry, index) => ({
            ...entry,
            usage_analysis: {
              meaning: entry.explanation,
              focus: index === 0 ? "侧重能力强，处理繁重任务仍显得轻松。" : "侧重技术熟练、经验丰富。",
              usage: index === 0 ? "多用于处理复杂、繁重的工作。" : "多用于技术、业务或工作熟练。",
              warning: index === 0 ? "强调处理重事时显得轻松。" : "强调熟练程度，不能只理解为态度轻松。",
              example: entry.example,
              tone: "",
            },
          })),
        }],
      });
    }
    return jsonResponse(libraryPayload);
  };
  const module = await import(`../../static/js/idioms.js?render=${Date.now()}`);
  module.initializeIdioms(new AbortController().signal);
  await flush();

  assert.equal(document.querySelector('[data-idiom-metric="mastered"]').textContent, "12");
  assert.equal(document.querySelector('[data-idiom-metric="accuracy"]').textContent, "80%");
  assert.equal(document.querySelector('[data-idiom-metric="review_due"]').textContent, "8");
  assert.equal(document.querySelectorAll("[data-idiom-entry]").length, 2);
  assert.ok(document.querySelector("[data-idiom-list]").textContent.includes("举重若轻"));
  assert.ok(document.querySelector("[data-idiom-list]").textContent.includes("处理繁重或困难的事情显得很轻松"));
  assert.ok(document.querySelector("[data-idiom-list]").textContent.includes("查看完整释义与辨析"));
  assert.ok(document.querySelector('select[name="category"]').textContent.includes("言语表达 · 20"));

  document.querySelector('[data-idiom-view="contrast"]').click();
  await flush();
  assert.equal(document.querySelector("[data-idiom-content-title]").textContent, "成语辨析");
  assert.equal(document.querySelectorAll(".idiom-contrast-group").length, 1);
  const contrastText = document.querySelector("[data-idiom-list]").textContent;
  assert.ok(contrastText.includes("共同语义"));
  assert.ok(contrastText.includes("一眼区分"));
  assert.ok(contrastText.includes("适用对象 / 语境"));
  assert.ok(contrastText.includes("易错边界"));
  assert.equal(document.querySelector("[data-idiom-result-count]").textContent, "1 组");
  assert.ok(calls.some((url) => url.includes("view=contrast")));
  dom.window.close();
});

test("review view exposes review reasons and flashcards support flip and mastery shortcuts", async () => {
  const dom = installDom();
  const posts = [];
  globalThis.fetch = async (url, options = {}) => {
    const href = String(url);
    if (href === "/idioms/progress") {
      posts.push(JSON.parse(options.body));
      return jsonResponse({
        ok: true,
        summary: { ...libraryPayload.summary, mastered: 13, review_due: 7 },
        progress: { status: posts.at(-1)?.status || "learning", is_starred: false },
      });
    }
    if (href.includes("view=review")) {
      return jsonResponse({ ...libraryPayload, view: "review", total: 2, entries: libraryPayload.entries });
    }
    return jsonResponse(libraryPayload);
  };
  const module = await import(`../../static/js/idioms.js?review=${Date.now()}`);
  module.initializeIdioms(new AbortController().signal);
  await flush();

  document.querySelector('[data-idiom-view="review"]').click();
  await flush();
  assert.equal(document.querySelector("[data-idiom-content-title]").textContent, "今日回炉");
  assert.ok(document.querySelector("[data-idiom-list]").textContent.includes("回炉原因"));
  assert.ok(document.querySelector("[data-idiom-list]").textContent.includes("错 1 次"));

  document.querySelector("[data-idiom-flashcard-open]").click();
  await flush();
  const layer = document.querySelector("[data-idiom-flashcard-layer]");
  assert.equal(layer.hidden, false);
  assert.ok(document.querySelector("[data-idiom-flashcard-stage]").textContent.includes("举重若轻"));

  document.querySelector("[data-idiom-flashcard-flip]").click();
  assert.ok(document.querySelector("[data-idiom-flashcard-flip]").classList.contains("is-flipped"));
  assert.ok(document.querySelector("[data-idiom-flashcard-stage]").textContent.includes("处理繁重或困难"));

  document.dispatchEvent(new KeyboardEvent("keydown", { key: "2", bubbles: true }));
  await flush();
  assert.deepEqual(posts[0], { entry_id: "idiom-1", status: "mastered" });
  assert.ok(document.querySelector("[data-idiom-flashcard-stage]").textContent.includes("游刃有余"));
  dom.window.close();
});

test("clicking an idiom opens a complete study card with notes and related idioms", async () => {
  const dom = installDom();
  const richPayload = {
    ...libraryPayload,
    entries: [
      {
        ...libraryPayload.entries[0],
        definition: "形容处理繁难问题很轻松。辨析：侧重面对复杂任务时仍显得从容。eg:他处理这项复杂工作举重若轻。",
      },
      libraryPayload.entries[1],
    ],
  };
  globalThis.fetch = async (url) => {
    if (String(url).includes("view=contrast")) {
      return jsonResponse({
        ok: true,
        view: "contrast",
        summary: richPayload.summary,
        categories: richPayload.categories,
        total: 1,
        groups: [{
          key: "言语表达::从容",
          category: "言语表达",
          subcategory: "从容",
          entries: richPayload.entries,
        }],
      });
    }
    return jsonResponse(richPayload);
  };
  const module = await import(`../../static/js/idioms.js?detail=${Date.now()}`);
  module.initializeIdioms(new AbortController().signal);
  await flush();

  const card = document.querySelector('[data-idiom-entry="idiom-1"]');
  assert.ok(card.textContent.includes("查看完整释义与辨析"));
  card.click();
  await flush();

  const layer = document.querySelector("[data-idiom-detail-layer]");
  const body = document.querySelector("[data-idiom-detail-body]");
  assert.equal(layer.hidden, false);
  assert.equal(document.querySelector("[data-idiom-detail-title]").textContent, "举重若轻 · 学习卡");
  assert.ok(body.textContent.includes("完整释义"));
  assert.ok(body.textContent.includes("备考资料速记"));
  assert.ok(body.textContent.includes("jǔ zhòng ruò qīng"));
  assert.ok(body.textContent.includes("辨析与使用边界"));
  assert.ok(body.textContent.includes("例句 / 语境"));
  assert.ok(body.textContent.includes("游刃有余"));

  document.querySelector("[data-idiom-detail-close]").click();
  assert.equal(layer.hidden, true);
  dom.window.close();
});

test("a failed core library does not prevent switching to the independent misuse view", async () => {
  const dom = installDom();
  globalThis.fetch = async (url) => {
    if (String(url).includes("view=mistakes")) {
      return jsonResponse({
        ok: true,
        view: "mistakes",
        summary: { total: 700, mastered: 0, learning: 0, reviewed: 0, accuracy: 0 },
        categories: [],
        total: 1,
        mistakes: [{ term: "首当其冲", note: "不能理解成首先冲上去。" }],
      });
    }
    return jsonResponse({ ok: false, error: "核心词库损坏" }, false);
  };
  const module = await import(`../../static/js/idioms.js?fallback=${Date.now()}`);
  module.initializeIdioms(new AbortController().signal);
  await flush();
  assert.equal(document.querySelector("[data-idiom-error]").hidden, false);

  document.querySelector('[data-idiom-view="mistakes"]').click();
  await flush();
  assert.equal(document.querySelector("[data-idiom-error]").hidden, true);
  assert.ok(document.querySelector("[data-idiom-list]").textContent.includes("首当其冲"));
  dom.window.close();
});

test("star and mastery actions persist through the idiom progress endpoint", async () => {
  const dom = installDom();
  const posts = [];
  globalThis.fetch = async (url, options = {}) => {
    if (String(url) === "/idioms/progress") {
      posts.push(JSON.parse(options.body));
      return jsonResponse({ ok: true, summary: libraryPayload.summary, progress: {} });
    }
    return jsonResponse(libraryPayload);
  };
  const module = await import(`../../static/js/idioms.js?progress=${Date.now()}`);
  module.initializeIdioms(new AbortController().signal);
  await flush();

  document.querySelector('[data-idiom-entry="idiom-1"] [data-idiom-star]').click();
  await flush();
  assert.deepEqual(posts[0], { entry_id: "idiom-1", is_starred: true });

  document.querySelector('[data-idiom-entry="idiom-1"] [data-idiom-status="mastered"]').click();
  await flush();
  assert.deepEqual(posts[1], { entry_id: "idiom-1", status: "mastered" });
  dom.window.close();
});

test("quick quiz reveals feedback, records result, and completes the set", async () => {
  const dom = installDom();
  const posts = [];
  globalThis.fetch = async (url, options = {}) => {
    const href = String(url);
    if (href.startsWith("/idioms/quiz-data")) {
      return jsonResponse({
        ok: true,
        kind: "meaning",
        questions: [{
          entry_id: "idiom-1",
          kind: "meaning",
          prompt: "形容处理繁难问题很轻松的是？",
          choices: ["游刃有余", "举重若轻", "如履薄冰", "无所适从"],
          answer: "举重若轻",
          explanation: "形容处理繁难问题或进行繁重工作很轻松。",
        }],
      });
    }
    if (href === "/idioms/progress") {
      posts.push(JSON.parse(options.body));
      return jsonResponse({ ok: true, summary: { ...libraryPayload.summary, reviewed: 21, accuracy: 81 }, progress: {} });
    }
    return jsonResponse(libraryPayload);
  };
  const module = await import(`../../static/js/idioms.js?quiz=${Date.now()}`);
  module.initializeIdioms(new AbortController().signal);
  await flush();

  document.querySelector("[data-idiom-quiz-open]").click();
  assert.equal(document.querySelector("[data-idiom-quiz-layer]").hidden, false);
  document.querySelector("[data-idiom-quiz-start]").click();
  await flush();

  const choices = document.querySelectorAll("[data-idiom-quiz-choice]");
  assert.equal(choices.length, 4);
  choices[1].click();
  await flush();
  assert.ok(choices[1].classList.contains("is-correct"));
  assert.equal(document.querySelector("[data-idiom-quiz-feedback]").hidden, false);
  assert.deepEqual(posts[0], { entry_id: "idiom-1", quiz_result: "correct" });

  document.querySelector("[data-idiom-quiz-next]").click();
  assert.equal(document.querySelector("[data-idiom-quiz-result]").hidden, false);
  assert.ok(document.querySelector("[data-idiom-quiz-result]").textContent.includes("100%"));
  dom.window.close();
});
