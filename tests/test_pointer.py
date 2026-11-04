import math
import re
import tempfile
import unittest
from pathlib import Path

from pipeline import anchored, pointer


def beat(phrases, voice_at=0.0, shown=0.0, action=None, duration=None):
    items = []
    t = 0.0
    for text, length in phrases:
        items.append(anchored.Phrase(text, t, t + length))
        t += length
    total = duration or voice_at + t + 0.5
    return anchored.Beat(step=0, index=0, action=action, raw_start=0.0, raw_end=1.0, keep=[], shown=shown, voice_at=voice_at,
                         phrases=items, voice=t, hold=total - shown, duration=total)


def evaluate(expr: str, t: float) -> float:
    env = {"t": t, "clip": lambda x, a, b: max(a, min(b, x)), "lt": lambda a, b: a < b}
    py = expr
    py = re.sub(r"if\(", "_if(", py)
    env["_if"] = lambda c, a, b: a if c else b
    return float(eval(py, {"__builtins__": {}}, env))


class AssignTest(unittest.TestCase):
    def test_cursor_on_element_for_whole_phrase(self):
        b = beat([("Указываем количество.", 2.0), ("Проверяем ставку НДС: двадцать два процента.", 3.0)], shown=1.0, action=0)
        notes = pointer.assign_points([pointer.Point("Ставка", "ставку ндс", (100, 100, 140, 120))], [b], ["количество"])
        self.assertEqual(notes, [])
        p = b.points[0]
        self.assertEqual(p["phrase"], 1)
        self.assertAlmostEqual(p["from"], 2.0, places=3)
        self.assertAlmostEqual(p["arrive"], 2.0, places=3)  # курсор успевает приехать к началу фразы
        self.assertEqual(p["covered"], 1.0)

    def test_not_before_live_video_ends(self):
        b = beat([("Договор подставился.", 2.0)], shown=1.5)
        pointer.assign_points([pointer.Point("Договор", "договор", (0, 0, 10, 10))], [b], [])
        self.assertGreaterEqual(b.points[0]["arrive"], 1.5 + pointer.TRAVEL - 1e-6)
        self.assertLess(b.points[0]["covered"], 1.0)

    def test_two_points_in_one_phrase_split_by_word(self):
        b = beat([("Проверим номер, дату и суммы.", 3.0)])
        pointer.assign_points([pointer.Point("Номер", "проверим номер", (0, 0, 10, 10)),
                               pointer.Point("Суммы", "и суммы", (50, 50, 90, 60))], [b], [])
        first, second = b.points
        self.assertLessEqual(first["to"], second["from"] + 1e-6)
        self.assertGreater(second["from"], 1.0)

    def test_missing_word_reported(self):
        b = beat([("Нажимаем ОК.", 1.0)])
        notes = pointer.assign_points([pointer.Point("Итог", "всего", (0, 0, 10, 10))], [b], [])
        self.assertTrue(notes and "не найдено" in notes[0])
        self.assertEqual(b.points, [])

    def test_path_reaches_element_and_returns(self):
        b = beat([("Цена у нас уже с налогом.", 2.0)], duration=3.0)
        pointer.assign_points([pointer.Point("Цена", "цена", (200, 100, 300, 120))], [b], [])
        appear, frames = pointer.keyframes(b, (20, 30))
        x = pointer.path_expr(frames, 1, 0)
        y = pointer.path_expr(frames, 2, 0)
        p = b.points[0]
        mid = (p["arrive"] + p["to"]) / 2
        self.assertEqual((evaluate(x, mid), evaluate(y, mid)), (250, 110))
        self.assertEqual((evaluate(x, b.duration), evaluate(y, b.duration)), (20, 30))  # вернулся к настоящему
        self.assertEqual((evaluate(x, appear), evaluate(y, appear)), (20, 30))


class CursorPictureTest(unittest.TestCase):
    W, H = 200, 120

    def frame_with_cursor(self, cx=100, cy=60):
        data = bytearray()
        for y in range(self.H):
            for x in range(self.W):
                r = g = b = 255
                if 20 <= y < 30:
                    r = g = b = 40  # «текст»
                d = math.hypot(x - cx, y - cy)
                a = max(0.0, 0.28 * (1 - d / 40))
                r, g, b = [int(c * (1 - a) + k * a) for c, k in zip((r, g, b), pointer.GLOW_RGB)]
                if 0 <= x - (cx - 6) <= (y - (cy - 12)) * 0.6 and 0 <= y - (cy - 12) < 18:
                    r = g = b = 0
                data += bytes((r, g, b))
        return bytes(data)

    def test_glow_found_and_removed(self):
        frame = self.frame_with_cursor()
        spot = pointer.find_glow(frame, self.W, self.H)
        self.assertIsNotNone(spot)
        self.assertLess(math.hypot(spot[0] - 100, spot[1] - 60), 20)
        tip = pointer.arrow_tip(frame, self.W, self.H, spot)
        region = pointer._region(tip[0] + 6, tip[1] + 12, self.W, self.H, side=80)
        clean = pointer.remove_cursor(frame, self.W, self.H, spot, tip, region)
        blue = sum(1 for i in range(0, len(clean), 3) if clean[i + 2] - clean[i] > 25)
        self.assertLess(blue, len(clean) // 3 * 0.02)

    def test_no_cursor_no_glow(self):
        frame = bytes([255]) * (self.W * self.H * 3)
        self.assertIsNone(pointer.find_glow(frame, self.W, self.H))

    def test_cursor_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            png = pointer.cursor_png(Path(tmp) / "c.png")
            from PIL import Image

            im = Image.open(png)
            self.assertEqual(im.getpixel((pointer.HOT[0] + 2, pointer.HOT[1] + 6))[:3], (0, 0, 0))


class JournalTest(unittest.TestCase):
    def test_point_at_from_json(self):
        pts = pointer.points_from_json([{"label": "Итог", "say": "всего", "rect": [1, 2, 3, 4]}, {"say": "x", "element": "Сумма"}])
        self.assertEqual(pts[0].rect, (1, 2, 3, 4))
        self.assertIsNone(pts[1].rect)
        self.assertEqual(pts[1].element, "Сумма")

    def test_books_points_words_exist_in_narration(self):
        import json

        from tools.books_edit_journal import BOOKS_POINTS

        spec = json.loads((Path(__file__).resolve().parents[1] / "scenarios" / "books-creation-video.json").read_text(encoding="utf-8-sig"))
        for index, points in BOOKS_POINTS.items():
            text = spec["scenes"][index]["speech"].lower().replace("ё", "е")
            for p in points:
                self.assertIn(p["say"].lower().replace("ё", "е"), text, p["label"])


if __name__ == "__main__":
    unittest.main()


class LivePointTest(unittest.TestCase):
    def test_uia_rect_relative_to_window(self):
        from pipeline import live
        from pipeline.ui_session import Found

        class Session:
            def find(self, name, timeout=0, role="", within="", index=None):
                if name != "Сумма":
                    raise LookupError(name)
                return Found(name=name, left=150, top=230, right=250, bottom=250)

            def window_rect(self):
                return (100, 200, 900, 800)

        rec = live._point_record(Session(), {"say": "всего", "element": "Сумма"})
        self.assertEqual(rec["rect"], [50, 30, 150, 50])
        self.assertIsNone(live._point_record(Session(), {"say": "всего", "element": "Нет такого"}))
