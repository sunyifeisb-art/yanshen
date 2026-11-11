import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

test("material drawer moves the annotated material without duplicating or losing it", async () => {
  const dom = new JSDOM(`<!doctype html><html><body>
    <section data-grading-material-home>
      <div data-grading-material-content>
        <div data-material-highlight data-highlight-scope="paper-7" data-material-id="42"
          data-saved-annotations='[{"start":0,"end":2,"color":"yellow"}]'>
          <mark class="material-highlight" data-highlight-color="yellow">高亮</mark>材料正文
        </div>
      </div>
    </section>
    <p data-grading-material-placeholder hidden>材料正在左侧显示。</p>
    <button type="button" data-open-grading-materials aria-expanded="false">显示材料</button>
    <div data-grading-material-drawer hidden>
      <button type="button" data-close-grading-materials>关闭</button>
      <div data-grading-material-drawer-body></div>
    </div>
  </body></html>`, { url: "http://localhost/attempts/7" });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.localStorage = dom.window.localStorage;
  globalThis.sessionStorage = dom.window.sessionStorage;
  const { initializeMaterialDrawer } = await import("../../static/js/grading.js");
  const controller = new dom.window.AbortController();
  const trigger = document.querySelector("[data-open-grading-materials]");
  const close = document.querySelector("[data-close-grading-materials]");
  const drawer = document.querySelector("[data-grading-material-drawer]");
  const home = document.querySelector("[data-grading-material-home]");
  const placeholder = document.querySelector("[data-grading-material-placeholder]");
  const content = document.querySelector("[data-grading-material-content]");
  const material = document.querySelector("[data-material-highlight]");

  initializeMaterialDrawer(controller.signal);
  trigger.click();
  assert.equal(drawer.hidden, false);
  assert.equal(trigger.getAttribute("aria-expanded"), "true");
  assert.equal(drawer.contains(content), true);
  assert.equal(placeholder.hidden, false);
  assert.equal(document.activeElement, close);
  assert.equal(document.querySelectorAll("[data-material-highlight]").length, 1);
  assert.equal(material.querySelector("mark")?.textContent, "高亮");
  assert.match(material.dataset.savedAnnotations, /yellow/);

  document.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.equal(drawer.hidden, true);
  assert.equal(home.contains(content), true);
  assert.equal(placeholder.hidden, true);
  assert.equal(document.activeElement, trigger);
  assert.equal(document.querySelector("[data-material-highlight]"), material);

  trigger.click();
  close.click();
  assert.equal(home.contains(content), true);
  trigger.click();
  controller.abort();
  assert.equal(home.contains(content), true);
  assert.equal(drawer.hidden, true);
  dom.window.close();
});
