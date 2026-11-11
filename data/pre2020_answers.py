#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse answer sections of 2015-2019 国考申论 PDFs into per-question answers.

Reads the `raw_answer_section` from each pre2020_json/<paper>.json and writes
`answers` into the same JSON:
    answers: [ { qnum, organization, answer_text }, ... ]
"""
import json
import re
from pathlib import Path

JSON_DIR = Path("/Users/xiangyang/yanshen-mac/data/pre2020_json")

# 答案块起始标记（每题一个块）
BLOCK_START_RE = re.compile(
    r"^\s*(?:[一二三四五六七八九十]+|[0-9０-９]+)\s*[、．.]?\s*(?:【参考答案[一二]?】|参考答案)"
)

# 版本标记（同一题多版本答案）
VER_RE = re.compile(r"【参考答案[一二]】|参考答案[一二]|参考文章\s*[0-9０-９]+")

# 解析/说明终止标记：参考答案正文到此为止
END_MARKERS = (
    "参考答案说明",
    "阅读资料，提取要点",
    "第一步——审题",
    "第一步——",
    "第一步",
    "第二步阅读资料",
    "本题特点与难点",
    "【解析】",
    "【审题及答题思路】",
    "审题及答题思路",
    "1、是什么",
    "一、是什么",
    "是什么：",
    "参考解析",
    "参考答案二",
    "参考答案一",
)

# 答案正文开始标记：从该标记之后取正文（如【参考答案】【参考范文】）
BODY_START_MARKERS = ("【参考答案】", "【参考范文】")


def _strip_footer(text: str) -> str:
    """Remove page footers (isolated page numbers) and collapse blank lines."""
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if re.fullmatch(r"[-–—]?\s*\d+\s*[-–—]?", s):
            continue
        lines.append(s)
    body = "\n".join(lines).strip()
    return re.sub(r"\n\s*\n+", "\n\n", body)


def _cut_at_end_marker(text: str) -> str:
    """Cut answer body at the first end/explanation marker."""
    for marker in END_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]
            break
    return text.strip()


def parse_answer_section(raw_section: str):
    """Split raw answer section into per-question blocks."""
    lines = raw_section.splitlines()
    blocks = []  # list of (header, body_lines)
    cur_header = None
    cur_lines = []
    for ln in lines:
        s = ln.strip()
        if BLOCK_START_RE.match(s):
            if cur_header is not None:
                blocks.append((cur_header, cur_lines))
            cur_header = s
            cur_lines = []
        else:
            if cur_header is not None:
                cur_lines.append(ln)
    if cur_header is not None:
        blocks.append((cur_header, cur_lines))
    return blocks


def extract_block_answers(header: str, body_lines):
    """Extract one or more (organization, answer_text) from a block."""
    body = _strip_footer("\n".join(body_lines))

    # 版本标记
    versions = []
    for m in VER_RE.finditer(body):
        versions.append((m.start(), m.group(0)))
    if versions:
        results = []
        for idx, (pos, name) in enumerate(versions):
            nxt = versions[idx + 1][0] if idx + 1 < len(versions) else len(body)
            seg = body[pos + len(name): nxt]
            seg = seg.lstrip("：:")
            seg = _cut_at_end_marker(seg)
            if seg:
                results.append((name, seg))
        return results

    # 单版本：若块内含【参考答案】/【参考范文】（无编号），取其后；否则取块正文
    for marker in BODY_START_MARKERS:
        if marker in body:
            body = body.split(marker, 1)[1]
            break
    body = _cut_at_end_marker(body)
    if body:
        return [("参考答案", body)]
    return []


def main():
    for json_path in sorted(JSON_DIR.glob("*.json")):
        doc = json.loads(json_path.read_text(encoding="utf-8"))
        raw = doc.get("raw_answer_section", "")
        if not raw or raw.startswith("(no answer"):
            continue
        blocks = parse_answer_section(raw)
        answers = []
        for qnum, (header, body_lines) in enumerate(blocks, start=1):
            for org, text in extract_block_answers(header, body_lines):
                answers.append({"qnum": qnum, "organization": org, "answer_text": text})
        doc["answers"] = answers
        json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{json_path.stem[:40]}: blocks={len(blocks)} answers={len(answers)}")


if __name__ == "__main__":
    main()
