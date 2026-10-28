import tempfile
import unittest
from pathlib import Path

from pipeline.lesson_yaml import action_plan, is_forbidden_name, load_lesson
from pipeline.live import dry_run, gdigrab_command
from pipeline.tnved import assess_tnved, read_smartphone_tnved
from pipeline.ui_session import EscWatcher, FakeSession, StopRequested, guard_name
from pipeline.__main__ import main

ROOT = Path(__file__).resolve().parents[1]
HOME = [
    "Главное",
    "Руководителю",
    "Банк и касса",
    "Продажи",
    "Покупки",
    "Склад",
    "Производство",
    "ОС и НМА",
    "Зарплата и кадры",
    "Операции",
    "Отчеты",
    "Справочники",
    "Администрирование",
    "Помощь",
    "Альфа ООО",
]


class LessonFilesTest(unittest.TestCase):
    def test_three_lessons_keep_speech_and_reasons(self):
        expected = {"lesson1.yaml": 17, "lesson2.yaml": 11, "lesson3.yaml": 12}
        for name, count in expected.items():
            lesson = load_lesson(ROOT / "lessons" / name)
            self.assertEqual(len(lesson["scenes"]), count)
            self.assertTrue(lesson["card_title"].startswith("Урок"))
            self.assertIn("Альфа", lesson["organization"])
            self.assertTrue(lesson["intro_narration"])
            self.assertTrue(lesson["outro_narration"])
            for scene in lesson["scenes"]:
                self.assertGreater(len(scene["narration"]), 40)
                self.assertGreater(len(scene["legal_note"]), 20)
                for action in action_plan(scene):
                    if is_forbidden_name(action["name"]):
                        self.assertEqual(action["kind"], "show")

    def test_lesson1_opening_and_close_are_intact(self):
        lesson = load_lesson(ROOT / "lessons" / "lesson1.yaml")
        self.assertTrue(lesson["scenes"][0]["narration"].startswith("Прежде чем начать"))
        self.assertIn("101-ФЗ", lesson["scenes"][0]["legal_note"])
        self.assertIn("Китай", lesson["outro_narration"])
        send = lesson["scenes"][8]
        self.assertTrue(send["show_only"])
        self.assertIn("не отправляем", send["narration"])


class DryRunTest(unittest.TestCase):
    def test_home_screen_is_searched_without_clicks(self):
        lesson = load_lesson(ROOT / "lessons" / "lesson1.yaml")
        session = FakeSession(HOME, texts=["Альфа ООО"])
        with tempfile.TemporaryDirectory() as folder:
            report = dry_run(session, lesson, timeout=0.02)
        self.assertEqual(session.clicks, [])
        self.assertEqual(session.fills, [])
        self.assertFalse(report["recorded"])
        self.assertFalse(report["posted"])
        self.assertIn("Покупки", report["found"])
        self.assertIn("Главное", report["found"])
        self.assertTrue(report["organization_seen"])
        self.assertTrue(report["missing"])
        self.assertIn("Провести", report["missing"])

    def test_escape_stops_the_pass(self):
        lesson = load_lesson(ROOT / "lessons" / "lesson1.yaml")
        watcher = EscWatcher()
        watcher.request_stop()
        session = FakeSession(HOME, texts=["Альфа ООО"], watcher=watcher)
        with self.assertRaises(StopRequested):
            dry_run(session, lesson, timeout=0.02)

    def test_delete_is_refused(self):
        with self.assertRaises(RuntimeError):
            guard_name("Удалить")


class TnvedTest(unittest.TestCase):
    def test_expected_code_is_quiet(self):
        report = assess_tnved("8517 13 000 0")
        self.assertTrue(report["ok"])
        self.assertIsNone(report["warning"])

    def test_other_code_warns_about_the_fee(self):
        report = assess_tnved("8517 12 000 0")
        self.assertFalse(report["ok"])
        self.assertIn("73 860", report["warning"])
        self.assertIn("1637", report["warning"])
        self.assertIn("8517 13 000 0", report["warning"])

    def test_reader_uses_the_card_and_does_not_post(self):
        session = FakeSession(
            ["Справочники", "Номенклатура", "Смартфон", "Код ТН ВЭД"],
            values={"Код ТН ВЭД": "8517 12 000 0"},
        )
        code = read_smartphone_tnved(session)
        self.assertEqual(code, "8517 12 000 0")
        self.assertNotIn("Провести", session.clicks)
        self.assertNotIn("Удалить", session.clicks)


class LaunchAndGrabTest(unittest.TestCase):
    def test_new_commands_call_py_launcher(self):
        for name in ("Проверить ТН ВЭД.cmd", "Пробный прогон урока 1.cmd", "Записать урок 1 в 1С.cmd"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("py -3", text)
            self.assertIn("%~dp0", text)
            self.assertNotRegex(text, r"(?m)^python ")

    def test_live_command_requires_confirmation(self):
        self.assertEqual(main(["live", "--lesson", "lessons/lesson1.yaml", "--dry-run"]), 1)

    def test_grab_keeps_spaced_cyrillic_path(self):
        path = Path("C:/Уроки  1С/raw-screen.mp4")
        command = gdigrab_command("ffmpeg", (0, 0, 1280, 720), path)
        self.assertIn("gdigrab", command)
        self.assertIn(str(path), command)
        self.assertEqual(command[command.index("-video_size") + 1], "1280x720")
