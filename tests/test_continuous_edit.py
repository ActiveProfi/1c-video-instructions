import json
import shutil
import tempfile
import unittest
from pathlib import Path

from pipeline.continuous import (
    Step,
    detect_freezes,
    edit_continuous,
    load_journal,
    parse_freezes,
    plan_segment,
    probe_video,
    steps_from_cues,
)
from pipeline.postprod import run_ffmpeg, wav_duration
from pipeline.tts import synthesize_offline

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_wav(path: Path, seconds: float) -> None:
    run_ffmpeg(["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-ar", "24000", "-ac", "1", str(path)])


def make_raw(path: Path) -> None:
    """0–3 с движение, 3–8 с стоп-кадр, 8–12 с движение."""
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=4",
            "-filter_complex",
            "[0:v]tpad=stop_mode=clone:stop_duration=5[a];[1:v]setpts=PTS-STARTPTS[b];[a][b]concat=n=2:v=1:a=0,format=yuv420p[v]",
            "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", str(path),
        ]
    )


class PlanTest(unittest.TestCase):
    def test_parse_freezedetect_log(self):
        log = (
            "[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 3.04\n"
            "[freezedetect @ 0x1] lavfi.freezedetect.freeze_duration: 4.96\n"
            "[freezedetect @ 0x1] lavfi.freezedetect.freeze_end: 8\n"
            "[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 11\n"
        )
        self.assertEqual(parse_freezes(log, 12.0), [(3.04, 8.0), (11.0, 12.0)])

    def test_pause_is_cut_and_short_action_is_held(self):
        plan = plan_segment("a", [(0, 8)], [(3, 8)], voice=6.0, total=12)
        self.assertEqual(plan.keep, [(0, 3.4), (7.6, 8)])
        self.assertAlmostEqual(plan.removed, 4.2, places=2)
        self.assertEqual(plan.speed, 1.0)
        self.assertAlmostEqual(plan.duration, 6.35, places=2)
        self.assertGreater(plan.hold, 2.5)

    def test_long_action_is_sped_up_but_not_beyond_limit(self):
        plan = plan_segment("b", [(0, 10)], [], voice=3.0, total=10, max_speed=2.0)
        self.assertEqual(plan.speed, 2.0)
        self.assertAlmostEqual(plan.duration, 5.0, places=2)
        self.assertTrue(plan.notes)
        mild = plan_segment("c", [(0, 4)], [], voice=3.0, total=10)
        self.assertAlmostEqual(mild.duration, 3.35, places=2)
        self.assertAlmostEqual(mild.speed, 4 / 3.35, places=3)

    def test_fully_frozen_window_keeps_a_short_frame(self):
        plan = plan_segment("d", [(4, 7)], [(3, 8)], voice=2.0, total=12)
        self.assertEqual(len(plan.keep), 1)
        self.assertLessEqual(plan.active, 1.21)

    def test_live_cues_become_windows(self):
        cues = [
            {"step": "s2", "start": 5.0, "narration": "Два.", "legal_note": "", "wav": "b.wav"},
            {"step": "s1", "start": 1.0, "narration": "Один.", "legal_note": "x", "wav": "a.wav", "end": 4.0},
        ]
        steps = steps_from_cues(cues, 9.0)
        self.assertEqual([s.id for s in steps], ["s1", "s2"])
        self.assertEqual(steps[0].windows, [(1.0, 4.0)])
        self.assertEqual(steps[1].windows, [(5.0, 9.0)])


@unittest.skipUnless(HAS_FFMPEG, "нужен ffmpeg")
class ContinuousRenderTest(unittest.TestCase):
    def test_synthetic_recording_is_cut_fitted_and_subtitled(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = root / "raw-screen.mp4"
            make_raw(raw)
            self.assertAlmostEqual(probe_video(raw)["duration"], 12.0, delta=0.2)
            freezes = detect_freezes(raw, 12.0)
            self.assertEqual(len(freezes), 1)
            self.assertAlmostEqual(freezes[0][0], 3.0, delta=0.3)
            self.assertAlmostEqual(freezes[0][1], 8.0, delta=0.3)
            make_wav(root / "a.wav", 2.0)
            make_wav(root / "b.wav", 5.0)
            synthesize_offline("Вступление.", root / "intro.wav")
            intro = root / "intro.mp4"
            run_ffmpeg(["-f", "lavfi", "-i", "color=c=navy:s=640x360:d=1.5", "-i", str(root / "intro.wav"), "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(intro)])
            steps = [
                Step("open", "Открываем раздел. Видим список.", root / "a.wav", [(0.0, 8.0)], "проверка"),
                Step("fill", "Заполняем поле.", root / "b.wav", [(8.0, 12.0)]),
            ]
            report = edit_continuous(raw, steps, root / "out", intro=intro, outro=intro)
            video = Path(report["video"])
            self.assertTrue(video.exists())
            info = probe_video(video)
            self.assertEqual((info["width"], info["height"]), (320, 240))
            first, second = report["segments"]
            self.assertGreater(first["removed"], 3.0)
            self.assertAlmostEqual(first["duration"], 2.35, delta=0.05)
            self.assertAlmostEqual(second["duration"], 5.35, delta=0.05)
            expected = 2 * probe_video(intro)["duration"] + first["duration"] + second["duration"]
            self.assertAlmostEqual(report["duration_seconds"], expected, delta=0.6)
            self.assertLess(report["duration_seconds"], 12.0 + 2 * 1.5)
            srt = (root / "out" / "subtitles.srt").read_text(encoding="utf-8")
            self.assertIn("Почему так: проверка", srt)
            self.assertIn("Заполняем поле.", srt)
            self.assertTrue((root / "out" / "edit-report.json").exists())

    def test_journal_file_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            make_raw(root / "raw.mp4")
            make_wav(root / "v.wav", 1.0)
            (root / "edit-journal.json").write_text(
                json.dumps({"raw": "raw.mp4", "steps": [{"id": "x", "narration": "Шаг.", "wav": "v.wav", "windows": [[8, 9], [10, 11]]}]}),
                encoding="utf-8",
            )
            raw, steps, _ = load_journal(root / "edit-journal.json")
            report = edit_continuous(raw, steps, root / "out")
            self.assertAlmostEqual(report["segments"][0]["active"], 2.0, places=2)
            self.assertAlmostEqual(report["duration_seconds"], 2.0 / report["segments"][0]["speed"], delta=0.3)


if __name__ == "__main__":
    unittest.main()
