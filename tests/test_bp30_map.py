import unittest
from pathlib import Path

from pipeline.bp30_map import click_steps, get_entry, load_map
from pipeline.lesson_yaml import action_plan, load_lesson
from pipeline.ui_session import FakeSession

ROOT = Path(__file__).resolve().parents[1]


class MapLoadTest(unittest.TestCase):
    def test_map_opens_and_names_the_official_paths(self):
        data = load_map()
        nomenclature = [step["name"] for step in click_steps("catalog.nomenclature", data)]
        self.assertEqual(nomenclature, ["Справочники", "Номенклатура"])
        dopp = get_entry("purchases.dopp", data)
        self.assertEqual(dopp["confidence"], "high")
        self.assertIn("Внешняя торговля", dopp["source_note"] + dopp["title"])
        joined = " ".join(step["name"] for step in click_steps("purchases.dopp", data))
        self.assertIn("Покупки", joined)
        self.assertIn("ДОПП", joined)
        statements = " ".join(step["name"] for step in click_steps("bank.statements", data))
        self.assertIn("Банк и касса", statements)
        self.assertIn("Банковские выписки", statements)
        self.assertIn("Поступление (акты, накладные, УПД)", get_entry("purchases.receipt", data)["title"])
        self.assertIn("Закрытие месяца", get_entry("ops.month_close", data)["title"])
        self.assertTrue(data["buttons"]["send"]["forbidden"])
        self.assertTrue(data["buttons"]["delete"]["forbidden"])

    def test_every_lesson_step_points_at_a_map_key(self):
        data = load_map()
        for name in ("lesson1.yaml", "lesson2.yaml", "lesson3.yaml"):
            lesson = load_lesson(ROOT / "lessons" / name)
            for scene in lesson["scenes"]:
                self.assertIn(scene.get("ui_ref"), data["entries"], scene.get("menu_path"))
                if scene.get("navigate", True):
                    self.assertTrue(click_steps(scene["ui_ref"], data))

    def test_alias_is_used_when_the_primary_caption_is_absent(self):
        lesson = load_lesson(ROOT / "lessons" / "lesson1.yaml")
        scene = next(item for item in lesson["scenes"] if item["id"] == "6")
        actions = [step for step in action_plan(scene) if step["name"].startswith("Документы о предстоящей")]
        self.assertTrue(actions)
        self.assertIn("Документ о предстоящей поставке товаров", actions[0]["aliases"])
        session = FakeSession(
            ["Покупки", "Документ о предстоящей поставке товаров"],
            texts=["Альфа ООО"],
        )
        from pipeline.bp30_map import find_first

        found = find_first(session, actions[0], 0.05)
        self.assertEqual(found.name, "Документ о предстоящей поставке товаров")

    def test_send_stays_show_only(self):
        lesson = load_lesson(ROOT / "lessons" / "lesson1.yaml")
        scene = lesson["scenes"][8]
        kinds = {step["name"]: step["kind"] for step in action_plan(scene)}
        for name, kind in kinds.items():
            if "Отправ" in name or "Выгруз" in name:
                self.assertEqual(kind, "show")
