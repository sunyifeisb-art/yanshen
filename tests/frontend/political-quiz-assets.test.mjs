import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const appJs = fs.readFileSync(new URL("../../static/app.js", import.meta.url), "utf8");
const appCss = fs.readFileSync(new URL("../../static/app.css", import.meta.url), "utf8");

test("political quiz module and stylesheet are mounted by the app shell", () => {
  assert.match(appJs, /import \{ initializePoliticalQuiz \} from "\.\/js\/political-quiz\.js"/);
  assert.match(appJs, /initializePoliticalQuiz\(signal\)/);
  assert.match(appCss, /@import url\("\.\/css\/political-quiz\.css"\)/);
});

test("political quiz stylesheet uses the existing editorial tokens and responsive layout", () => {
  const css = fs.readFileSync(new URL("../../static/css/political-quiz.css", import.meta.url), "utf8");
  assert.match(css, /var\(--green\)/);
  assert.match(css, /var\(--paper\)/);
  assert.match(css, /var\(--line\)/);
  assert.match(css, /\.political-launch-grid/);
  assert.match(css, /\.political-question/);
  assert.match(css, /@media\s*\(max-width:\s*860px\)/);
});
