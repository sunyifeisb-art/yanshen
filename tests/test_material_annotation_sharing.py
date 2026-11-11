import json
import tempfile
import unittest
from pathlib import Path

from gongkao.db import connect, prepare_user_database
from gongkao.services.personal_records import paper_material_annotations, save_text_annotations

SEED = Path(__file__).resolve().parent.parent / "data" / "gongkao_seed.sqlite3"


class MaterialAnnotationSharingTest(unittest.TestCase):
    def test_same_paper_questions_share_marks_and_clear_together(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "sharing.sqlite3"
            prepare_user_database(db_path, SEED)
            with connect(db_path) as conn:
                target = conn.execute(
                    """SELECT q1.id AS first_id, q2.id AS second_id,
                              q1.paper_id, m.material_number
                         FROM questions q1
                         JOIN questions q2 ON q2.paper_id = q1.paper_id AND q2.id > q1.id
                         JOIN paper_materials m ON m.paper_id = q1.paper_id
                        ORDER BY q1.id, q2.id, m.material_number LIMIT 1"""
                ).fetchone()
                first = target["first_id"]
                second = target["second_id"]
                number = target["material_number"]
                paper_id = target["paper_id"]
                save_text_annotations(
                    conn,
                    "material",
                    [{"start": 0, "end": 2, "color": "yellow", "note": "重点"}],
                    "shared-hash",
                    question_id=second,
                    material_number=number,
                )
                shared = paper_material_annotations(conn, paper_id)[number]
                self.assertEqual(json.loads(shared["annotations_json"])[0]["note"], "重点")
                self.assertEqual(shared["text_hash"], "shared-hash")
                self.assertEqual(
                    conn.execute("SELECT question_id FROM text_annotations WHERE target_type = 'material'").fetchone()[0],
                    first,
                )
                save_text_annotations(conn, "material", [], question_id=first, material_number=number)
                self.assertNotIn(number, paper_material_annotations(conn, paper_id))

    def test_older_question_scoped_marks_are_combined(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "legacy.sqlite3"
            prepare_user_database(db_path, SEED)
            with connect(db_path) as conn:
                target = conn.execute(
                    """SELECT q1.id AS first_id, q2.id AS second_id,
                              q1.paper_id, m.material_number
                         FROM questions q1
                         JOIN questions q2 ON q2.paper_id = q1.paper_id AND q2.id > q1.id
                         JOIN paper_materials m ON m.paper_id = q1.paper_id
                        ORDER BY q1.id, q2.id, m.material_number LIMIT 1"""
                ).fetchone()
                number = target["material_number"]
                for question_id, start, color in (
                    (target["first_id"], 0, "yellow"),
                    (target["second_id"], 4, "blue"),
                ):
                    conn.execute(
                        """INSERT INTO text_annotations
                           (annotation_key, target_type, question_id, material_number, annotations_json)
                           VALUES (?, 'material', ?, ?, ?)""",
                        (
                            f"material:{question_id}:{number}",
                            question_id,
                            number,
                            json.dumps([{"start": start, "end": start + 2, "color": color}]),
                        ),
                    )
                marks = json.loads(paper_material_annotations(conn, target["paper_id"])[number]["annotations_json"])
                self.assertEqual({item["color"] for item in marks}, {"yellow", "blue"})
                save_text_annotations(
                    conn, "material", marks,
                    question_id=target["second_id"], material_number=number,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM text_annotations WHERE target_type = 'material'").fetchone()[0],
                    1,
                )


if __name__ == "__main__":
    unittest.main()
