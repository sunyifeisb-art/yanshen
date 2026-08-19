#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 2015-2019 年国考申论真题（夸克网盘 PDF 提取）导入 gongkao_seed.sqlite3。

新卷使用 SRNZ-{num} 卷号（1000+，避开已有 1-948）、GKS-{num}-Q{n} 题号，
与现有题库格式保持一致（参照 import_srnz_papers.py）。
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gongkao.taxonomy import infer_question_type

DB_PATH = ROOT / "data" / "gongkao_seed.sqlite3"
JSON_DIR = ROOT / "data" / "pre2020_json"
SOURCE_URL = "https://pan.quark.cn/s/16e2a525bd13"

# (json文件名, SRNZ 卷号, 卷种, 卷名后缀)
PAPER_PLAN = [
    ("2015年国家录用公务员考试《申论》真题卷及答案（地市级）", 1001, "地市级", "地市级"),
    ("2015年国家录用公务员考试《申论》真题卷及答案（省部级）", 1002, "省部级", "省部级"),
    ("2016年国家录用公务员考试《申论》真题卷及答案（副省级）", 1003, "副省级", "副省级"),
    ("2016年国家录用公务员考试《申论》真题卷及答案（地市级）", 1004, "地市级", "地市级"),
    ("2017年国家录用公务员考试《申论》真题卷及答案（地市级）（含解析共20面）", 1005, "地市级", "地市级"),
    ("2017年国家录用公务员考试《申论》真题卷及答案（省级）（含解析共20面）", 1006, "省级", "省级"),
    ("2018年国家录用公务员考试《申论》真题卷及答案（副省级）", 1007, "副省级", "副省级"),
    ("2018年国家录用公务员考试《申论》真题卷及答案（地市级）", 1008, "地市级", "地市级"),
    ("2019年国家公务员考试《申论》真题及答案（地市级）（含解析）", 1009, "地市级", "地市级"),
    ("2019年国家公务员考试《申论》真题及答案（省级）（含解析）", 1010, "省级", "省级"),
]


def normalize(text):
    return re.sub(r"\s+", "", text or "")


def split_question(raw):
    raw = re.sub(r"[ \t ]+", " ", (raw or "").strip())
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    match = re.search(r"要求\s*(?:[：:]|\n)", raw)
    if not match:
        match = re.search(r"\n\s*[（(]1[）)]", raw)
    if not match:
        return raw, ""
    return raw[: match.start()].strip(), raw[match.start():].strip()


def infer_word_limit(raw):
    match = re.search(
        r"(?:\d+\s*字\s*左右|(?:不超过|不多于|不少于|字数在)\s*\d+(?:\s*[-—至到]\s*\d+)?\s*字(?:以内|左右)?)",
        raw,
    )
    return match.group(0) if match else ""


def reformat_requirements(req):
    """把要求规范为"原材料的样子"（原始真题卷格式）：
       * 无分项（单行要求）→ 单行，去硬换行；
       * 有（N）分项 → "要求："独占一行，每个分项各占一行（单个 \\n 分隔，无空行）。
    """
    req = (req or "").strip()
    if not req:
        return ""
    m = re.match(r"^(要求\s*[：:]\s*)(.*)$", req, re.S)
    head = m.group(1).strip() if m else ""
    rest = m.group(2).strip() if m else req
    if re.search(r"[（(]\d+[）)]", rest):
        parts = re.split(r"(?=[（(]\d+[）)])", rest)
        parts = [re.sub(r"\s*\n+\s*", "", p).strip() for p in parts if p.strip()]
        return head + "\n" + "\n".join(parts)
    return (head + re.sub(r"\s*\n+\s*", "", rest)).strip()


def prompt_hash(prompt, requirements):
    return hashlib.sha1(normalize(prompt + requirements).encode("utf-8")).hexdigest()


def import_paper(conn, doc, srnz_num, category, suffix):
    title = f"{doc['year']}年国家公考《申论》题（{suffix}）"
    exam_type, region, province, relevance = "国考", "全国", "全国", 4
    paper_code = f"SRNZ-{srnz_num}"
    source_note = "夸克网盘真题PDF提取；材料按整张试卷共享保存"

    conn.execute(
        """
        INSERT INTO papers (
            paper_code, paper_name, paper_category, exam_type, year, region,
            source_province, target_group, zhejiang_relevance, source_note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(paper_code) DO UPDATE SET
            paper_name = excluded.paper_name,
            paper_category = excluded.paper_category,
            exam_type = excluded.exam_type,
            year = excluded.year,
            region = excluded.region,
            source_province = excluded.source_province,
            target_group = excluded.target_group,
            zhejiang_relevance = excluded.zhejiang_relevance,
            source_note = excluded.source_note,
            updated_at = CURRENT_TIMESTAMP
        """,
        (paper_code, title, category, exam_type, doc["year"], region, province, "国考", relevance, source_note),
    )
    paper_id = conn.execute("SELECT id FROM papers WHERE paper_code = ?", (paper_code,)).fetchone()["id"]

    conn.execute("DELETE FROM paper_materials WHERE paper_id = ?", (paper_id,))
    for mat in doc["materials"]:
        content = (mat.get("content") or "").strip()
        if not content:
            continue
        conn.execute(
            "INSERT INTO paper_materials (paper_id, material_number, title, content) VALUES (?, ?, ?, ?)",
            (paper_id, mat["number"], f"材料{mat['number']}", content),
        )

    q_count = a_count = 0
    for qnum, q in enumerate(doc["questions"], start=1):
        full = q["text"]
        if "回答下列问题" in full:
            # 带子题的大题：整题作题干，不拆分（答案涵盖各子题）
            prompt, requirements = full, ""
        else:
            prompt, requirements = split_question(full)
        if not prompt:
            continue
        # 清理 pdftotext 折行产生的换行，题干合并为单行（与现有数据一致）
        prompt = re.sub(r"\s*\n+\s*", "", prompt)
        requirements = reformat_requirements(requirements)
        qtype = infer_question_type(prompt, requirements)
        question_code = f"GKS-{srnz_num}-Q{qnum}"
        word_limit = infer_word_limit(requirements)
        conn.execute(
            """
            INSERT INTO questions (
                question_code, paper_id, paper_name, paper_category, question_number,
                exam_type, year, region, source_province, zhejiang_relevance,
                question_type, title, prompt, original_text, materials, requirements,
                word_limit, source_url, source_kind, is_full_original, content_hash,
                source_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(question_code) DO UPDATE SET
                paper_id = excluded.paper_id, paper_name = excluded.paper_name,
                paper_category = excluded.paper_category, question_number = excluded.question_number,
                exam_type = excluded.exam_type, year = excluded.year, region = excluded.region,
                source_province = excluded.source_province, zhejiang_relevance = excluded.zhejiang_relevance,
                question_type = excluded.question_type, title = excluded.title, prompt = excluded.prompt,
                requirements = excluded.requirements, word_limit = excluded.word_limit,
                source_url = excluded.source_url, source_kind = excluded.source_kind,
                is_full_original = excluded.is_full_original, content_hash = excluded.content_hash,
                source_note = excluded.source_note, updated_at = CURRENT_TIMESTAMP
            """,
            (
                question_code, paper_id, title, category, qnum,
                exam_type, doc["year"], region, province, relevance,
                qtype, f"第{qnum}题", prompt, "", "", requirements,
                word_limit, SOURCE_URL, "夸克PDF", 1,
                prompt_hash(prompt, requirements), source_note,
            ),
        )
        question_id = conn.execute("SELECT id FROM questions WHERE question_code = ?", (question_code,)).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO question_sources (question_id, provider, source_name, source_url, section) VALUES (?, ?, ?, ?, ?)",
            (question_id, "夸克网盘", doc["source"], SOURCE_URL, f"第{qnum}题"),
        )
        q_count += 1

        for ans in doc.get("answers", []):
            if ans["qnum"] != qnum:
                continue
            text = (ans.get("answer_text") or "").strip()
            if not text:
                continue
            org = ans.get("organization") or "参考答案"
            conn.execute(
                """
                INSERT INTO reference_answers (
                    question_id, organization, answer_text, notes, is_reviewed
                ) VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(question_id, organization) DO UPDATE SET
                    answer_text = excluded.answer_text, notes = excluded.notes,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (question_id, org, text, f"夸克网盘真题PDF参考答案（{org}）"),
            )
            a_count += 1
    return q_count, a_count


def main():
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    total_q = total_a = 0
    for stem, num, category, suffix in PAPER_PLAN:
        path = JSON_DIR / f"{stem}.json"
        if not path.exists():
            print(f"!! 缺少 {path.name}")
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        # 年份从卷名推断
        m = re.search(r"(20\d{2})年", stem)
        doc["year"] = int(m.group(1))
        doc["source"] = stem
        q, a = import_paper(conn, doc, num, category, suffix)
        total_q += q
        total_a += a
        print(f"  导入 {stem[:24]}... 题目 {q} 答案 {a}")
    conn.commit()

    # 校验
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM questions WHERE year BETWEEN 2015 AND 2019")
    n = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM papers WHERE paper_code BETWEEN 'SRNZ-1000' AND 'SRNZ-1099'")
    p = cur.fetchone()[0]
    print(f"完成：新增题目 {total_q}，答案 {total_a}；2015-2019 题目总数 {n}，新卷 {p}")


if __name__ == "__main__":
    main()
