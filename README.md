# 研申（申论训练台）

> **本项目基于 [Nullapse/YanShen](https://github.com/Nullapse/YanShen)（MIT 协议）二次开发。**
> 原作者：**Nullapse**。感谢原作者的出色工作。
> 本仓库在原版基础上新增了 ChatGPT / Codex 账号接入、导出批改包、智能批改稳定性修复等功能。

本地申论练习应用（macOS / Windows），内置国考、省考和选调申论试卷。支持试卷检索、材料阅读、作答记录、批注、收藏、训练统计、智能批改和 AI 训练复盘。

## 相比原版的增强功能

- **ChatGPT / Codex 账号接入**：用 ChatGPT 账号登录即可调用 GPT 模型进行批改与 AI 教练，无需 API Key（复用本机 Codex CLI 的 ChatGPT OAuth 登录，设置页选择「ChatGPT / Codex 账号」模式）。
- **导出批改包（ZIP）**：一键导出包含题目、材料、参考答案、AI 评分基准、检索证据、独立字数脚本和系统提示词的压缩包，可发给网页版 GPT 批改，不消耗模型额度。
- **智能批改稳定性修复**：修复评分基准在深度思考模式下因 token 预算被推理占用而被截断、导致批改失败或压缩预算被挤占的问题。

## 主要功能

- 按试卷、地区、年份、题型、状态、关键词和答案来源筛选
- 阅读整卷材料并定位题目引用范围
- 作答草稿自动保存，多次作答可继续修改
- 材料高亮、文本批注、收藏和个人笔记
- 参考答案对比、训练统计和数据导入导出
- 基于本题参考答案共识、材料依据和个人历史的智能批改
- 逐点纠错、评分重算与 AI 训练复盘

## 智能批改

- **API 自动模式**：配置 DeepSeek 或其他 OpenAI-compatible 接口，详见 [DeepSeek API 配置教程](docs/deepseek-api-setup.md)。
- **ChatGPT / Codex 账号模式**：无需 API Key，复用本机 ChatGPT 登录（需安装 ChatGPT 桌面应用或 Codex CLI）。
- **Codex 手动模式**：生成批改包复制到 Codex，报告可粘贴回来保存。

检索、参考答案聚类和证据筛选在本地完成；只有主动使用智能批改或 AI 功能时，当前题必要数据和少量命中证据才会发送给所配置的模型服务。API Key 不应提交到仓库。

## 源码运行

```bash
pip install -r requirements.txt
python app.py          # macOS / Windows
```

打开 `http://127.0.0.1:5000`。可用 `GONGKAO_DATA_DIR` 指定个人数据目录，或用 `GONGKAO_DB_PATH` 指定测试数据库。

测试与构建：

```bash
python -m unittest discover -s tests
python scripts/audit_release.py
pyinstaller --clean --noconfirm "研申_mac.spec"     # macOS
pyinstaller --clean --noconfirm "研申.spec"          # Windows
```

## 数据与许可

程序代码使用 [MIT License](LICENSE)。题目、材料和参考答案的相关权利归原作者或原出处所有，仅供个人学习、比较和研究；内容可能存在错字、缺漏或来源差异，请结合原始来源校验。公开数据库不包含私人作答、收藏、批改记录、API Key、课程原始讲义或本机路径。

## 致谢

本项目源自 [Nullapse/YanShen](https://github.com/Nullapse/YanShen)，原始版权归原作者所有。如需补充署名、修正来源或移除内容，请通过 GitHub Issue 联系维护者。
