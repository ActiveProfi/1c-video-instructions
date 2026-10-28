import tempfile
import unittest
from pathlib import Path

from PIL import Image

from pipeline.run import execute

ROOT = Path(__file__).resolve().parents[1]


class PipelineEndToEndTest(unittest.TestCase):
    def test_mock_lesson_produces_video(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "Урок  1С"
            result = execute(
                ROOT,
                {
                    "topic": "Как учесть входной НДС при упрощённой системе налогообложения (УСН) в 1С:Бухгалтерия",
                    "configuration": "Бухгалтерия предприятия, редакция 3.0",
                    "materials": [],
                    "script": None,
                    "ui_map": "fixtures/vat-usn/ui-map.json",
                    "voice": "ru-RU-DmitryNeural",
                    "tts": "offline",
                    "driver": "mock",
                },
                out,
                driver_name="mock",
                tts_name="offline",
                voice="ru-RU-DmitryNeural",
                confirm=False,
                dry_run=False,
            )
            video = Path(result["video"])
            self.assertTrue(video.exists())
            self.assertGreater(video.stat().st_size, 20_000)
            self.assertEqual(result["status"], "verified")
            self.assertTrue(result["card_title"].startswith("Урок:"))
            self.assertGreater(result["duration_seconds"], 8)
            self.assertEqual(result["codecs"]["video"], "h264")
            self.assertEqual(result["codecs"]["audio"], "aac")
            self.assertIn("highlight", result["annotations"])
            self.assertIn("intro", result["annotations"])
            self.assertIn("outro", result["annotations"])
            subtitles = Path(result["subtitles"]).read_text(encoding="utf-8").lower().replace("ё", "е")
            self.assertIn("упрощен", subtitles)
            self.assertIn("бухгалтерия", subtitles)
            script = (out / "script.json").read_text(encoding="utf-8")
            self.assertIn("кредит", script)
            self.assertNotIn("кре́дит", script)
            card = Image.open(out / "build" / "intro-sub.png")
            # Тёмная заставка, не пустой кадр.
            pixel = card.getpixel((40, 40))
            self.assertGreater(pixel[2], pixel[0])
            annotated = Image.open(out / "build" / "open_main.png")
            self.assertTrue(_has_orange(annotated), "на кадре нет оранжевой рамки или стрелки")
            self.assertEqual(annotated.getpixel((20, annotated.height - 12))[2], 51)


def _has_orange(image: Image.Image) -> bool:
    width, height = image.size
    # Верхняя кромка рамки у кнопки «Главное» и полоса субтитров не считаются.
    for y in range(70, 130):
        for x in range(8, 220):
            red, green, blue = image.getpixel((x, y))
            if red > 180 and 80 < green < 180 and blue < 60:
                return True
    return False
