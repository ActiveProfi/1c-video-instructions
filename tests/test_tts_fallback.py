import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import tts


class NoAudioReceived(Exception):
    pass


class WSServerHandshakeError(Exception):
    pass


def fake_writer(label):
    def write(text, dest, *args, **kwargs):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        tts.synthesize_offline(label + text, Path(dest))
    return write


class ChainTest(unittest.TestCase):
    def test_parse_chain(self):
        self.assertEqual(tts.parse_chain(None), ["elevenlabs", "kie", "edge", "offline"])
        self.assertEqual(tts.parse_chain("elevenlabs, edge>offline,edge"), ["elevenlabs", "edge", "offline"])
        with self.assertRaises(RuntimeError):
            tts.parse_chain("edge,yandex")


class FallbackTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.sleeps = []

    def tearDown(self):
        self.folder.cleanup()

    def test_edge_retries_with_growing_pause_then_succeeds(self):
        calls = {"n": 0}

        def edge(text, voice, dest):
            calls["n"] += 1
            if calls["n"] < 3:
                raise WSServerHandshakeError("403")
            fake_writer("e")(text, dest)

        log = tts.TtsLog()
        with mock.patch.object(tts, "synthesize_edge", edge):
            entry = tts.speak_with_fallback("Привет.", self.root / "a.wav", "edge,offline", "fr-FR-RemyMultilingualNeural", self.root, log=log, sleep=self.sleeps.append, base_delay=2)
        self.assertEqual(entry["provider"], "edge")
        self.assertEqual(entry["attempts"], 3)
        self.assertEqual(self.sleeps, [2, 4])
        self.assertFalse(entry["fallback"])
        self.assertIsNone(log.mixed_warning())

    def test_no_audio_is_not_retried_and_falls_back_per_fragment(self):
        def edge(text, voice, dest):
            if "кре" in text:
                Path(dest).with_suffix(".mp3").write_bytes(b"")
                raise NoAudioReceived("No audio was received")
            fake_writer("e")(text, dest)

        log = tts.TtsLog()
        with mock.patch.object(tts, "synthesize_edge", edge), mock.patch.object(tts, "synthesize_elevenlabs", lambda t, v, d, r: fake_writer("x")(t, d)):
            tts.speak_with_fallback("Дебет.", self.root / "v1.wav", "edge,elevenlabs,offline", "fr-FR-RemyMultilingualNeural", self.root, log=log, sleep=self.sleeps.append)
            second = tts.speak_with_fallback("кре\u0301дит.", self.root / "v2.wav", "edge,elevenlabs,offline", "fr-FR-RemyMultilingualNeural", self.root, log=log, sleep=self.sleeps.append)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(second["provider"], "elevenlabs")
        self.assertEqual(second["voice"], tts.ELEVENLABS_DEFAULT_VOICE)
        self.assertEqual(second["errors"][0]["error"], "NoAudioReceived")
        self.assertTrue((self.root / "v2.wav").exists())
        self.assertFalse(any(p.stat().st_size == 0 for p in self.root.glob("v2*")))
        warning = log.mixed_warning()
        self.assertIn("смешаны голоса", warning)
        self.assertIn("v2", warning)
        log.write(self.root / "tts-log.json")
        self.assertIn("elevenlabs", (self.root / "tts-log.json").read_text(encoding="utf-8"))

    def test_last_resort_is_offline_and_key_is_never_logged(self):
        def edge(text, voice, dest):
            raise WSServerHandshakeError("403")

        def eleven(text, voice, dest, root):
            raise RuntimeError("ElevenLabs не озвучил фрагмент. Проверьте ключ, голос и остаток кредитов.")

        (self.root / ".env").write_text("ELEVENLABS_API_KEY=sk_secret_value\n", encoding="utf-8")
        log = tts.TtsLog()
        with mock.patch.object(tts, "synthesize_edge", edge), mock.patch.object(tts, "synthesize_elevenlabs", eleven), mock.patch.dict("os.environ", {"KIE_API_KEY": ""}):
            chosen, warning = tts.speak_clip("Итог.", self.root / "o.wav", "auto", "fr-FR-RemyMultilingualNeural", self.root, log=log, sleep=self.sleeps.append)
        self.assertEqual(chosen, "offline")
        self.assertIn("elevenlabs", warning)
        self.assertEqual(self.sleeps, [2.0, 4.0])
        self.assertEqual(log.entries[0]["attempts"], 6)
        self.assertEqual([e["provider"] for e in log.entries[0]["errors"]][:3], ["elevenlabs", "kie", "edge"])
        self.assertNotIn("sk_secret_value", str(log.to_dict()))

    def test_all_providers_failing_raises_clear_error(self):
        with mock.patch.object(tts, "synthesize_offline", side_effect=OSError("disk")):
            with self.assertRaises(RuntimeError) as caught:
                tts.speak_with_fallback("Текст.", self.root / "z.wav", "offline", "", self.root, sleep=self.sleeps.append)
        self.assertIn("не озвучен", str(caught.exception))

    def test_explicit_provider_keeps_old_behaviour(self):
        chosen, warning = tts.speak_clip("Проверка.", self.root / "p.wav", "offline", "", self.root)
        self.assertEqual((chosen, warning), ("offline", None))

    def test_chain_from_environment(self):
        with mock.patch.dict("os.environ", {"LESSON_TTS_CHAIN": "offline"}):
            chosen, _ = tts.speak_clip("Проверка.", self.root / "q.wav", "auto", "", self.root)
        self.assertEqual(chosen, "offline")


if __name__ == "__main__":
    unittest.main()
