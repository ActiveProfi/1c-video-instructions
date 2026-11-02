import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pipeline.postprod import verify_media
from pipeline.proc import loads_json, run_captured, text_of

ROOT = Path(__file__).resolve().parents[1]


class SubprocessEncodingTest(unittest.TestCase):
    def test_byte_outside_cp1251_does_not_drop_stdout(self):
        # 0x98 нельзя прочитать как cp1251. Раньше поток чтения падал, и stdout становился None.
        code = "import sys; sys.stderr.buffer.write(bytes([0x98])); sys.stdout.buffer.write(b'{\"ok\": true}\\n')"
        result = run_captured([sys.executable, "-c", code])
        self.assertIsInstance(result.stdout, str)
        self.assertIsInstance(result.stderr, str)
        self.assertEqual(loads_json(result.stdout, "проверка")["ok"], True)
        self.assertIn("\ufffd", result.stderr)

    def test_empty_output_is_not_parsed_as_json(self):
        for empty in (None, "", "   "):
            with self.assertRaises(RuntimeError) as caught:
                loads_json(empty, "ffprobe")
            self.assertNotIsInstance(caught.exception.__cause__, TypeError)
        self.assertEqual(text_of(None), "")

    def test_cyrillic_path_with_double_space_roundtrips(self):
        with tempfile.TemporaryDirectory() as folder:
            lesson = Path(folder) / "Урок  1С"
            lesson.mkdir()
            video = lesson / "кадр.mp4"
            from pipeline.postprod import run_ffmpeg

            run_ffmpeg(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=160x120:d=0.4",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=22050:cl=mono",
                    "-shortest",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    str(video),
                ]
            )
            checked = verify_media(video)
            self.assertEqual(checked["video"], "h264")
            self.assertEqual(checked["audio"], "aac")
            self.assertGreater(checked["duration_seconds"], 0)

    def test_launchers_try_py_before_python(self):
        runner = (ROOT / "tools" / "Run-Py.cmd").read_text(encoding="utf-8")
        py_at = runner.find("py -3")
        python_at = runner.find("\npython ")
        self.assertGreater(py_at, 0)
        self.assertGreater(python_at, py_at)
        for name in ("Собрать учебный урок.cmd", "Записать урок в 1С.cmd"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("Run-Py.cmd", text)
            self.assertNotRegex(text, r"(?m)^python ")


class RawDecodeWouldFailTest(unittest.TestCase):
    def test_strict_cp1251_rejects_the_ffmpeg_byte(self):
        with self.assertRaises(UnicodeDecodeError):
            bytes([0x98]).decode("cp1251")
        self.assertEqual(bytes([0x98]).decode("utf-8", errors="replace"), "\ufffd")
        # И что сам subprocess без errors=replace на этом байте не обязан падать в UTF-8 локали
        # мы не проверяем: помощник всегда ставит errors=replace.
        completed = subprocess.run(
            [sys.executable, "-c", "import sys; sys.stderr.buffer.write(bytes([0x98]))"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertIn("\ufffd", completed.stderr)
        self.assertIsNotNone(json.dumps({"stderr": completed.stderr}))
