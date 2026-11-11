#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse 2015-2019 国考申论真题 PDF text into structured JSON.

Each PDF text (from pdftotext -layout) is split into:
  - materials: 给定资料 sections (材料 N)
  - questions: 作答要求 section
  - answers:   参考答案/解析 section

Output: one JSON file per PDF under data/pre2020_json/.
"""
import json
import re
from pathlib import Path

TXT_DIR = Path("/Users/xiangyang/yanshen-mac/data/pre2020_txt")
JSON_DIR = Path("/Users/xiangyang/yanshen-mac/data/pre2020_json")
JSON_DIR.mkdir(parents=True, exist_ok=True)


def clean_line(line: str) -> str:
    """Remove page footers and stray whitespace artifacts."""
    s = line.strip()
    # strip page number markers like "-1-", "  -5-"
    if re.fullmatch(r"[-–—]?\s*\d+\s*[-–—]?", s):
        return ""
    return s


def find_section_span(lines, header_re):
    """Return (start, end) indices for the section starting at a header regex.
    Start is the index of the first line matching header_re; end is the last
    content line (index) before the next major section header or EOF.
    """
    start = None
    for i, ln in enumerate(lines):
        if header_re.search(ln):
            start = i
            break
    if start is None:
        return None, None
    end = len(lines) - 1
    return start, end


def parse_materials(lines, start, end):
    """Parse 给定资料 section into ordered materials.

    输出格式与现有题库一致：段落=单行长文本，段落间以 \n\n 分隔，段内无硬换行。
    段落检测：空行 = 段落分隔；首行缩进（>=1 空格）的缩进行 = 新段落开头；
    无缩进行 = 当前段落续行。续行拼接时处理 拉丁字符/数字→汉字 折行补空格。
    """
    materials = []  # list of {number, raw_lines}
    cur = None
    mat_re = re.compile(r"^\s*材料\s*([0-9０-９]+)\s*[：:]?")
    for i in range(start, end + 1):
        raw = lines[i]
        m = mat_re.match(raw)
        if m:
            num = int(m.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789")))
            if cur is None or cur["number"] != num:
                cur = {"number": num, "raw_lines": []}
                materials.append(cur)
            body = raw[m.end():].strip()
            if body:
                cur["raw_lines"].append(raw)
            continue
        if cur is not None:
            cur["raw_lines"].append(raw)

    out = []
    for mat in materials:
        out.append({"number": mat["number"], "content": _reflow_material(mat["raw_lines"])})
    return out


def _reflow_material(raw_lines):
    """把材料原文（含 pdftotext 折行）重排为干净段落文本。"""
    paragraphs = []
    cur = []
    for raw in raw_lines:
        s = clean_line(raw)
        if not s:
            if cur:
                paragraphs.append(_join_para(cur))
                cur = []
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent >= 1 and cur:
            # 首行缩进 → 新段落
            paragraphs.append(_join_para(cur))
            cur = []
        cur.append(s)
    if cur:
        paragraphs.append(_join_para(cur))
    return "\n\n".join(p for p in paragraphs if p)


def _join_para(lines):
    """把段落内的折行拼接起来，处理 拉丁/数字→汉字 的折行空格。"""
    text = ""
    for ln in lines:
        if (
            text
            and text[-1].isascii()
            and not text[-1].isspace()
            and ln
            and not ln[0].isascii()
        ):
            text += " "
        text += ln
    # 清除汉字之间的多余空格
    text = re.sub(r"(?<=[一-鿿])\s+(?=[一-鿿])", "", text)
    return text.strip()


# 题干线索词：标记行命中这些词之一，才视为新题目的开头（用于和"要求"小项区分）
QUESTION_CUES = (
    '根据"给定资料',
    '根据“给定资料',
    "假如你是",
    "假如你",
    "请根据",
    "请结合",
    "请你",
    "谈谈你对",
    "谈谈对",
    "谈谈你",
    "谈谈",
    "阅读",
    "概括",
    "概述",
    "阐述",
    "简述",
    "整理",
    "汇总",
    "列出",
    "回答下列",
    "分析",
    "理解",
    "自拟",
    "写一篇文章",
    "写一篇",
    "写一份",
    "写一则",
    "写一封",
    "草拟",
    "拟定",
    "撰写",
    "提炼",
    "指出",
    "画线句子",
    "划线句子",
    "含义",
    "意思",
    "功能",
    "建议",
    "报道",
    "讲话",
    "短文",
    "标题",
    "经验",
    "措施",
    "做法",
    "结合",
    "题目",
    "文章",
    "材料",
    "简要",
    "如何",
    "哪些",
    "介绍",
    "汇报",
    "启示",
    "感想",
    "收获",
    "要点",
    "问题",
    "是什么",
    "为什么",
    "变化",
    "思考",
    "深入",
    "写",
    "（[一二三四五六七八九十]+）",
)


def _has_cue(text):
    for cue in QUESTION_CUES:
        if cue in text:
            return True
    return False


CHINESE_MARKER = re.compile(
    r"^\s*(?:（[一二三四五六七八九十]+）|[一二三四五六七八九十]+\s*[、．.])"
)
ARABIC_MARKER = re.compile(r"^\s*[（(](\d+)[）)]")
SCORE_RE = re.compile(r"[（(]\s*\d+\s*分[）)]")


def _score_in_lines(lines, i, n=3):
    """Look ahead up to n lines for a score marker （N 分）."""
    for j in range(i, min(i + n, len(lines))):
        if SCORE_RE.search(lines[j]):
            return True
        if re.search(r"要求\s*[：:]", lines[j]):
            return False  # 进入要求块还没出现分值 → 不是新题目
    return False


STRONG_CUES = (
    '根据"给定资料', "根据“给定资料", "根据“指定资料", '根据"指定资料',
    "请你", "假如你是", "假如你", "请根据", "请结合", "请深入", "请为", "请以",
    "写一篇文章", "写一篇", "写一份", "写一则", "写一封", "草拟", "拟定",
    "撰写", "谈谈你对", "谈谈对", "深入思考",
)

REQ_FRAGMENT_RE = re.compile(
    r"^(准确|全面|简明|扼要|条理|清晰|流畅|逻辑|观点|紧扣|语言|字数|不|参考|自选"
    r"|角度|立意|见解|深刻|思路|层次|表述|完整|具体|分条|序号|每条|每则|总|字|恰当"
    r"|归纳|提炼|说明|分析|内容|对象|理由|措施|论述|围绕|在|要点|概述|阐述|概括)"
)


def _is_requirement(rest):
    """Decide whether an Arabic marker's rest reads as a requirement item."""
    if SCORE_RE.search(rest):
        return False  # 同行分值 → 新题目
    for cue in STRONG_CUES:
        if cue in rest:
            return False  # 题干线索词 → 新题目
    if REQ_FRAGMENT_RE.match(rest):
        return True  # 以"要求用语"开头 → 要求小项
    if len(rest) < 40:
        return True  # 短片段且在要求块内 → 要求小项
    return False


def parse_questions(lines, start, end):
    """Parse 作答要求 section into questions using a requirement-block state
    machine:

      * Chinese-numeral markers （一）… / 一、… are always question starts.
      * While inside a "要求：" block, Arabic marker lines that read as short
        requirement fragments are merged into the current question; otherwise
        they start a new question.
      * Sub-questions under a "回答下列问题" stub are merged into it.
      * A line ending with / containing "要求：" enters the requirement block.
    """
    questions = []
    cur = None
    in_req = False
    stub_mode = False
    i = start
    while i <= end:
        raw = lines[i]
        ln = clean_line(raw)
        if not ln:
            if cur is not None:
                cur["parts"].append("")
            i += 1
            continue

        cm = CHINESE_MARKER.match(raw)
        am = ARABIC_MARKER.match(raw)

        if cm is not None:
            if cur is not None:
                questions.append(cur)
            cur = {"marker": cm.group(0).strip(), "parts": [raw[cm.end():].strip()]}
            in_req = False
            stub_mode = False
            i += 1
            continue

        if am is not None:
            rest = raw[am.end():]
            cur_text = "\n".join(cur["parts"]) if cur else ""
            if stub_mode or re.search(r"回答下列问题[。．.]?\s*$", cur_text.strip()):
                # sub-question under 回答下列问题 → merge into current
                if cur is not None:
                    cur["parts"].append(raw.strip())
                    stub_mode = True
                i += 1
                continue
            if in_req and _is_requirement(rest):
                if cur is not None:
                    cur["parts"].append(ln)
                i += 1
                continue
            # otherwise it's a new question
            if cur is not None:
                questions.append(cur)
            cur = {"marker": am.group(0).strip(), "parts": [rest.strip()]}
            in_req = False
            stub_mode = False
            i += 1
            continue

        # non-marker line
        if cur is not None:
            cur["parts"].append(ln)
            if re.search(r"要求\s*[：:]", ln):
                in_req = True
        i += 1

    if cur is not None:
        questions.append(cur)
    # collapse parts
    out = []
    for q in questions:
        text = "\n".join(q["parts"])
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        out.append({"marker": q["marker"], "text": text})
    return out


def main():
    for txt_path in sorted(TXT_DIR.glob("*.txt")):
        lines = txt_path.read_text(encoding="utf-8").splitlines()
        name = txt_path.stem
        doc = {"source": name, "materials": [], "questions": [], "raw_answer_section": ""}

        # ---- locate major sections ----
        sec = {}
        for idx, ln in enumerate(lines):
            s = clean_line(ln)
            if re.match(r"^一、注意事项", s):
                sec.setdefault("notes", idx)
            elif re.match(r"^二、给定材料|^二、给定资料", s):
                sec.setdefault("materials", idx)
            elif re.match(r"^三、作答要求", s):
                sec.setdefault("questions", idx)
            elif re.search(r"（解析）|卷（解析）|(?:真题|试卷).{0,12}解析", s) or re.search(r"参考答案", s):
                sec.setdefault("answers", idx)

        mats_start = sec.get("materials")
        q_start = sec.get("questions")
        a_start = sec.get("answers")
        if mats_start is None or q_start is None:
            print(f"!! {name}: missing section markers: {sec}")
            continue

        # materials: between 给定材料 and 作答要求
        doc["materials"] = parse_materials(lines, mats_start, q_start - 1)
        # questions: between 作答要求 and answer section
        q_end = (a_start - 1) if a_start is not None else len(lines) - 1
        doc["questions"] = parse_questions(lines, q_start + 1, q_end)
        # raw answer section text (for manual answer parsing)
        if a_start is not None:
            doc["raw_answer_section"] = "\n".join(lines[a_start:]).strip()
        else:
            doc["raw_answer_section"] = "(no answer section found)"

        out = JSON_DIR / f"{name}.json"
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"OK {name}: materials={len(doc['materials'])} questions={len(doc['questions'])} ans_chars={len(doc['raw_answer_section'])}")


if __name__ == "__main__":
    main()
