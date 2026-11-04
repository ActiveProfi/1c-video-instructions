import tempfile
import unittest
import wave
from pathlib import Path

from pipeline import anchored
from pipeline.pronunciation import changed_terms, pronounce, revoice_needed, strip_stress

ACUTE = "\u0301"


class PronunciationTest(unittest.TestCase):
    def test_debit_credit_first_syllable(self):
        out = pronounce("Дебет и кредит, по дебету, кредита.")
        self.assertIn("Де" + ACUTE + "бет", out)
        self.assertIn("кре" + ACUTE + "дит", out)
        self.assertIn("де" + ACUTE + "бету", out)
        self.assertIn("кре" + ACUTE + "дита", out)

    def test_abbreviations_case_sensitive(self):
        out = pronounce("НДС в 1С, Дт 41 Кт 60")
        self.assertNotIn("НДС", out)
        self.assertIn("один эс", out)
        self.assertIn("де" + ACUTE + "бет 41", out)
        self.assertEqual(pronounce("ндс"), "ндс")

    def test_existing_marks_kept(self):
        text = "кре" + ACUTE + "дит"
        self.assertEqual(pronounce(text), text)

    def test_subtitles_have_no_marks(self):
        text = "Проводки: дебет 41, кредит 60."
        self.assertEqual(strip_stress(pronounce(text)).replace("ё", "е"), text)
        self.assertNotIn(ACUTE, strip_stress(pronounce(text)))

    def test_revoice_only_for_stress_terms(self):
        self.assertTrue(revoice_needed("Смотрим дебет счёта."))
        self.assertFalse(revoice_needed("Нажимаем кнопку Провести."))
        self.assertTrue(changed_terms("Сальдо по счёту"))


class BoxTest(unittest.TestCase):
    def frame(self, w, h, rect=None, value=200):
        data = bytearray([30]) * (w * h)
        if rect:
            l, t, r, b = rect
            for y in range(t, b):
                for x in range(l, r):
                    data[y * w + x] = value
        return bytes(data)

    def test_mask_box_finds_changed_field(self):
        w, h = 160, 90
        box = anchored.mask_box(self.frame(w, h), self.frame(w, h, (40, 20, 70, 28)), w, h)
        self.assertIsNotNone(box)
        l, t, r, b = box
        self.assertLessEqual(abs(l - 40), 1)
        self.assertLessEqual(abs(r - 70), 2)

    def test_mask_box_rejects_whole_screen_change(self):
        w, h = 160, 90
        self.assertIsNone(anchored.mask_box(self.frame(w, h), self.frame(w, h, (0, 0, 160, 90)), w, h))

    def test_stable_box_needs_agreement(self):
        self.assertEqual(anchored.stable_box([(10, 10, 50, 20), (11, 10, 50, 21), (10, 9, 51, 20)]), (10, 10, 50, 20))
        self.assertIsNone(anchored.stable_box([(10, 10, 50, 20), (100, 60, 150, 80)]))
        self.assertIsNone(anchored.stable_box([(10, 10, 50, 20)]))


class PhraseWavTest(unittest.TestCase):
    def test_phrase_replaced_without_stretch(self):
        rate = 24000
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
                w.writeframes(b"\x00\x00" * 2400 + b"\x10\x27" * rate + b"\x00\x00" * 2400)
            phrases = [anchored.Phrase("а", 0.0, 1.0, None), anchored.Phrase("б", 1.0, 2.0, None)]
            self.assertEqual(anchored.apply_phrase_wavs(phrases, {"1": str(path)}, rate), [1])
            self.assertAlmostEqual(phrases[1].end - phrases[1].start, 1.0 + 0.06 + 0.1, delta=0.25)
            self.assertEqual(anchored.apply_phrase_wavs(phrases, {"0": str(path)}, 16000 if False else rate, note="fallback"), [])


if __name__ == "__main__":
    unittest.main()
