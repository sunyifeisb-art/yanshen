"""Source-grounded idiom learning corpus and local learning progress."""

from __future__ import annotations

import base64
import bz2
import json
import random
import re
from collections import defaultdict
from functools import lru_cache

from .idiom_core_data import CORE_B64
from .idiom_corpus_data import MISTAKE_B64
from .idiom_dictionary_data import IDIOM_DICTIONARY_B64

CORE_EXPECTED_ENTRIES = 700
CORE_EXPECTED_CATEGORIES = 65
QUIZ_KINDS = ("meaning", "context", "contrast", "usage")


def _decode_payload(encoded: str) -> dict:
    raw = bz2.decompress(base64.b64decode(encoded.encode("ascii")))
    return json.loads(raw.decode("utf-8"))


@lru_cache(maxsize=1)
def load_idiom_dictionary() -> dict[str, dict]:
    return _decode_payload(IDIOM_DICTIONARY_B64).get("entries") or {}


@lru_cache(maxsize=1)
def load_core_corpus() -> dict:
    data = _decode_payload(CORE_B64)
    entries = data.get("entries") or []
    categories = {str(row.get("category") or "") for row in entries}
    if len(entries) != CORE_EXPECTED_ENTRIES:
        raise RuntimeError(f"成语主词库覆盖异常：期望 {CORE_EXPECTED_ENTRIES} 条，实际 {len(entries)} 条")
    if len(categories) != CORE_EXPECTED_CATEGORIES:
        raise RuntimeError(f"成语主词库分类覆盖异常：期望 {CORE_EXPECTED_CATEGORIES} 类，实际 {len(categories)} 类")
    dictionary = load_idiom_dictionary()
    for row in entries:
        detail = dictionary.get(str(row.get("term") or ""), {})
        row["explanation"] = str(detail.get("explanation") or "")
        row["pinyin"] = str(detail.get("pinyin") or "")
        row["derivation"] = str(detail.get("derivation") or "")
        row["example"] = str(detail.get("example") or "")
    return data


@lru_cache(maxsize=1)
def load_mistake_corpus() -> dict:
    return _decode_payload(MISTAKE_B64)


def core_entries() -> list[dict]:
    return load_core_corpus()["entries"]


def mistake_entries() -> list[dict]:
    return load_mistake_corpus()["entries"]


def core_entry(entry_id: str) -> dict | None:
    return next((row for row in core_entries() if row.get("id") == entry_id), None)


def core_categories() -> list[tuple[str, int]]:
    counts: dict[str, int] = defaultdict(int)
    order: list[str] = []
    for row in core_entries():
        name = str(row.get("category") or "未分类")
        if name not in counts:
            order.append(name)
        counts[name] += 1
    return [(name, counts[name]) for name in order]


def comparison_groups() -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in core_entries():
        key = (str(row.get("category") or ""), str(row.get("subcategory") or ""))
        groups[key].append(row)
    output = []
    signal = ("侧重", "区别", "同义", "反义", "辨析", "相近", "一样", "不用辨析", "意思差不多")
    for (category, subcategory), rows in groups.items():
        if len(rows) < 2:
            continue
        joined = " ".join(str(row.get("definition") or "") for row in rows)
        score = sum(joined.count(word) for word in signal)
        analyses = [_usage_analysis(row) for row in rows]
        output.append(
            {
                "key": f"{category}::{subcategory}",
                "category": category,
                "subcategory": subcategory or "同组辨析",
                "common_ground": _group_common_ground(category, subcategory, analyses),
                "comparison_tip": _group_comparison_tip(analyses),
                "entries": [
                    {**row, "usage_analysis": analysis}
                    for row, analysis in zip(rows, analyses)
                ],
                "signal_score": score,
            }
        )
    output.sort(key=lambda item: (-item["signal_score"], item["category"], item["subcategory"]))
    return output


def _clean_usage_text(value: str) -> str:
    text = str(value or "").replace("~", "该成语")
    text = re.sub(r"P\s*\d+", "", text, flags=re.I)
    text = re.sub(r"(?:^|\s)(?:[①②③④⑤⑥⑦⑧⑨⑩]|\d+(?:[-.]\d+)?)[.、]?\s*", " ", text)
    return re.sub(r"\s+", " ", text).strip(" ，；。")


def _usage_fragments(value: str) -> list[str]:
    text = _clean_usage_text(value)
    if not text:
        return []
    text = re.sub(r"\s+(?=(?:区别|侧重|强调|语境|搭配|固定搭配|用于|多指|多用于|常用于|只能|只用于|不能|不可|反义词|同义)[:：]?)", "。", text)
    parts = re.split(r"[。；]|(?=\b(?:eg|例)[:：])", text, flags=re.I)
    return [part.strip(" ，；。") for part in parts if part.strip(" ，；。")]


def _pick_fragment(fragments: list[str], keywords: tuple[str, ...]) -> str:
    for fragment in fragments:
        if any(keyword in fragment for keyword in keywords):
            return fragment
    return ""


def _usage_analysis(row: dict) -> dict:
    definition = str(row.get("definition") or "")
    explanation = str(row.get("explanation") or "")
    fragments = _usage_fragments(definition)
    meaning = _clean_usage_text(explanation) or _clean_usage_text(definition)

    focus = _pick_fragment(
        fragments,
        ("侧重", "强调", "区别", "偏重", "突出", "主要", "重点", "核心", "同义", "反义"),
    )
    usage_keywords = ("用于", "多指", "多用于", "常用于", "形容", "语境", "搭配", "对象", "固定搭配", "指")
    usage_candidates = [fragment for fragment in fragments if any(keyword in fragment for keyword in usage_keywords)]
    usage = next((fragment for fragment in usage_candidates if fragment != focus), usage_candidates[0] if usage_candidates else "")
    warning = _pick_fragment(
        fragments,
        ("不能", "不可", "不用于", "不适用", "只能", "只用于", "不用做辨析", "易误", "区别"),
    )
    example = _clean_usage_text(str(row.get("example") or ""))
    if example in {"无", "暂无", "无例句"}:
        example = ""
    if not example:
        example_fragment = _pick_fragment(fragments, ("eg", "例", "例如", "比如"))
        example = re.sub(r"^(?:eg|例|例如|比如)[:：]?\s*", "", example_fragment, flags=re.I).strip()

    tone = ""
    joined = f"{definition} {explanation}"
    if "贬义" in joined:
        tone = "贬义"
    elif "褒义" in joined:
        tone = "褒义"
    elif "中性" in joined:
        tone = "中性"

    if not focus:
        focus = usage or (fragments[0] if fragments else meaning)
    if not usage:
        usage = fragments[0] if fragments else meaning

    return {
        "meaning": meaning,
        "focus": focus,
        "usage": usage,
        "warning": warning,
        "example": example,
        "tone": tone,
    }


def _group_common_ground(category: str, subcategory: str, analyses: list[dict]) -> str:
    topic = (subcategory or category or "相近语义").strip()
    return f"这一组都与“{topic}”相关，作答时要继续比较每个词的语义侧重、适用对象和使用边界。"


def _group_comparison_tip(analyses: list[dict]) -> str:
    has_warning = any(item.get("warning") for item in analyses)
    has_tone = len({item.get("tone") for item in analyses if item.get("tone")}) > 1
    has_usage = any(item.get("usage") and item.get("usage") != item.get("focus") for item in analyses)
    dimensions = ["语义侧重"]
    if has_usage:
        dimensions.append("适用对象/语境")
    if has_warning:
        dimensions.append("易错边界")
    if has_tone:
        dimensions.append("感情色彩")
    return "重点比较：" + "、".join(dimensions) + "。"


def filter_core_entries(*, query: str = "", category: str = "", status: str = "", progress=None) -> list[dict]:
    query = (query or "").strip().lower()
    category = (category or "").strip()
    status = (status or "").strip()
    progress = progress or {}
    rows = []
    for row in core_entries():
        if category and row.get("category") != category:
            continue
        if query:
            haystack = " ".join(
                str(row.get(key) or "")
                for key in ("term", "category", "subcategory", "definition", "explanation", "pinyin")
            ).lower()
            if query not in haystack:
                continue
        state = progress.get(row["id"], {})
        if status == "starred" and not state.get("is_starred"):
            continue
        if status in {"unseen", "learning", "mastered"} and state.get("status", "unseen") != status:
            continue
        rows.append(row)
    return rows


def filter_mistake_entries(query: str = "") -> list[dict]:
    query = (query or "").strip().lower()
    if not query:
        return list(mistake_entries())
    rows = []
    for row in mistake_entries():
        haystack = f"{row.get('term', '')} {row.get('note', '')}".lower()
        if query in haystack:
            rows.append(row)
    return rows


def review_queue(*, progress=None, query: str = "", category: str = "") -> list[dict]:
    """Return a deterministic weak-word queue built from local learning history.

    Priority is given to words answered incorrectly, then words marked as learning,
    then starred words. When there is no history yet, unseen words become the starter
    queue so the review surface is still useful on first launch.
    """
    progress = progress or {}
    candidates = filter_core_entries(query=query, category=category, progress=progress)

    def state_for(row):
        return progress.get(row["id"], {})

    focused = []
    for row in candidates:
        state = state_for(row)
        if int(state.get("wrong_count", 0)) > 0 or state.get("status") == "learning" or state.get("is_starred"):
            focused.append(row)
    rows = focused or [row for row in candidates if state_for(row).get("status", "unseen") == "unseen"]

    def score(row):
        state = state_for(row)
        correct = int(state.get("correct_count", 0))
        wrong = int(state.get("wrong_count", 0))
        reviewed = correct + wrong
        accuracy = round(correct * 100 / reviewed) if reviewed else 101
        status = state.get("status", "unseen")
        bucket = 0 if wrong else 1 if status == "learning" else 2 if state.get("is_starred") else 3
        return (bucket, -wrong, accuracy, -reviewed, str(row.get("category") or ""), str(row.get("term") or ""))

    return sorted(rows, key=score)


def load_progress(conn) -> dict[str, dict]:
    rows = conn.execute(
        """
        SELECT entry_id, status, is_starred, review_count, correct_count, wrong_count,
               last_reviewed_at, updated_at
          FROM idiom_progress
        """
    ).fetchall()
    return {
        row["entry_id"]: {
            "status": row["status"],
            "is_starred": bool(row["is_starred"]),
            "review_count": int(row["review_count"] or 0),
            "correct_count": int(row["correct_count"] or 0),
            "wrong_count": int(row["wrong_count"] or 0),
            "last_reviewed_at": row["last_reviewed_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    }


def update_progress(
    conn,
    entry_id: str,
    *,
    status: str | None = None,
    is_starred: bool | None = None,
    quiz_result: str | None = None,
) -> dict:
    if core_entry(entry_id) is None:
        raise ValueError("unknown idiom entry")
    current = load_progress(conn).get(entry_id, {})
    next_status = status if status in {"unseen", "learning", "mastered"} else current.get("status", "unseen")
    next_starred = bool(is_starred) if is_starred is not None else bool(current.get("is_starred"))
    review_count = int(current.get("review_count", 0))
    correct_count = int(current.get("correct_count", 0))
    wrong_count = int(current.get("wrong_count", 0))
    if quiz_result in {"correct", "wrong"}:
        review_count += 1
        if quiz_result == "correct":
            correct_count += 1
        else:
            wrong_count += 1
        if next_status == "unseen":
            next_status = "learning"
    conn.execute(
        """
        INSERT INTO idiom_progress(
            entry_id, status, is_starred, review_count, correct_count, wrong_count,
            last_reviewed_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, CASE WHEN ? <> '' THEN CURRENT_TIMESTAMP ELSE NULL END, CURRENT_TIMESTAMP)
        ON CONFLICT(entry_id) DO UPDATE SET
            status=excluded.status,
            is_starred=excluded.is_starred,
            review_count=excluded.review_count,
            correct_count=excluded.correct_count,
            wrong_count=excluded.wrong_count,
            last_reviewed_at=CASE WHEN ? <> '' THEN CURRENT_TIMESTAMP ELSE idiom_progress.last_reviewed_at END,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            entry_id,
            next_status,
            int(next_starred),
            review_count,
            correct_count,
            wrong_count,
            quiz_result or "",
            quiz_result or "",
        ),
    )
    conn.commit()
    return load_progress(conn).get(entry_id, {})


def progress_summary(progress: dict[str, dict]) -> dict:
    mastered = sum(1 for row in progress.values() if row.get("status") == "mastered")
    learning = sum(1 for row in progress.values() if row.get("status") == "learning")
    starred = sum(1 for row in progress.values() if row.get("is_starred"))
    correct = sum(int(row.get("correct_count", 0)) for row in progress.values())
    wrong = sum(int(row.get("wrong_count", 0)) for row in progress.values())
    reviewed = correct + wrong
    return {
        "mastered": mastered,
        "learning": learning,
        "starred": starred,
        "reviewed": reviewed,
        "accuracy": round(correct * 100 / reviewed) if reviewed else 0,
    }


def _example_text(definition: str) -> str:
    match = __import__("re").search(r"(?:eg|例)[：:]?\s*(.+?)(?:[。；]|$)", definition, __import__("re").I)
    return match.group(1).strip() if match else ""


def build_quiz(kind: str = "meaning", count: int = 10, seed: int = 0) -> list[dict]:
    kind = kind if kind in QUIZ_KINDS else "meaning"
    rng = random.Random(f"idiom-quiz:{kind}:{seed}")
    source = list(core_entries())
    if kind == "context":
        source = [row for row in source if _example_text(str(row.get("definition") or ""))]
    elif kind == "contrast":
        ids = {row["id"] for group in comparison_groups()[:30] for row in group["entries"]}
        source = [row for row in source if row["id"] in ids]
    source = [row for row in source if len(str(row.get("definition") or "")) >= 2]
    rng.shuffle(source)
    questions = []
    by_category: dict[str, list[dict]] = defaultdict(list)
    for row in core_entries():
        by_category[str(row.get("category") or "")].append(row)
    for target in source:
        siblings = [row for row in by_category[target.get("category", "")] if row["id"] != target["id"]]
        if len(siblings) < 3:
            siblings = [row for row in core_entries() if row["id"] != target["id"]]
        rng.shuffle(siblings)
        if kind == "usage":
            false_case = bool(rng.randrange(2))
            if false_case:
                shown = siblings[0]
                statement = f"“{target['term']}”：{shown['definition']}"
                answer = "不符合资料"
            else:
                statement = f"“{target['term']}”：{target['definition']}"
                answer = "符合资料"
            questions.append(
                {
                    "entry_id": target["id"],
                    "term": target["term"],
                    "kind": kind,
                    "prompt": statement,
                    "choices": ["符合资料", "不符合资料"],
                    "answer": answer,
                    "explanation": target["definition"],
                }
            )
        else:
            choices = [target] + siblings[:3]
            rng.shuffle(choices)
            if kind == "context":
                example = _example_text(target["definition"])
                prompt = f"根据资料中的例句/语境选择最合适的成语：{example.replace('~', '____')}"
            elif kind == "contrast":
                prompt = f"根据资料中的辨析说明选择对应成语：{target['definition']}"
            else:
                prompt = f"根据资料释义选择对应成语：{target['definition']}"
            questions.append(
                {
                    "entry_id": target["id"],
                    "term": target["term"],
                    "kind": kind,
                    "prompt": prompt,
                    "choices": [row["term"] for row in choices],
                    "answer": target["term"],
                    "explanation": target["definition"],
                }
            )
        if len(questions) >= max(1, min(int(count), 30)):
            break
    return questions
