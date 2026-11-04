import array
import json
import shutil
import tempfile
import unittest
import wave
from pathlib import Path

from pipeline.anchored import (
    Action,
    AnchoredStep,
    Phrase,
    beat_audio,
    default_anchors,
    edit_anchored,
    load_anchored_journal,
    plan_step,
    read_wav,
    split_phrases,
    steps_from_live_cues,
)
from pipeline.postprod import run_ffmpeg

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
RATE = 24000


def tone_wav(path: Path, parts: list[tuple[str, float]]) -> list[int]:
    """parts: ("tone"|"pause", секунды). Возвращает сэмплы."""
    import math

    data = array.array("h")
    for kind, seconds in parts:
        n = int(seconds * RATE)
        if kind == "tone":
            data.extend(int(8000 * math.sin(2 * math.pi * 300 * i / RATE)) for i in range(n))
        else:
            data.extend([0] * n)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(data.tobytes())
    return list(data)


class PhraseTest(unittest.TestCase):
    def test_sentences_split_at_pauses_without_changing_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            wav = Path(folder) / "v.wav"
            tone_wav(wav, [("tone", 1.0), ("pause", 0.4), ("tone", 2.0), ("pause", 0.1), ("tone", 0.5), ("pause", 0.5), ("tone", 1.0)])
            samples, rate = read_wav(wav)
            phrases, note = split_phrases("Первая фраза. Вторая фраза, длиннее первой и с запятой. Третья.", samples, rate)
        self.assertEqual(note, "")
        self.assertEqual(len(phrases), 3)
        self.assertAlmostEqual(phrases[0].end, 1.2, delta=0.05)
        # короткая пауза 0.1 с внутри второй фразы не режется
        self.assertAlmostEqual(phrases[1].end, 4.25, delta=0.06)
        self.assertAlmostEqual(phrases[2].end, len(samples) / rate, places=3)
        self.assertAlmostEqual(sum(p.end - p.start for p in phrases), len(samples) / rate, places=3)

    def test_no_pauses_keeps_whole_phrase(self):
        with tempfile.TemporaryDirectory() as folder:
            wav = Path(folder) / "v.wav"
            tone_wav(wav, [("tone", 2.0)])
            samples, rate = read_wav(wav)
            phrases, note = split_phrases("Раз. Два.", samples, rate)
        self.assertEqual(len(phrases), 1)
        self.assertTrue(note)

    def test_default_anchors_are_monotonic(self):
        self.assertEqual(default_anchors(4, 2), [0, 0, 1, 1])
        self.assertEqual(default_anchors(2, 5), [0, 2])
        self.assertEqual(default_anchors(2, 0), [None, None])


def step(actions, anchors, phrases_len):
    return AnchoredStep(id="s", narration="", wav=Path("x.wav"), actions=actions, anchors=anchors), [
        Phrase(f"ф{i}", sum(phrases_len[:i]), sum(phrases_len[: i + 1])) for i in range(len(phrases_len))
    ]


class PlanTest(unittest.TestCase):
    def test_each_phrase_starts_with_its_action(self):
        actions = [Action("a", 10.0, 10.5), Action("b", 20.0, 20.6)]
        st, phrases = step(actions, [0, 1], [2.0, 6.0])
        beats, _ = plan_step(st, phrases, [], cursor=0.0, next_start=None, total=60, lead=0.3, pre=0.8, tail=1.5)
        self.assertEqual(len(beats), 2)
        for beat in beats:
            # голос начинается за lead до действия на смонтированном таймлайне
            self.assertAlmostEqual(beat.voice_at, 0.8 - 0.3, places=3)
            self.assertGreaterEqual(beat.duration + 1e-6, beat.voice_at + beat.voice)
        # короткое действие и длинная фраза: стоп-кадр, а не ускорение голоса
        self.assertGreater(beats[1].hold, 3.0)
        self.assertAlmostEqual(beats[1].voice, 6.0, places=3)

    def test_cut_pause_does_not_shift_anchor(self):
        actions = [Action("a", 10.0, 10.4), Action("b", 10.6, 16.0)]
        # пауза 11–15 с вырезается, голос второй фразы всё равно в начале действия b
        st, phrases = step(actions, [0, 1], [0.5, 0.5])
        beats, _ = plan_step(st, phrases, [(11.0, 15.0)], cursor=0.0, next_start=None, total=30, lead=0.3, pre=0.8, tail=1.5, margin=0.4)
        b = beats[1]
        self.assertLess(b.shown, 0.8 + 5.4 + 1.5 - 3.0)
        self.assertAlmostEqual(b.voice_at, 0.5, places=3)

    def test_unanchored_phrases_follow_previous_or_open_step(self):
        actions = [Action("a", 5.0, 5.5)]
        st, phrases = step(actions, [None, 0, None], [3.0, 1.0, 2.0])
        beats, _ = plan_step(st, phrases, [], cursor=0.0, next_start=None, total=20)
        self.assertEqual([b.action for b in beats], [None, 0])
        self.assertEqual([len(b.phrases) for b in beats], [1, 2])
        self.assertEqual(beats[0].keep, [])
        self.assertAlmostEqual(beats[1].voice, 3.0, places=3)

    def test_long_action_plays_at_normal_speed(self):
        actions = [Action("a", 2.0, 12.0)]
        st, phrases = step(actions, [0], [1.0])
        beats, _ = plan_step(st, phrases, [], cursor=0.0, next_start=None, total=30)
        self.assertAlmostEqual(beats[0].shown, 0.8 + 10 + 1.5, places=3)
        self.assertGreaterEqual(beats[0].duration, beats[0].shown)
        self.assertLess(beats[0].hold, 0.05)

    def test_actions_out_of_order_in_recording(self):
        actions = [Action("late", 160.0, 160.5), Action("early", 24.0, 24.6)]
        st, phrases = step(actions, [0, 1], [1.0, 1.0])
        beats, _ = plan_step(st, phrases, [], cursor=150.0, next_start=None, total=200)
        self.assertEqual(beats[1].raw_start, 23.2)
        self.assertAlmostEqual(beats[1].voice_at, 0.5, places=3)

    def test_beat_audio_keeps_samples_and_length(self):
        with tempfile.TemporaryDirectory() as folder:
            src = Path(folder) / "v.wav"
            samples = tone_wav(src, [("tone", 1.0), ("pause", 0.5), ("tone", 1.0)])
            actions = [Action("a", 3.0, 3.3)]
            st, phrases = step(actions, [0], [2.5])
            beats, _ = plan_step(st, phrases, [], cursor=0.0, next_start=None, total=10)
            dest = Path(folder) / "b.wav"
            beat_audio(beats[0], samples, RATE, dest)
            out, rate = read_wav(dest)
        offset = int(round(beats[0].voice_at * RATE))
        self.assertEqual(len(out), int(round(beats[0].duration * RATE)))
        self.assertEqual(out[offset : offset + len(samples)], samples)


class JournalTest(unittest.TestCase):
    def test_journal_with_actions_and_without(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with_actions = {"raw": "raw.mp4", "steps": [{"id": "1", "narration": "Т.", "wav": "v.wav", "title": "Шаг",
                                                         "anchors": [0], "actions": [{"label": "a", "start": 1, "end": 2, "rect": [1, 2, 30, 40], "caption": "Кнопка"}]}]}
            (base / "j.json").write_text(json.dumps(with_actions, ensure_ascii=False), encoding="utf-8")
            raw, steps, _ = load_anchored_journal(base / "j.json")
            self.assertEqual(raw, base / "raw.mp4")
            self.assertEqual(steps[0].actions[0].rect, (1, 2, 30, 40))
            self.assertEqual(steps[0].actions[0].caption, "Кнопка")
            old = {"raw": "raw.mp4", "steps": [{"id": "1", "narration": "Т.", "wav": "v.wav", "windows": [[0, 1]]}]}
            (base / "o.json").write_text(json.dumps(old), encoding="utf-8")
            self.assertIsNone(load_anchored_journal(base / "o.json"))

    def test_live_cues_keep_uia_rect(self):
        steps = steps_from_live_cues([{"step": "1", "narration": "А.", "wav": "a.wav", "title": "Т",
                                        "actions": [{"label": "Провести", "start": 1.0, "end": 1.4, "rect": [10, 20, 110, 50]}]}])
        self.assertEqual(steps[0].actions[0].rect, (10, 20, 110, 50))
        self.assertEqual(steps[0].title, "Т")


@unittest.skipUnless(HAS_FFMPEG, "нужен ffmpeg")
class RenderTest(unittest.TestCase):
    def test_render_with_annotations_and_sync(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            raw = base / "raw.mp4"
            run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=12", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(raw)])
            wav = base / "v.wav"
            tone_wav(wav, [("tone", 1.0), ("pause", 0.5), ("tone", 3.0)])
            steps = [AnchoredStep(id="1", narration="Нажимаем. Смотрим долго результат.", wav=wav, title="Шаг один",
                                  actions=[Action("a", 2.0, 2.4, "Кнопка", (20, 20, 120, 60)), Action("b", 6.0, 6.5, "Поле")], anchors=[0, 1])]
            report = edit_anchored(raw, steps, base / "out", use_freezedetect=False)
            beats = report["steps"][0]["beats"]
            self.assertEqual(report["speech_tempo"], 1.0)
            self.assertTrue((base / "out" / "lesson.mp4").exists())
            expected = sum(b["duration"] for b in beats)
            self.assertAlmostEqual(report["duration_seconds"], expected, delta=0.15)
            srt = (base / "out" / "subtitles.srt").read_text(encoding="utf-8")
            self.assertIn("00:00:00,500 -->", srt)
            second_start = beats[0]["duration"] + beats[1]["voice_at"]
            self.assertIn(f"00:00:0{int(second_start)},", srt)
            plain = edit_anchored(raw, steps, base / "plain", use_freezedetect=False, annotate=False)
            self.assertFalse(plain["annotations"])
