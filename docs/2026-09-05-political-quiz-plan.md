# 时政测验 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在研申中新增可持续更新的“时政测验”一级模块，支持已核验题包、ChatGPT/Codex 联网生成、练习/考试双模式、本地成绩与易错复盘。

**Architecture:** 新增 `gongkao/political_quiz.py` 负责题包协议、校验、持久化和生成提示词；新增 `PoliticalQuizController` 负责页面/JSON 接口；前端用独立 `static/js/political-quiz.js` 与 `static/css/political-quiz.css` 渲染交互。题包与作答均落 SQLite，避免把 Skill 的单文件 HTML 直接嵌入应用。

**Tech Stack:** Python 3 标准库、SQLite、现有 Jinja 布局、原生 ES Modules、现有 CSS token、Node test runner。

---

### Task 1: 题包协议与数据库

**Files:**
- Create: `gongkao/political_quiz.py`
- Modify: `gongkao/db.py`
- Test: `tests/test_political_quiz.py`

- [ ] **Step 1: 写题包校验失败测试**
  - 合法 combo/count/count5/pick/map 题包通过。
  - 缺来源、选项非4个、count 数量错位、解析条数错位、traps 未覆盖全部题号时返回错误。
- [ ] **Step 2: 运行 `python -m unittest tests.test_political_quiz -v`，确认因模块不存在而失败。**
- [ ] **Step 3: 实现 `validate_quiz_pack(data)`、`normalize_quiz_pack(data)` 和 JSON 辅助函数。**
- [ ] **Step 4: 为 `political_quiz_packs`、`political_quiz_attempts` 增加 schema 与 current-version additive migration。**
- [ ] **Step 5: 实现题包保存/读取、作答创建/更新/完成、首页统计查询。**
- [ ] **Step 6: 重跑单测并确认通过。**

### Task 2: 联网生成通道

**Files:**
- Modify: `gongkao/ai.py`
- Modify: `gongkao/political_quiz.py`
- Test: `tests/test_political_quiz.py`

- [ ] **Step 1: 写测试验证联网生成命令显式包含 `--search` 且仍为只读临时执行。**
- [ ] **Step 2: 写测试验证生成提示词包含官方来源白名单、20题默认配比、政治表述逐字核对、JSON-only 约束。**
- [ ] **Step 3: 运行测试确认失败。**
- [ ] **Step 4: 新增 `codex_research_completion()`，复用认证/模型/超时处理。**
- [ ] **Step 5: 新增 `build_generation_prompt()` 与 `generate_quiz_pack()`；仅 ChatGPT/Codex 模式允许“最新”生成。**
- [ ] **Step 6: 解析模型 JSON、运行结构质检，通过后保存 ready 题包，失败保存错误状态。**
- [ ] **Step 7: 重跑单测。**

### Task 3: 路由、控制器与侧边栏

**Files:**
- Create: `gongkao/web/controllers/political_quiz.py`
- Modify: `gongkao/web/controllers/__init__.py`
- Modify: `gongkao/web/application.py`
- Modify: `gongkao/web/routing.py`
- Modify: `gongkao/web/templates/layout.html`
- Test: `tests/test_release_runtime.py`

- [ ] **Step 1: 写路由测试：GET `/political-quiz`、GET `/political-quiz/packs/{id}`、POST 生成/答题/交卷接口。**
- [ ] **Step 2: 写布局测试：侧边栏出现“时政测验”且 active=`political_quiz` 时高亮。**
- [ ] **Step 3: 运行目标测试确认失败。**
- [ ] **Step 4: 注册 controller mixin、GET/POST route。**
- [ ] **Step 5: 增加侧边栏一级入口，位置在错题本之后、AI 教练之前。**
- [ ] **Step 6: 实现首页、题包答题页、生成接口、保存答案接口、交卷接口。**
- [ ] **Step 7: 重跑目标测试。**

### Task 4: 原生视觉与答题交互

**Files:**
- Create: `static/css/political-quiz.css`
- Create: `static/js/political-quiz.js`
- Modify: `static/app.css`
- Modify: `static/app.js`
- Test: `tests/frontend/political-quiz.test.mjs`

- [ ] **Step 1: 写前端失败测试：练习模式选题即锁定并显示解析；考试模式允许改答案且交卷前不显示解析。**
- [ ] **Step 2: 写失败测试：完成后显示成绩、错题关键字、重新开始状态清空。**
- [ ] **Step 3: 运行 `node --test tests/frontend/political-quiz.test.mjs` 确认失败。**
- [ ] **Step 4: 实现 `initializePoliticalQuiz()`，通过 data-* 驱动页面，复用页面级 AbortSignal。**
- [ ] **Step 5: 实现样式：现有 paper/green/line token、单列答题、粘性进度、低饱和对错色、解析批注线。**
- [ ] **Step 6: 在 `app.js` 与 `app.css` 中挂载模块。**
- [ ] **Step 7: 重跑前端测试。**

### Task 5: 结果、复盘与首页统计

**Files:**
- Modify: `gongkao/political_quiz.py`
- Modify: `gongkao/web/controllers/political_quiz.py`
- Modify: `static/js/political-quiz.js`
- Modify: `static/css/political-quiz.css`
- Test: `tests/test_political_quiz.py`
- Test: `tests/frontend/political-quiz.test.mjs`

- [ ] **Step 1: 写结果统计测试：总正确率、topic 分组、trap_type 五手法分组、3 个关键字提炼。**
- [ ] **Step 2: 运行确认失败。**
- [ ] **Step 3: 实现 `build_attempt_analysis()`。**
- [ ] **Step 4: 首页显示最近测验和累计易错结构；完成页显示三类复盘结果。**
- [ ] **Step 5: 重跑目标测试。**

### Task 6: 全量验证

**Files:**
- No production changes unless verification exposes regression.

- [ ] **Step 1: `python -m unittest tests.test_political_quiz -v`。**
- [ ] **Step 2: `python -m unittest tests.test_release_runtime -v`。**
- [ ] **Step 3: `npm test`。**
- [ ] **Step 4: `npm run lint:js` 与 `npm run lint:css`。**
- [ ] **Step 5: `git diff --check`。**
- [ ] **Step 6: 检查 `git diff` 仅包含本功能相关改动，不覆盖用户现有未提交文件的其它内容。**
