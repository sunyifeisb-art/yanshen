import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

function installDom(html, url = "http://localhost/attempts/7") {
  const dom = new JSDOM(`<!doctype html><html><body>${html}</body></html>`, { url });
  [
    "AbortController",
    "CustomEvent",
    "DOMParser",
    "Element",
    "Event",
    "FormData",
    "HTMLAnchorElement",
    "HTMLFormElement",
    "HTMLElement",
    "InputEvent",
    "MouseEvent",
    "Node",
    "NodeFilter",
  ].forEach((name) => {
    globalThis[name] = dom.window[name];
  });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.localStorage = dom.window.localStorage;
  globalThis.sessionStorage = dom.window.sessionStorage;
  globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
  window.requestAnimationFrame = (callback) => callback();
  window.scrollTo = () => {};
  return dom;
}

// 事件处理走 setTimeout(0)，固定 sleep 在负载高的 runner 上会抖；
// 改成轮询直到条件成立，超时才失败。
async function waitUntil(predicate, message, timeout = 2000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (predicate()) return;
    await new Promise((resolve) => window.setTimeout(resolve, 10));
  }
  assert.ok(predicate(), message);
}

test("alignment applies only to the caret line or selected paragraphs", async () => {
  const dom = installDom(`
    <div class="answer-editor-toolbar" data-editor-toolbar data-editor-target="#answer">
      <button type="button" data-editor-align="left">左</button>
      <button type="button" data-editor-align="center">中</button>
      <button type="button" data-editor-align="right">右</button>
    </div>
    <div id="answer" contenteditable="true" data-text-annotation>第一行\n第二行\n第三行</div>
  `);
  const practice = await import(`../../static/js/practice.js?alignment=${Date.now()}`);
  const annotations = await import(`../../static/js/annotations.js?alignment=${Date.now()}`);
  practice.initializeEditorToolbars(new AbortController().signal);
  const editor = document.querySelector("#answer");
  annotations.renderTextAnnotations(editor);

  const lines = () => Array.from(editor.querySelectorAll(":scope > [data-editor-line]"));
  const selection = window.getSelection();
  const caret = document.createRange();
  caret.setStart(lines()[1].firstChild, 2);
  caret.collapse(true);
  selection.removeAllRanges();
  selection.addRange(caret);
  document.dispatchEvent(new Event("selectionchange"));
  document.querySelector('[data-editor-align="center"]').dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  document.querySelector('[data-editor-align="center"]').click();

  assert.deepEqual(lines().map((line) => line.style.textAlign), ["", "center", ""]);

  const paragraphSelection = document.createRange();
  paragraphSelection.setStart(lines()[0].firstChild, 1);
  paragraphSelection.setEnd(lines()[1].firstChild, 2);
  selection.removeAllRanges();
  selection.addRange(paragraphSelection);
  document.dispatchEvent(new Event("selectionchange"));
  document.querySelector('[data-editor-align="right"]').dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  document.querySelector('[data-editor-align="right"]').click();

  assert.deepEqual(lines().map((line) => line.style.textAlign), ["right", "right", ""]);
  assert.equal(annotations.editableValue(editor), "第一行\n第二行\n第三行");
  dom.window.close();
});

test("a new line inherits centered alignment and keeps the center button active", async () => {
  const dom = installDom(`
    <div class="answer-editor-toolbar" data-editor-toolbar data-editor-target="#answer">
      <button type="button" data-editor-align="left">左</button>
      <button type="button" data-editor-align="center">中</button>
      <button type="button" data-editor-align="right">右</button>
    </div>
    <div id="answer" contenteditable="true" data-text-annotation
      data-paragraph-alignments='["center"]'>居中标题</div>
  `);
  const practice = await import(`../../static/js/practice.js?enter-alignment=${Date.now()}`);
  practice.initializeEditorToolbars(new AbortController().signal);
  const editor = document.querySelector("#answer");
  const firstLine = editor.querySelector(":scope > [data-editor-line]");
  const selection = window.getSelection();
  const caret = document.createRange();
  caret.setStart(firstLine.firstChild, firstLine.firstChild.nodeValue.length);
  caret.collapse(true);
  selection.removeAllRanges();
  selection.addRange(caret);
  document.dispatchEvent(new Event("selectionchange"));

  editor.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  firstLine.append(document.createTextNode("\n"));
  const nextLineCaret = document.createRange();
  nextLineCaret.setStart(firstLine.lastChild, 1);
  nextLineCaret.collapse(true);
  selection.removeAllRanges();
  selection.addRange(nextLineCaret);
  editor.dispatchEvent(new InputEvent("input", {
    bubbles: true,
    inputType: "insertParagraph",
    data: null,
  }));

  assert.equal(practice.paragraphAlignmentsJson(editor), '["center","center"]');
  assert.equal(document.querySelector('[data-editor-align="center"]').getAttribute("aria-pressed"), "true");
  assert.equal(document.querySelector('[data-editor-align="left"]').getAttribute("aria-pressed"), "false");
  dom.window.close();
});

test("an empty answer editor stays empty until the user types", async () => {
  const dom = installDom(`
    <div class="answer-editor-toolbar" data-editor-toolbar data-editor-target="#answer">
      <button type="button" data-editor-align="left">左</button>
      <button type="button" data-editor-align="center">中</button>
      <button type="button" data-editor-align="right">右</button>
    </div>
    <div id="answer" contenteditable="true" data-text-annotation></div>
  `);
  const practice = await import(`../../static/js/practice.js?empty-alignment=${Date.now()}`);
  practice.initializeEditorToolbars(new AbortController().signal);

  assert.equal(document.querySelector("#answer").childNodes.length, 0);
  assert.equal(document.querySelectorAll("#answer > [data-editor-line]").length, 0);
  dom.window.close();
});

test("material font controls resize material text and persist the choice", async () => {
  const dom = installDom(`
    <div class="tabbed-content" data-tabs>
      <div data-material-font-controls>
        <button type="button" data-material-font-size-down>A−</button>
        <span data-material-font-size-value>16</span>
        <button type="button" data-material-font-size-up>A+</button>
      </div>
      <section class="tab-panel active-panel" id="material-panel-0">
        <div class="material-text">材料正文</div>
      </section>
    </div>
  `);
  localStorage.setItem("gongkao.materialFontSize:v1", "20");
  const { initializeTabsAndPagination } = await import(`../../static/js/shell.js?material-font=${Date.now()}`);
  initializeTabsAndPagination(new AbortController().signal, () => {});

  const tabset = document.querySelector("[data-tabs]");
  const value = document.querySelector("[data-material-font-size-value]");
  const down = document.querySelector("[data-material-font-size-down]");
  const up = document.querySelector("[data-material-font-size-up]");

  assert.equal(tabset.style.getPropertyValue("--material-font-size"), "20px");
  assert.equal(value.textContent, "20");
  const pressDown = new MouseEvent("mousedown", { bubbles: true, cancelable: true });
  assert.equal(down.dispatchEvent(pressDown), false);
  assert.equal(pressDown.defaultPrevented, true);
  assert.equal(tabset.style.getPropertyValue("--material-font-size"), "18px");
  assert.equal(localStorage.getItem("gongkao.materialFontSize:v1"), "18");

  // The browser still emits a click after the handled mousedown. It must not
  // apply the delta twice.
  down.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 }));
  assert.equal(tabset.style.getPropertyValue("--material-font-size"), "18px");

  // WKWebView/accessibility activation can emit a mouse-like click without a
  // preceding mousedown. It must still resize even when detail is non-zero.
  up.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 }));
  assert.equal(tabset.style.getPropertyValue("--material-font-size"), "20px");

  // Keyboard/programmatic activation has detail=0 and remains supported too.
  up.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 0 }));
  assert.equal(tabset.style.getPropertyValue("--material-font-size"), "22px");
  dom.window.close();
});

test("material selectionchange opens the annotation toolbar and can apply a highlight", async () => {
  const dom = installDom(`
    <div class="material-text" style="line-height: 32px" data-material-highlight
      data-annotation-type="material" data-annotation-id="3" data-highlight-scope="material-3">材料正文测试</div>
  `);
  dom.window.Range.prototype.getBoundingClientRect = () => ({
    top: 100,
    bottom: 120,
    left: 50,
    width: 100,
    height: 20,
    right: 150,
  });
  const annotations = await import(`../../static/js/annotations.js?selectionchange-highlight=${Date.now()}`);
  const controller = new AbortController();
  annotations.initializeAnnotations(controller.signal);
  Object.defineProperty(document.documentElement, "clientWidth", { configurable: true, value: 1200 });
  Object.defineProperty(document.documentElement, "clientHeight", { configurable: true, value: 800 });
  const toolbar = document.querySelector("[data-highlight-toolbar]");
  toolbar.getBoundingClientRect = () => ({
    top: 0,
    bottom: 40,
    left: 0,
    width: 240,
    height: 40,
    right: 240,
  });

  const material = document.querySelector("[data-material-highlight]");
  const textNode = material.firstChild;
  const range = document.createRange();
  range.setStart(textNode, 0);
  range.setEnd(textNode, 4);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  document.dispatchEvent(new Event("selectionchange"));
  document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  await waitUntil(() => toolbar.hidden === false, "annotation toolbar did not appear for the material selection");

  assert.equal(toolbar.hidden, false);
  assert.equal(toolbar.dataset.placement, "right");
  assert.equal(toolbar.style.left, "162px");
  toolbar.querySelector('[data-highlight-color="yellow"]').click();
  assert.equal(material.querySelector(".material-highlight")?.textContent, "材料正文");

  controller.abort();
  dom.window.close();
});

test("same-paper material marks use one browser key across questions", async () => {
  const dom = installDom(`
    <div id="first" data-material-highlight data-highlight-scope="paper-7"
      data-material-id="42" data-question-id="11" data-saved-annotations="[]">材料正文</div>
    <div id="second" data-material-highlight data-highlight-scope="paper-7"
      data-material-id="42" data-question-id="12" data-saved-annotations="[]">材料正文</div>
  `);
  const annotations = await import(`../../static/js/annotations.js?paper-sharing=${Date.now()}`);
  const first = document.querySelector("#first");
  const second = document.querySelector("#second");
  assert.equal(annotations.materialHighlightKey(first), annotations.materialHighlightKey(second));
  localStorage.setItem(
    annotations.legacyMaterialHighlightKey(first),
    JSON.stringify([{ start: 0, end: 2, color: "yellow" }]),
  );
  assert.equal(annotations.localTextAnnotations(first)?.[0]?.color, "yellow");
  dom.window.close();
});

test("practice initialization updates the current 25-cell line counter without aborting page mount", async () => {
  const dom = installDom(`
    <form data-answer-form data-word-limit="500">
      <div contenteditable="true" class="answer-compose-editor" data-answer-input>测试作答</div>
      <span data-word-count>0</span>
      <span data-word-status></span>
      <span data-current-line-status></span>
    </form>
  `);
  const practice = await import(`../../static/js/practice.js?practice-grid-import=${Date.now()}`);
  practice.initializePractice(new AbortController().signal);

  assert.equal(document.querySelector("[data-word-count]").textContent, "4");
  assert.equal(document.querySelector("[data-current-line-status]").textContent, "本行：4/25格");
  dom.window.close();
});

test("manual line breaks consume the 300-cell answer-sheet budget", async () => {
  const dom = installDom(`
    <form data-answer-form data-word-limit="不超过300字">
      <div contenteditable="true" class="answer-compose-editor" data-answer-input></div>
      <span data-word-count></span>
      <span data-line-status></span>
      <span data-word-status></span>
      <span data-current-line-status></span>
    </form>
  `);
  const practice = await import(`../../static/js/practice.js?inclusive-count=${Date.now()}`);
  practice.initializePractice(new AbortController().signal);
  const form = document.querySelector("[data-answer-form]");
  const editor = form.querySelector("[data-answer-input]");
  editor.textContent = `${"甲".repeat(9)}\n${"乙".repeat(275)}`;
  editor.dispatchEvent(new InputEvent("input", { bubbles: true }));
  assert.equal(form.querySelector("[data-word-count]").textContent, "300");
  assert.equal(form.querySelector("[data-line-status]").textContent, "行数：12/12行");
  assert.match(form.querySelector("[data-word-status]").textContent, /还可写 0 格；实写 284 字，换行留空 16 格/);
  assert.equal(form.classList.contains("is-over-limit"), false);

  const { answerGridMetrics } = await import("../../static/js/core.js");
  assert.equal(answerGridMetrics(`${editor.textContent}\n\n\n`).occupiedCells, 300);

  editor.textContent += "丙";
  editor.dispatchEvent(new InputEvent("input", { bubbles: true }));
  assert.equal(form.classList.contains("is-over-limit"), true);
  assert.equal(form.querySelector("[data-line-status]").textContent, "行数：13/12行");
  assert.match(form.querySelector("[data-word-status]").textContent, /超出 1 格/);
  dom.window.close();
});

test("cmd-f page search counts matches and reveals hidden tabs and details", async () => {
  const dom = installDom(`
    <main class="main">
      <p>第一处目标文字</p>
      <div class="tabbed-content" data-tabs>
        <button type="button" role="tab" class="active-tab" data-tab-target="panel-a" aria-selected="true">A</button>
        <button type="button" role="tab" data-tab-target="panel-b" aria-selected="false">B</button>
        <section class="tab-panel active-panel" id="panel-a">普通内容</section>
        <section class="tab-panel" id="panel-b" hidden>
          <details><summary>展开内容</summary><p>第二处目标文字</p></details>
        </section>
      </div>
    </main>
  `);
  const shell = await import(`../../static/js/shell.js?page-find=${Date.now()}`);
  const controller = new AbortController();
  shell.initializeTabsAndPagination(controller.signal, () => {});
  shell.initializePageFind(controller.signal);

  document.dispatchEvent(new window.KeyboardEvent("keydown", {
    key: "f",
    metaKey: true,
    bubbles: true,
    cancelable: true,
  }));
  const bar = document.querySelector("[data-page-find]");
  const input = bar.querySelector("[data-page-find-input]");
  input.value = "目标文字";
  input.dispatchEvent(new Event("input", { bubbles: true }));

  assert.equal(bar.hidden, false);
  assert.equal(bar.querySelector("[data-page-find-count]").textContent, "1 / 2");
  input.dispatchEvent(new window.KeyboardEvent("keydown", {
    key: "Enter",
    bubbles: true,
    cancelable: true,
  }));

  assert.equal(bar.querySelector("[data-page-find-count]").textContent, "2 / 2");
  assert.equal(document.querySelector("#panel-b").hidden, false);
  assert.equal(document.querySelector("#panel-b details").open, true);

  document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  assert.equal(bar.hidden, true);
  controller.abort();
  dom.window.close();
});

test("grading report tabs switch between software and imported reports", async () => {
  const dom = installDom(`
    <section class="inline-report-stack">
      <div data-report-switcher>
        <button class="grading-report-tab is-active" type="button" data-report-target="report-2">导入批改报告</button>
        <button class="grading-report-tab" type="button" data-report-target="report-1">软件批改报告</button>
      </div>
      <article id="report-2" data-report-card>导入报告</article>
      <article id="report-1" data-report-card hidden>软件报告</article>
    </section>
  `);
  const grading = await import(`../../static/js/grading.js?report-switch=${Date.now()}`);
  const controller = new AbortController();
  grading.initializeGrading(controller.signal, () => {});

  const imported = document.querySelector("#report-2");
  const software = document.querySelector("#report-1");
  const softwareButton = document.querySelector('[data-report-target="report-1"]');
  assert.equal(imported.hidden, false);
  assert.equal(software.hidden, true);
  softwareButton.click();
  assert.equal(imported.hidden, true);
  assert.equal(software.hidden, false);
  assert.equal(softwareButton.classList.contains("is-active"), true);
  assert.equal(window.location.hash, "#report-1");
  controller.abort();
  dom.window.close();
});

test("grading markdown import auto-submits after a file is selected", async () => {
  const dom = installDom(`
    <form data-report-import-form>
      <input type="file" data-report-import-file>
      <button type="button" data-report-import-trigger>导入批改数据</button>
      <span data-report-import-status></span>
    </form>
  `);
  const grading = await import(`../../static/js/grading.js?report-import=${Date.now()}`);
  const controller = new AbortController();
  let submitted = false;
  const form = document.querySelector("[data-report-import-form]");
  form.requestSubmit = () => { submitted = true; };
  grading.initializeGrading(controller.signal, () => {});
  const input = document.querySelector("[data-report-import-file]");
  Object.defineProperty(input, "files", { value: [{ name: "external-report.md" }] });
  input.dispatchEvent(new Event("change", { bubbles: true }));

  assert.equal(submitted, true);
  assert.equal(document.querySelector("[data-report-import-trigger]").disabled, true);
  assert.match(document.querySelector("[data-report-import-status]").textContent, /external-report\.md/);
  controller.abort();
  dom.window.close();
});

test("native pdf export forwards one click to the mac host", async () => {
  const dom = installDom(`
    <main class="main">
      <a href="/questions/7/export.pdf" download data-native-download data-download-label="本题 PDF">导出本题 PDF</a>
    </main>
  `);
  const calls = [];
  window.pywebview = {
    api: {
      download_file: async (href) => {
        calls.push(href);
        return { ok: true, path: "/Users/test/Downloads/题目.pdf" };
      },
    },
  };
  const shell = await import(`../../static/js/shell.js?native-download=${Date.now()}`);
  const controller = new AbortController();
  shell.initializeNativeDownloads(controller.signal);
  document.querySelector("[data-native-download]").click();
  await new Promise((resolve) => window.setTimeout(resolve, 0));

  assert.deepEqual(calls, ["/questions/7/export.pdf"]);
  assert.equal(document.querySelector("[data-native-download]").getAttribute("aria-busy"), null);
  assert.match(document.querySelector("[data-native-download-toast]").textContent, /本题 PDF 已导出/);
  controller.abort();
  dom.window.close();
});

test("native pdf export never navigates the webview when the host bridge is late", async () => {
  const dom = installDom(`
    <main class="main">
      <a href="/questions/7/export.pdf" download data-native-download data-download-label="本题 PDF">导出本题 PDF</a>
    </main>
  `);
  delete window.pywebview;
  const shell = await import(`../../static/js/shell.js?native-download-late=${Date.now()}`);
  const controller = new AbortController();
  const calls = [];
  shell.initializeNativeDownloads(controller.signal);
  const link = document.querySelector("[data-native-download]");
  const click = new MouseEvent("click", { bubbles: true, cancelable: true });
  const dispatched = link.dispatchEvent(click);
  assert.equal(dispatched, false);
  assert.equal(click.defaultPrevented, true);

  window.pywebview = {
    api: {
      download_file: async (href) => {
        calls.push(href);
        return { ok: true, path: "/Users/test/Downloads/题目.pdf" };
      },
    },
  };
  window.dispatchEvent(new Event("pywebviewready"));
  await new Promise((resolve) => window.setTimeout(resolve, 0));

  assert.deepEqual(calls, ["/questions/7/export.pdf"]);
  assert.match(document.querySelector("[data-native-download-toast]").textContent, /本题 PDF 已导出/);
  controller.abort();
  dom.window.close();
});

test("record filters use regular partial navigation instead of the library list endpoint", async () => {
  const dom = installDom(`
    <main class="main">
      <form class="auto-filter" action="/attempts" method="get">
        <select name="status"><option value="graded" selected>已批改</option></select>
        <input name="q" value="基层治理">
      </form>
    </main>
  `, "http://localhost/attempts");
  const { initializeFilters } = await import(`../../static/js/shell.js?record-filter=${Date.now()}`);
  const navigations = [];
  const form = document.querySelector("form");
  initializeFilters(new AbortController().signal, (url) => navigations.push(url));

  form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));

  assert.equal(navigations.length, 1);
  assert.equal(navigations[0].pathname, "/attempts");
  assert.equal(navigations[0].searchParams.get("status"), "graded");
  assert.equal(navigations[0].searchParams.get("q"), "基层治理");
  dom.window.close();
});

test("highlighting paragraphs across a blank line keeps exactly one blank paragraph", async () => {
  const dom = installDom(`
    <div class="answer-editor-toolbar" data-editor-toolbar data-editor-target="#answer">
      <button type="button" data-editor-align="left">左</button>
    </div>
    <div id="answer" contenteditable="true" data-text-annotation
      data-annotation-type="answer" data-annotation-id="7" data-highlight-scope="attempt-7">甲\n\n乙文</div>
  `);
  dom.window.Range.prototype.getBoundingClientRect = () => ({
    top: 100,
    bottom: 120,
    left: 50,
    width: 100,
    height: 20,
    right: 150,
  });
  const practice = await import(`../../static/js/practice.js?blank-highlight=${Date.now()}`);
  const annotations = await import(`../../static/js/annotations.js?blank-highlight=${Date.now()}`);
  const controller = new AbortController();
  practice.initializeEditorToolbars(controller.signal);
  annotations.initializeAnnotations(controller.signal);

  const editor = document.querySelector("#answer");
  const lines = editor.querySelectorAll(":scope > [data-editor-line]");
  const range = document.createRange();
  range.setStart(lines[0].firstChild, 0);
  range.setEnd(lines[2].firstChild, lines[2].firstChild.nodeValue.length);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  const highlightToolbar = document.querySelector("[data-highlight-toolbar]");
  await waitUntil(() => highlightToolbar && highlightToolbar.hidden === false, "annotation toolbar did not appear for the paragraph selection");
  document.querySelector('[data-highlight-color="yellow"]').click();

  const renderedLines = editor.querySelectorAll(":scope > [data-editor-line]");
  assert.equal(annotations.editableValue(editor), "甲\n\n乙文");
  assert.equal(renderedLines.length, 3);
  assert.equal(renderedLines[1].childNodes.length, 0);
  assert.deepEqual(
    Array.from(editor.querySelectorAll(".text-annotation-highlight")).map((mark) => mark.textContent),
    ["甲", "乙文"],
  );
  controller.abort();
  dom.window.close();
});

test("workflow more menu opens on click and its actions remain clickable", async () => {
  const dom = installDom(`
    <div data-workflow-menu>
      <button type="button" data-workflow-menu-toggle aria-expanded="false">更多</button>
      <div data-workflow-menu-popover hidden><button type="button" data-action>操作</button></div>
    </div>
  `);
  const core = await import(`../../static/js/core.js?menu=${Date.now()}`);
  core.initializeWorkflowMenus(new AbortController().signal);
  const menu = document.querySelector("[data-workflow-menu]");
  const toggle = menu.querySelector("[data-workflow-menu-toggle]");
  const popover = menu.querySelector("[data-workflow-menu-popover]");
  let actions = 0;
  popover.querySelector("[data-action]").addEventListener("click", () => { actions += 1; });

  menu.dispatchEvent(new MouseEvent("mouseenter", { bubbles: false }));
  assert.equal(popover.hidden, true);
  toggle.click();
  assert.equal(popover.hidden, false);
  assert.equal(toggle.getAttribute("aria-expanded"), "true");
  popover.querySelector("[data-action]").click();
  assert.equal(actions, 1);
  assert.equal(popover.hidden, true);
  dom.window.close();
});

test("silent grading replacement leaves the sidebar navigation untouched", async () => {
  const dom = installDom(`
    <aside class="sidebar"><nav class="nav"><a class="active" href="/attempts">原标签</a></nav></aside>
    <main class="main"><p>旧批改内容</p></main>
  `);
  document.body.dataset.activeSection = "grading";
  const navigation = await import(`../../static/js/navigation.js?silent=${Date.now()}`);
  navigation.configureNavigation(() => {});
  const originalNav = document.querySelector(".sidebar .nav");
  globalThis.fetch = async () => new Response(`<!doctype html><html><head><title>批改完成</title></head>
    <body data-active-section="grading"><aside class="sidebar"><nav class="nav"><a href="/attempts">新标签</a></nav></aside>
    <main class="main"><p>新批改内容</p></main></body></html>`, {
    status: 200,
    headers: { "Content-Type": "text/html" },
  });

  await navigation.navigatePartial(new URL("http://localhost/attempts/7#report-1"), {
    requestInit: { method: "POST" },
    replace: true,
    silent: true,
  });

  assert.equal(document.querySelector(".sidebar .nav"), originalNav);
  assert.equal(originalNav.textContent.trim(), "原标签");
  assert.equal(document.querySelector(".main").textContent.trim(), "新批改内容");
  dom.window.close();
});

test("sidebar sync preserves existing labels while updating active state", async () => {
  const dom = installDom(`
    <nav class="nav">
      <a href="/home">Home</a>
      <div class="nav-cluster is-expanded">
        <a class="nav-primary" href="/papers">Library</a>
        <nav class="nav-submenu"><a class="active" href="/papers">Papers</a><a href="/">All</a></nav>
      </div>
      <a class="nav-settings" href="/settings">Settings</a>
    </nav>
  `, "http://localhost/papers");
  const navigation = await import(`../../static/js/navigation.js?sidebar=${Date.now()}`);
  const currentNav = document.querySelector(".nav");
  const papersLabel = currentNav.querySelector('.nav-submenu a[href="/papers"]');
  const nextDocument = new DOMParser().parseFromString(`
    <nav class="nav">
      <a href="/home">Home</a>
      <div class="nav-cluster is-expanded">
        <a class="nav-primary" href="/papers">Library</a>
        <nav class="nav-submenu"><a href="/papers">Papers</a><a class="active" href="/">All</a></nav>
      </div>
      <a class="nav-settings" href="/settings">Settings</a>
    </nav>
  `, "text/html");

  navigation.syncSidebarNavigation(currentNav, nextDocument.querySelector(".nav"));

  assert.equal(currentNav.querySelector('.nav-submenu a[href="/papers"]'), papersLabel);
  assert.equal(papersLabel.classList.contains("active"), false);
  assert.equal(currentNav.querySelector('.nav-submenu a[href="/"]').classList.contains("active"), true);
  dom.window.close();
});

test("answer editor height follows the requested writing length", async () => {
  const dom = installDom("<main></main>");
  const practice = await import(`../../static/js/practice.js?height=${Date.now()}`);

  assert.equal(practice.answerEditorDefaultHeight("300字以内"), 356);
  assert.equal(practice.answerEditorDefaultHeight("500 字左右"), 556);
  assert.equal(practice.answerEditorDefaultHeight("1000字左右"), 656);
  assert.equal(practice.answerEditorDefaultHeight("未标注"), 356);
  dom.window.close();
});

test("opening or editing the answer does not start an idle timer", async () => {
  const dom = installDom(`
    <div data-practice-timer data-timer-kind="question" data-timer-key="question-9">
      <strong data-timer-display></strong>
      <button type="button" data-timer-toggle>Start</button>
      <button type="button" data-timer-reset>Reset</button>
    </div>
  `);
  const timers = await import(`../../static/js/timers.js?activity=${Date.now()}`);
  const container = document.querySelector("[data-practice-timer]");
  timers.bindPracticeTimer(container);

  assert.equal(timers.readPracticeTimerState("question-9").running, false);
  container.dispatchEvent(new Event("focus"));
  container.dispatchEvent(new InputEvent("beforeinput", { bubbles: true }));
  assert.equal(timers.readPracticeTimerState("question-9").running, false);
  assert.equal("startFromActivity" in container.__practiceTimer, false);
  container.querySelector("[data-timer-toggle]").click();
  assert.equal(timers.readPracticeTimerState("question-9").running, true);

  (window.__gongkaoPageIntervals || []).forEach((timer) => window.clearInterval(timer));
  dom.window.close();
});

test("entering the paper library does not guess a page size before its grid exists", async () => {
  const dom = installDom(`
    <aside class="sidebar"></aside>
    <main class="main" style="width: 1200px"></main>
    <a id="papers-link" href="/papers">题库</a>
  `, "http://localhost/home");
  const navigation = await import(`../../static/js/navigation.js?cross-library=${Date.now()}`);
  const url = navigation.partialNavigationUrl(document.querySelector("#papers-link"));

  assert.equal(url.pathname, "/papers");
  assert.equal(url.searchParams.has("per_page"), false);
  dom.window.close();
});

test("saved paragraph alignment is restored and serialized", async () => {
  const dom = installDom(`
    <div class="answer-editor-toolbar" data-editor-toolbar data-editor-target="#answer">
      <button type="button" data-editor-align="left">左</button>
      <button type="button" data-editor-align="center">中</button>
      <button type="button" data-editor-align="right">右</button>
    </div>
    <div id="answer" contenteditable="true" data-text-annotation
      data-paragraph-alignments='["center","left","right"]'>标题\n正文\n落款</div>
  `);
  const practice = await import(`../../static/js/practice.js?persisted-alignment=${Date.now()}`);
  practice.initializeEditorToolbars(new AbortController().signal);
  const editor = document.querySelector("#answer");

  assert.deepEqual(
    Array.from(editor.querySelectorAll(":scope > [data-editor-line]")).map((line) => line.style.textAlign),
    ["center", "", "right"],
  );
  assert.equal(practice.paragraphAlignmentsJson(editor), '["center","left","right"]');
  dom.window.close();
});

test("mistake notebook switches details and persists review state", async () => {
  const dom = installDom(`
    <div data-mistake-book>
      <button data-mistake-select="report:1:point:a" class="mistake-card is-selected">A</button>
      <button data-mistake-select="report:2:point:b" class="mistake-card">B</button>
      <article data-mistake-detail="report:1:point:a">
        <span data-mistake-state class="mistake-state-pill active">待复盘</span>
        <b data-mistake-review-count>0</b>
        <button data-mistake-action="review">复盘 +1</button>
      </article>
      <article data-mistake-detail="report:2:point:b" hidden>
        <span data-mistake-state class="mistake-state-pill active">待复盘</span>
        <b data-mistake-review-count>0</b>
        <button data-mistake-action="mastered">标记已掌握</button>
      </article>
    </div>
  `, "http://localhost/mistakes");
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async (_url, options) => {
    const values = new URLSearchParams(options.body);
    const action = values.get("action");
    return {
      ok: true,
      async json() {
        return {
          ok: true,
          state: action === "mastered" ? "mastered" : "reviewing",
          state_label: action === "mastered" ? "已掌握" : "复盘中",
          is_starred: false,
          review_count: action === "review" ? 1 : 0,
        };
      },
    };
  };
  try {
    const mistakes = await import(`../../static/js/mistakes.js?mistakes=${Date.now()}`);
    mistakes.initializeMistakes(new AbortController().signal);
    const secondCard = document.querySelector('[data-mistake-select="report:2:point:b"]');
    secondCard.click();
    assert.equal(secondCard.classList.contains("is-selected"), true);
    assert.equal(document.querySelector('[data-mistake-detail="report:1:point:a"]').hidden, true);
    assert.equal(document.querySelector('[data-mistake-detail="report:2:point:b"]').hidden, false);

    document.querySelector('[data-mistake-detail="report:2:point:b"] [data-mistake-action]').click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(document.querySelector('[data-mistake-detail="report:2:point:b"] [data-mistake-state]').textContent, "已掌握");
    assert.equal(secondCard.classList.contains("is-mastered"), true);
  } finally {
    globalThis.fetch = previousFetch;
    dom.window.close();
  }
});
