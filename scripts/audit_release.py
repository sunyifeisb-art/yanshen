import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gongkao.db import CURRENT_SCHEMA_VERSION
from gongkao.taxonomy import QUESTION_TYPES, classify_question_type

MINIMUM = {"papers": 1600, "questions": 5800, "reference_answers": 13500}
PRIVATE_PATH = re.compile(r"(?:[A-Za-z]:\\|/Users/|/home/)")
ALLOWED_QUESTION_TYPES = set(QUESTION_TYPES)

# 组题引导语：真题里「请仔细阅读材料，回答下面两题」这类句子被误建成独立题目行，
# 它没有、也不该有参考答案。识别出来按“非题目”处理，不计入缺答案统计。
PREAMBLE_PATTERNS = (
    re.compile(r"^\s*本题仅限报考"),
    re.compile(r"^\s*请仔细阅读(给定)?材料[，,]?根据要求进行概括"),
    re.compile(r"回答(下面|下列)?(以下)?\s*[两二若干\d]*\s*(个|小)?\s*题"),
    re.compile(r"回答(下面|下列|以下)\s*[两二若干\d]*\s*(个|小)?\s*问题"),
    re.compile(r"回答(下列|以下)?问题[。：:]?\s*$"),
    re.compile(r"请回答[：:]?\s*$"),
    re.compile(r"分别回答(下列|以下)?问题"),
    re.compile(r"^\s*按要求谈谈对以下"),
)

# 题干里必须出现的作答动词。全都没有，说明这一行只是材料引子或残句，不是一道题。
INSTRUCTION_MARKERS = (
    "请",
    "简",
    "概括",
    "归纳",
    "分析",
    "谈谈",
    "回答",
    "写",
    "提出",
    "论述",
    "说明",
    "指出",
    "拟",
    "草拟",
    "解释",
    "评价",
    "结合",
    "理解",
    "归纳",
)


def is_preamble(prompt):
    text = (prompt or "").strip()
    if not text:
        return False
    if any(pattern.search(text) for pattern in PREAMBLE_PATTERNS):
        return True
    return not any(marker in text for marker in INSTRUCTION_MARKERS)


def audit_database(path):
    conn = sqlite3.connect(path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"schema version: expected {CURRENT_SCHEMA_VERSION}, got {version}"
            )
        for table, minimum in MINIMUM.items():
            actual = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if actual < minimum:
                raise RuntimeError(f"{table}: expected at least {minimum}, got {actual}")
        checks = {
            "papers without questions": "SELECT COUNT(*) FROM papers p WHERE NOT EXISTS (SELECT 1 FROM questions q WHERE q.paper_id = p.id)",
            "papers without materials": "SELECT COUNT(*) FROM papers p WHERE NOT EXISTS (SELECT 1 FROM paper_materials m WHERE m.paper_id = p.id AND TRIM(COALESCE(m.content, '')) <> '')",
            "placeholder answers": "SELECT COUNT(*) FROM reference_answers WHERE TRIM(COALESCE(answer_text, '')) IN ('', '暂无答案数据') OR TRIM(COALESCE(organization, '')) IN ('', '??????')",
            "stored API keys": "SELECT COUNT(*) FROM ai_settings WHERE api_key <> ''",
            "attempts": "SELECT COUNT(*) FROM attempts",
            "grading reports": "SELECT COUNT(*) FROM grading_reports",
            "grading rubrics": "SELECT COUNT(*) FROM grading_rubrics",
            "grading jobs": "SELECT COUNT(*) FROM grading_jobs",
            "grading report contexts": "SELECT COUNT(*) FROM grading_report_contexts",
            "grading feedback": "SELECT COUNT(*) FROM grading_feedback",
            "question favorites": "SELECT COUNT(*) FROM question_favorites",
            "paper favorites": "SELECT COUNT(*) FROM paper_favorites",
            "text annotations": "SELECT COUNT(*) FROM text_annotations",
            "imports": "SELECT COUNT(*) FROM imports",
            "agent messages": "SELECT COUNT(*) FROM agent_messages",
            "agent memories": "SELECT COUNT(*) FROM agent_memories",
            "agent conversations": "SELECT COUNT(*) FROM agent_conversations",
            "training plan items": "SELECT COUNT(*) FROM training_plan_items",
            "agent feedback": "SELECT COUNT(*) FROM agent_feedback",
            "agent steps": "SELECT COUNT(*) FROM agent_steps",
            "agent runs": "SELECT COUNT(*) FROM agent_runs",
            "agent evaluations": "SELECT COUNT(*) FROM agent_eval_results",
            "agent weakness profiles": "SELECT COUNT(*) FROM agent_weakness_profile",
            "answers without organization groups": "SELECT COUNT(*) FROM reference_answers WHERE canonical_organization = ''",
            "private agent context chunks": "SELECT COUNT(*) FROM agent_context_chunks WHERE source_type IN ('attempt', 'grading_report', 'personal_note')",
            "non-public knowledge chunks": "SELECT COUNT(*) FROM agent_context_chunks WHERE source_type = 'knowledge' AND json_extract(metadata_json, '$.visibility') <> 'public'",
        }
        for label, sql in checks.items():
            count = conn.execute(sql).fetchone()[0]
            if count:
                raise RuntimeError(f"release database contains {count} {label}")
        answerless = [
            (row[0], row[1])
            for row in conn.execute(
                "SELECT id, prompt FROM questions q "
                "WHERE NOT EXISTS (SELECT 1 FROM reference_answers r WHERE r.question_id = q.id)"
            )
            if not is_preamble(row[1])
        ]
        if answerless:
            raise RuntimeError(
                f"release database contains {len(answerless)} questions without answers; "
                f"first: {answerless[0]}"
            )
        if conn.execute("SELECT COUNT(*) FROM agent_context_chunks").fetchone()[0]:
            raise RuntimeError("release database contains a prebuilt retrieval index")
        if conn.execute("SELECT COUNT(*) FROM agent_context_pending").fetchone()[0]:
            raise RuntimeError("release database contains pending retrieval-index work")
        derived_tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND (name = 'agent_context_fts' OR name LIKE 'agent_context_vec_%')"
            )
        ]
        if derived_tables:
            raise RuntimeError(f"release database contains derived retrieval tables: {derived_tables}")
        embedding_state = conn.execute(
            "SELECT embedding_model, embedding_dimensions FROM agent_context_index_state WHERE id = 1"
        ).fetchone()
        if tuple(embedding_state) != ("feature-hash-v1", 128):
            raise RuntimeError(f"release embedding state is not portable: {tuple(embedding_state)}")
        index_flags = conn.execute(
            "SELECT dirty, full_rebuild FROM agent_context_index_state WHERE id = 1"
        ).fetchone()
        if tuple(index_flags) != (1, 1):
            raise RuntimeError(f"release retrieval index should be deferred: {tuple(index_flags)}")
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("release database failed PRAGMA quick_check")

        invalid_types = [
            row[0]
            for row in conn.execute("SELECT DISTINCT question_type FROM questions")
            if row[0] not in ALLOWED_QUESTION_TYPES
        ]
        if invalid_types:
            raise RuntimeError(f"release database contains invalid question types: {invalid_types}")
        taxonomy_errors = []
        for row_id, question_type, prompt, requirements in conn.execute(
            "SELECT id, question_type, prompt, requirements FROM questions"
        ):
            inferred, reason = classify_question_type(
                prompt, requirements, fallback=question_type
            )
            # 规则没覆盖到某道题时 fallback 会保留人工标注的题型，这不算错；
            # 只有分类器给出了明确判断、且与该题题型冲突，才是真的归类错误。
            if reason != "未命中明确规则" and inferred != question_type:
                taxonomy_errors.append((row_id, question_type, inferred, reason))
        if taxonomy_errors:
            raise RuntimeError(
                f"release database contains {len(taxonomy_errors)} taxonomy mismatches; "
                f"first: {taxonomy_errors[0]}"
            )
        if conn.execute("SELECT COUNT(*) FROM questions WHERE question_type IN ('申论题', '贯彻执行', '文章写作')").fetchone()[0]:
            raise RuntimeError("release database contains legacy question types")
        if conn.execute("SELECT COUNT(*) FROM questions WHERE UPPER(question_code) LIKE 'SRNZ-%'").fetchone()[0]:
            raise RuntimeError("release database contains legacy SRNZ question codes")
        if conn.execute("SELECT COUNT(*) FROM papers WHERE region = '新疆兵团'").fetchone()[0]:
            raise RuntimeError("release database contains Xinjiang Bingtuan as a separate region")
        if conn.execute("SELECT COUNT(*) FROM papers WHERE paper_name LIKE '%广东省公考%' AND paper_name LIKE '%公安%' AND (region <> '广东' OR exam_type <> '广东省考')").fetchone()[0]:
            raise RuntimeError("release database misclassifies Guangdong police papers")
        if conn.execute("SELECT COUNT(*) FROM papers WHERE paper_name LIKE '%公安院校联考%' AND exam_type <> '公安院校联考'").fetchone()[0]:
            raise RuntimeError("release database misclassifies public security academy joint exam papers")

        text_columns = [
            ("questions", "source_note"),
            ("question_sources", "source_path"),
            ("reference_answers", "notes"),
        ]
        for table, column in text_columns:
            for (value,) in conn.execute(f"SELECT {column} FROM {table} WHERE {column} <> ''"):
                if PRIVATE_PATH.search(value or ""):
                    raise RuntimeError(f"private absolute path found in {table}.{column}")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Audit the public seed database.")
    parser.add_argument("path", nargs="?", default="data/gongkao_seed.sqlite3")
    args = parser.parse_args()
    audit_database(Path(args.path))
    print("Release database audit passed.")


if __name__ == "__main__":
    main()
