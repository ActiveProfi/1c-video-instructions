import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import tts

SECRET = "kie_secret_value_123"


class FakeKie:
    def __init__(self, states, create_error=None):
        self.states = list(states)
        self.create_error = create_error
        self.calls = []

    def __call__(self, method, url, api_key, body=None, timeout=60):
        self.calls.append((method, url, body))
        assert api_key == SECRET
        if url.endswith("/createTask"):
            if self.create_error:
                raise self.create_error
            return {"taskId": "task-1"}
        state = self.states.pop(0)
        if state == "success":
            return {"state": "success", "resultJson": json.dumps({"resultUrls": ["https://example.test/a.mp3"]}), "creditsConsumed": 1.2}
        if state == "fail":
            return {"state": "fail", "failCode": 501, "failMsg": "boom"}
        return {"state": state}


def fake_download(url, mp3):
    wav = mp3.with_name("src.wav")
    tts.synthesize_offline("Проверка.", wav)
    mp3.write_bytes(wav.read_bytes())


class KieTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        (self.root / ".env").write_text(f"KIE_API_KEY={SECRET}\n", encoding="utf-8")
        self.env = mock.patch.dict("os.environ", {"KIE_API_KEY": "", "KIE_TTS_MODEL": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def test_create_poll_download(self):
        fake, sleeps = FakeKie(["waiting", "generating", "success"]), []
        info = tts.synthesize_kie("Привет.", "", self.root / "k.wav", self.root, request=fake, download=fake_download, sleep=sleeps.append)
        self.assertTrue((self.root / "k.wav").exists())
        self.assertEqual(info["task_id"], "task-1")
        body = fake.calls[0][2]
        self.assertEqual(body["model"], tts.KIE_DEFAULT_MODEL)
        self.assertEqual(body["input"]["voice"], tts.KIE_DEFAULT_VOICE)
        self.assertEqual(sleeps, [2.0, 3.0])
        self.assertIn("taskId=task-1", fake.calls[1][1])

    def test_failed_task_raises(self):
        with self.assertRaises(tts.KieError) as caught:
            tts.synthesize_kie("x", "", self.root / "k.wav", self.root, request=FakeKie(["fail"]), download=fake_download, sleep=lambda s: None)
        self.assertFalse(caught.exception.retryable)

    def test_poll_timeout(self):
        with self.assertRaises(tts.KieError) as caught:
            tts.synthesize_kie("x", "", self.root / "k.wav", self.root, request=FakeKie(["queuing"] * 50), download=fake_download, sleep=lambda s: None, poll_timeout=5)
        self.assertTrue(caught.exception.retryable)

    def test_missing_key(self):
        (self.root / ".env").write_text("", encoding="utf-8")
        with self.assertRaises(tts.KieError) as caught:
            tts.synthesize_kie("x", "", self.root / "k.wav", self.root)
        self.assertEqual(caught.exception.code, 401)

    def test_lily_maps_to_bella(self):
        self.assertEqual(tts._voice_for("kie", "pFZP5JQG7iQjIQuC4Bku", None), tts.KIE_DEFAULT_VOICE)
        self.assertEqual(tts._voice_for("kie", "EkK5I93UQWFDigLMpZcX", None), "EkK5I93UQWFDigLMpZcX")

    def _chain(self, error):
        sleeps, log = [], tts.TtsLog()

        def kie(text, voice, dest, root):
            raise error

        with mock.patch.object(tts, "synthesize_kie", kie):
            entry = tts.speak_with_fallback("Итог.", self.root / "o.wav", "kie,offline", "", self.root, log=log, sleep=sleeps.append)
        return entry, sleeps, log

    def test_no_credits_is_not_retried(self):
        entry, sleeps, log = self._chain(tts.KieError(402, "Insufficient credits"))
        self.assertEqual(entry["provider"], "offline")
        self.assertEqual(sleeps, [])
        self.assertNotIn(SECRET, str(log.to_dict()))

    def test_rate_limit_is_retried(self):
        entry, sleeps, _ = self._chain(tts.KieError(429, "rate limit", retryable=True))
        self.assertEqual(entry["provider"], "offline")
        self.assertEqual(sleeps, [2.0, 4.0])
        self.assertEqual(entry["errors"][0]["error"], "Kie429")

    def test_http_200_with_error_code(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"code": 402, "msg": "Insufficient credits"}).encode()
        with mock.patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(tts.KieError) as caught:
                tts._kie_request("GET", "https://api.kie.ai/x", SECRET)
        self.assertEqual(caught.exception.code, 402)
        self.assertNotIn(SECRET, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
