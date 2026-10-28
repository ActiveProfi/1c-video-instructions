import tempfile
import unittest
from pathlib import Path

from pipeline.planner import UnsafeStep, build_plan, complete_ui_map
from pipeline.research import load_research
from pipeline.scriptgen import build_script
from pipeline.text_ru import speak_account, speak_rubles
from pipeline.tts import prepare_speech

ROOT = Path(__file__).resolve().parents[1]


def request(topic, **extra):
    payload = {
        "topic": topic,
        "configuration": "Бухгалтерия предприятия, редакция 3.0",
        "materials": [],
        "script": None,
        "ui_map": "fixtures/vat-usn/ui-map.json",
    }
    payload.update(extra)
    return payload


class NumbersTest(unittest.TestCase):
    def test_money_and_accounts(self):
        self.assertEqual(speak_rubles(2440), "две тысячи четыреста сорок рублей")
        self.assertEqual(speak_rubles(440), "четыреста сорок рублей")
        self.assertEqual(speak_rubles(244), "двести сорок четыре рубля")
        self.assertEqual(speak_rubles(1), "один рубль")
        self.assertEqual(speak_rubles(11), "одиннадцать рублей")
        self.assertEqual(speak_account("10.01"), "десять ноль один")
        self.assertEqual(speak_account("60.01"), "шестьдесят ноль один")


class ScriptAndPlanTest(unittest.TestCase):
    def test_sample_topic_builds_lesson_with_intro(self):
        data = request("Как учесть входной НДС при УСН в 1С:Бухгалтерия")
        research = load_research(ROOT, data)
        script = build_script(ROOT, data, research)
        self.assertEqual(script["source_kind"], "fixture")
        self.assertIn("Бухгалтерия", script["intro_narration"])
        self.assertIn("Главное", script["intro_narration"])
        self.assertIn("две тысячи четыреста сорок рублей", script["outro_narration"])
        self.assertIn("кредит", script["outro_narration"])
        self.assertNotIn("кре́дит", script["outro_narration"])
        self.assertGreaterEqual(len(script["scenes"]), 6)
        joined = " ".join(step["caption"] for scene in script["scenes"] for step in scene["steps"])
        self.assertIn("Провести", joined)

    def test_pronunciation_is_only_for_speech(self):
        spoken = prepare_speech("Кредит счёта шестьдесят.")
        self.assertNotEqual(spoken, "Кредит счёта шестьдесят.")
        self.assertTrue(spoken.lower().startswith("кре"))

    def test_unknown_topic_is_a_draft(self):
        data = request("Как завести нового контрагента")
        research = load_research(ROOT, data)
        script = build_script(ROOT, data, research)
        self.assertEqual(script["source_kind"], "draft")
        self.assertTrue(script["needs_review"])

    def test_materials_are_mentioned(self):
        with tempfile.TemporaryDirectory() as folder:
            note = Path(folder) / "zametki.txt"
            note.write_text("Поставщик в нашей базе называется Альфа.", encoding="utf-8")
            data = request(
                "Как учесть входной НДС при УСН",
                materials=[str(note)],
            )
            research = load_research(ROOT, data)
            script = build_script(ROOT, data, research)
        self.assertIn("ваши материалы", script["intro_narration"])

    def test_user_script_is_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "script.json"
            path.write_text(
                """
                {
                  "title": "Свой урок",
                  "intro_narration": "Уникальная фраза вступления про склад.",
                  "outro_narration": "Уникальная фраза концовки.",
                  "scenes": [
                    {
                      "id": "only",
                      "title": "Один шаг",
                      "narration": "Нажимаем свою кнопку.",
                      "steps": [
                        {"id": "go", "action": "click", "target": "my_button", "caption": "Своя кнопка"}
                      ]
                    }
                  ]
                }
                """,
                encoding="utf-8",
            )
            data = request("Как учесть входной НДС при УСН", script=str(path), ui_map=None)
            research = load_research(ROOT, data)
            script = build_script(ROOT, data, research)
            ui_map, generated = complete_ui_map(None, script)
            plan = build_plan(script, ui_map, generated)
        self.assertFalse(script["generated"])
        self.assertIn("Уникальная фраза вступления про склад.", script["intro_narration"])
        self.assertTrue(generated)
        self.assertEqual(plan["steps"][0]["automation_name"], "Своя кнопка")

    def test_report_sending_is_refused(self):
        script = {
            "title": "Плохой урок",
            "configuration": "Бухгалтерия предприятия, редакция 3.0",
            "intro_narration": "Вступление.",
            "outro_narration": "Концовка.",
            "scenes": [
                {
                    "id": "bad",
                    "title": "Отправка",
                    "narration": "Нельзя.",
                    "steps": [
                        {
                            "id": "send",
                            "action": "click",
                            "target": "send",
                            "caption": "Отправить в ФНС",
                        }
                    ],
                }
            ],
            "needs_review": True,
        }
        ui_map, generated = complete_ui_map(None, script)
        with self.assertRaises(UnsafeStep):
            build_plan(script, ui_map, generated)
