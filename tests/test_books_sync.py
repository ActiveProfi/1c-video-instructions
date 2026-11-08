import inspect
import json
import tempfile
import unittest
import wave
from datetime import datetime
from pathlib import Path

from pipeline import books_sync, live, tts
from pipeline.lesson_yaml import load_lesson
from pipeline.pronunciation import pronounce
from pipeline.ui_session import FakeSession, RoleMismatch, canonical_role, unique_match
from pipeline.__main__ import main

ROOT = Path(__file__).resolve().parents[1]
LESSON_PATH = ROOT / "lessons" / "books-upd.yaml"
ACUTE = "\u0301"
WHEN = datetime(2026, 11, 8, 12, 30, 45)


def silence(path: Path, seconds: float = 0.2, rate: int = 8000) -> float:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames)
    return seconds


def session_for(lesson, skip=None, duplicate=None):
    skip = set(skip or [])
    beats = books_sync.beats_from_lesson(lesson)
    controls = [
        {"name": "Альфа ООО", "role": "text", "parents": ["Главное"], "visible": True, "value": "Альфа ООО"},
    ]
    for beat in beats:
        if beat["element"] in skip:
            continue
        value = ""
        if beat.get("expect_digits"):
            value = beat["expect_digits"]
        elif beat.get("expect_contains"):
            value = beat["expect_contains"]
        elif beat.get("require_filled"):
            value = "Основной"
        elif beat.get("expect_name"):
            value = beat["element"]
        controls.append(
            {
                "name": beat["element"],
                "role": canonical_role(beat.get("role") or "button"),
                "parents": [beat["within"]] if beat.get("within") else [],
                "visible": True,
                "value": value,
                "left": 120,
                "top": 90,
                "right": 280,
                "bottom": 130,
            }
        )
        if duplicate == beat["element"]:
            controls.append(dict(controls[-1]))
    session = FakeSession(controls, texts=["Альфа ООО", "уже есть УЧ-КНИГИ-001 и УЧ-КНИГИ-002"])
    session.cursor = (15, 15)
    return session


class LessonShapeTest(unittest.TestCase):
    def test_phrases_are_short_and_cover_the_shown_fields(self):
        lesson = load_lesson(LESSON_PATH)
        self.assertEqual(lesson["recording"], "synced")
        self.assertEqual(lesson["voice"], books_sync.LILY_VOICE)
        self.assertEqual(lesson["tts_model"], books_sync.LILY_MODEL)
        beats = books_sync.beats_from_lesson(lesson)
        covers = {beat["covers"] for beat in beats}
        for name in books_sync.REQUIRED_COVERS:
            self.assertIn(name, covers)
        for beat in beats:
            self.assertLessEqual(len(beat["narration"]), 220)
            self.assertLessEqual(len(books_sync._sentences(beat["narration"])), 2)
            self.assertNotIn(ACUTE, beat["narration"])
            self.assertNotIn("УЧ-КНИГИ-001", beat["value"])
        entries = next(beat for beat in beats if beat["covers"] == "entries")
        spoken = pronounce(entries["narration"])
        self.assertIn("де" + ACUTE + "бет", spoken)
        self.assertIn("кре" + ACUTE + "дит", spoken)
        self.assertNotIn(ACUTE, books_sync.strip_stress(entries["narration"]))

    def test_plan_never_restores_or_deletes(self):
        beats = books_sync.beats_from_lesson(load_lesson(LESSON_PATH))
        blob = json.dumps(beats, ensure_ascii=False).lower()
        for word in ("удалить", "восстанов", ".dt", "отправить", "выгрузить"):
            self.assertNotIn(word, blob)


class NumberTest(unittest.TestCase):
    def test_scan_is_read_only_and_skips_reserved_numbers(self):
        session = session_for(load_lesson(LESSON_PATH))
        seen = books_sync.visible_incoming_numbers(session)
        self.assertEqual(session.clicks, [])
        self.assertEqual(session.fills, [])
        self.assertIn("УЧ-КНИГИ-001", seen)
        self.assertIn("УЧ-КНИГИ-002", seen)
        number = books_sync.allocate_incoming_number(seen, WHEN)
        self.assertEqual(number, "УЧ-КНИГИ-261108123045")
        self.assertNotIn(number, books_sync.RESERVED_NUMBERS)
        again = books_sync.allocate_incoming_number(set(seen) | {number}, WHEN)
        self.assertEqual(again, "УЧ-КНИГИ-261108123045-2")

    def test_each_run_gets_its_own_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = books_sync.output_directory(root, None, "20261108-123045")
            first.mkdir(parents=True)
            second = books_sync.output_directory(root, None, "20261108-123045")
        self.assertNotEqual(first, second)
        self.assertIn("books-upd", str(first))


class RecordingTest(unittest.TestCase):
    def setUp(self):
        self.lesson = load_lesson(LESSON_PATH)
        self.spoken = []

    def speak(self, text, dest):
        self.spoken.append(text)
        return silence(dest, 0.2)

    def record(self, session, **kwargs):
        folder = Path(kwargs.pop("folder"))
        return books_sync.perform_synced(
            session,
            self.lesson,
            folder,
            ROOT,
            "offline",
            "",
            clock=books_sync.Clock(),
            speak=self.speak,
            now=WHEN,
            timeout=0.05,
            **kwargs,
        )

    def test_speech_starts_when_the_target_is_shown(self):
        with tempfile.TemporaryDirectory() as folder:
            session = session_for(self.lesson)
            report = self.record(session, folder=folder)
            self.assertEqual(report["status"], "recorded", report.get("message"))
            self.assertFalse(report["legacy_fallback"])
            self.assertFalse(report["windows_1c_verified"])
            self.assertNotEqual(report["incoming_number"], "УЧ-КНИГИ-001")
            self.assertIn(("Номер входящего документа", report["incoming_number"]), session.fills)
            self.assertNotIn("Удалить", session.clicks)
            self.assertNotIn("УЧ-КНИГИ-001", session.clicks)
            self.assertFalse(getattr(session, "closed_panels", 0))
            timing = json.loads((Path(folder) / "timing-report.json").read_text(encoding="utf-8"))
            self.assertTrue(timing["synced"])
            self.assertEqual(timing["max_drift_seconds"], 0.0)
            for row in timing["beats"]:
                self.assertEqual(row["shown_at"], row["speech_start"])
            coverage = json.loads((Path(folder) / "coverage.json").read_text(encoding="utf-8"))
            shown = {row["covers"]: row for row in coverage}
            for name in books_sync.REQUIRED_COVERS:
                self.assertEqual(shown[name]["rect_source"], "uia")
                self.assertEqual(shown[name]["speech"][0], next(beat["shown_at"] for beat in report["beats"] if beat["covers"] == name))
            self.assertGreater(len(session.cursor_path), 4)
            self.assertTrue(any(point != session.cursor_path[0] and point != session.cursor_path[-1] for point in session.cursor_path))
            self.assertTrue(all(ACUTE not in text for text in self.spoken))
            self.assertTrue((Path(folder) / "audit.json").exists())
            self.assertEqual(json.loads((Path(folder) / "errors.json").read_text(encoding="utf-8")), [])

    def test_rect_is_taken_before_a_later_click_moves_the_field(self):
        session = session_for(self.lesson)
        original = None

        real_click = session.click

        def click(element):
            real_click(element)
            if element.name == "Провести":
                for item in session.controls:
                    if item["name"] == "Всего":
                        item["left"], item["right"] = 800, 900

        session.click = click
        with tempfile.TemporaryDirectory() as folder:
            report = self.record(session, folder=folder)
        total = next(row for row in report["beats"] if row["covers"] == "total")
        original = total["rect"][0]
        self.assertEqual(original, 110)
        self.assertLess(original, 700)

    def test_ambiguous_contract_stops_before_posting(self):
        session = session_for(self.lesson, duplicate="Договор")
        with tempfile.TemporaryDirectory() as folder:
            report = self.record(session, folder=folder)
            errors = json.loads((Path(folder) / "errors.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["recorded"])
        self.assertFalse(report["posted"])
        self.assertNotIn("Провести", session.clicks)
        self.assertEqual(errors[0]["kind"], "ambiguous")
        self.assertIn("Договор", errors[0]["target"])
        self.assertFalse(report["legacy_fallback"])

    def test_missing_vat_rate_stops_and_does_not_invent_a_box(self):
        session = session_for(self.lesson, skip={"Ставка НДС"})
        with tempfile.TemporaryDirectory() as folder:
            report = self.record(session, folder=folder)
            coverage = json.loads((Path(folder) / "coverage.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "failed")
        self.assertNotIn("Провести", session.clicks)
        self.assertFalse(any(row.get("covers") == "vat_rate" for row in coverage))
        self.assertIn("Ставка НДС", report["message"])

    def test_seen_number_is_replaced_after_a_read_only_rescan(self):
        session = session_for(self.lesson)
        stamp = "УЧ-КНИГИ-261108123045"
        real_click = session.click

        def click(element):
            real_click(element)
            if element.name.startswith("Поступление (акты"):
                session.texts.append(stamp)

        session.click = click
        with tempfile.TemporaryDirectory() as folder:
            report = self.record(session, folder=folder)
        self.assertEqual(report["incoming_number"], stamp + "-2")
        self.assertIn(("Номер входящего документа", stamp + "-2"), session.fills)
        self.assertNotIn(stamp, [value for _name, value in session.fills])

    def test_preflight_does_not_click_or_close_panels(self):
        session = session_for(self.lesson)
        report = books_sync.preflight_synced(session, self.lesson, now=WHEN)
        self.assertEqual(report["status"], "preflight")
        self.assertEqual(session.clicks, [])
        self.assertEqual(session.fills, [])
        self.assertFalse(report["document_modified"])
        self.assertEqual(report["incoming_number_preview"], "УЧ-КНИГИ-261108123045")
        self.assertEqual(books_sync.preflight_exit_code(report), 0)
        blocked = books_sync.preflight_synced(session_for(self.lesson, duplicate="Договор"), self.lesson, now=WHEN)
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(books_sync.preflight_exit_code(blocked), 2)

    def test_dry_run_writes_reports_without_finish(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            session = session_for(self.lesson)
            report = live.run_dry(LESSON_PATH, out, session=session)
            self.assertEqual(report["status"], "preflight")
            self.assertTrue((out / "preflight.json").exists())
            self.assertEqual(session.clicks, [])
            self.assertFalse(getattr(session, "closed_panels", 0))


class VoiceAndAssemblyTest(unittest.TestCase):
    def test_cache_is_reused_across_folders(self):
        calls = []
        original = tts.synthesize_offline

        def wrapped(text, dest, rate=22050):
            calls.append(dest)
            return original(text, dest, rate)

        tts.synthesize_offline = wrapped
        try:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                cache = root / "cache"
                first = root / "run-a" / "phrase.wav"
                second = root / "run-b" / "phrase.wav"
                tts.synthesize("Дебет и кредит.", first, "offline", "", root, cache_dir=cache)
                self.assertEqual(len(calls), 1)
                tts.synthesize("Дебет и кредит.", second, "offline", "", root, cache_dir=cache)
                self.assertEqual(len(calls), 1)
                self.assertGreater(second.stat().st_size, 400)
        finally:
            tts.synthesize_offline = original

    def test_other_model_and_auto_voice_are_refused(self):
        lesson = load_lesson(LESSON_PATH)
        with self.assertRaises(RuntimeError):
            books_sync.resolve_voice(lesson, "auto", None)
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder) / ".env"
            env.write_text("ELEVENLABS_MODEL_ID=eleven_turbo_v2_5\n", encoding="utf-8")
            with self.assertRaises(RuntimeError) as caught:
                books_sync.assert_approved_voice(Path(folder), "elevenlabs", books_sync.LILY_VOICE, lesson)
        self.assertIn(books_sync.LILY_MODEL, str(caught.exception))
        books_sync.assert_approved_voice(ROOT, "offline", "", lesson)

    def test_assembly_uses_the_captured_rect_and_has_no_legacy_path(self):
        calls = []

        def runner(args):
            calls.append(args)
            Path(args[-1]).write_bytes(b"x" * 1200)

        beats = [
            {
                "id": "contract",
                "narration": "Договор — семь от первого апреля.",
                "subtitle": "Договор — семь от первого апреля.",
                "element": "Договор",
                "covers": "contract",
                "rect": [10, 20, 110, 60],
                "rect_source": "uia",
                "shown_at": 0.4,
                "speech_start": 0.4,
                "speech_end": 0.9,
                "wav": "contract.wav",
            },
            {
                "id": "vat_rate",
                "narration": "Ставка НДС — двадцать два процента.",
                "subtitle": "Ставка НДС — двадцать два процента.",
                "element": "Ставка НДС",
                "covers": "vat_rate",
                "rect": [12, 22, 80, 50],
                "rect_source": "uia",
                "shown_at": 1.2,
                "speech_start": 1.2,
                "speech_end": 1.7,
                "wav": "vat.wav",
            },
            {
                "id": "vat_amount",
                "narration": "Сумма НДС.",
                "subtitle": "Сумма НДС.",
                "covers": "vat_amount",
                "element": "Сумма НДС",
                "rect": [1, 2, 3, 4],
                "rect_source": "uia",
                "shown_at": 2.0,
                "speech_start": 2.0,
                "speech_end": 2.4,
                "wav": "vat-amount.wav",
            },
            {
                "id": "total",
                "narration": "Всего.",
                "subtitle": "Всего.",
                "covers": "total",
                "element": "Всего",
                "rect": [1, 2, 3, 4],
                "rect_source": "uia",
                "shown_at": 2.5,
                "speech_start": 2.5,
                "speech_end": 2.9,
                "wav": "total.wav",
            },
            {
                "id": "invoice",
                "narration": "Счёт-фактура.",
                "subtitle": "Счёт-фактура.",
                "covers": "invoice",
                "element": "Счет-фактура полученный",
                "rect": [1, 2, 3, 4],
                "rect_source": "uia",
                "shown_at": 3.0,
                "speech_start": 3.0,
                "speech_end": 3.4,
                "wav": "invoice.wav",
            },
            {
                "id": "entries",
                "narration": "Книги без налога: дебет сорок один, кредит шестьдесят.",
                "subtitle": "Книги без налога: дебет сорок один, кредит шестьдесят.",
                "covers": "entries",
                "element": "41.01",
                "rect": [4, 5, 40, 20],
                "rect_source": "uia",
                "shown_at": 3.5,
                "speech_start": 3.5,
                "speech_end": 4.0,
                "wav": "entries.wav",
            },
        ]
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            report = books_sync.assemble_synced(out / "raw-screen.mp4", beats, out, runner)
            subtitles = (out / "subtitles.srt").read_text(encoding="utf-8")
        command = " ".join(calls[0])
        self.assertIn("drawbox=x=10:y=20:w=100:h=40", command)
        self.assertIn("adelay=400|400", command)
        self.assertIn("synced-body.mp4", command)
        self.assertNotIn(ACUTE, subtitles)
        self.assertIn("дебет", subtitles)
        self.assertEqual(report["legacy_fallback"], False)
        self.assertNotIn("edit_continuous", inspect.getsource(books_sync.run_synced))
        self.assertNotIn("_finish_movie", inspect.getsource(books_sync.run_synced))
        self.assertNotIn("edit_continuous", inspect.getsource(books_sync.assemble_synced))

    def test_bad_timing_does_not_fall_back(self):
        beat = {
            "id": "contract",
            "narration": "Договор.",
            "element": "Договор",
            "covers": "contract",
            "rect": [1, 2, 3, 4],
            "rect_source": "uia",
            "shown_at": 0.0,
            "speech_start": 1.0,
            "speech_end": 1.2,
            "wav": "a.wav",
        }
        with self.assertRaises(books_sync.SyncAssemblyError):
            books_sync.assemble_synced(Path("raw.mp4"), [beat], Path("."), lambda args: None)

    def test_cards_shift_human_subtitles(self):
        lesson = load_lesson(LESSON_PATH)

        def speak(text, dest):
            return silence(dest, 1.0)

        def card_clip(*args):
            Path(args[-1]).write_bytes(b"card")

        def concat(paths, dest, with_audio=True):
            dest.write_bytes(b"movie")

        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            from pipeline.postprod import write_srt

            write_srt([(0.0, 0.5, "Договор — семь от первого апреля.")], out / "subtitles.srt")
            books_sync.attach_cards(lesson, out / "synced-body.mp4", out, speak, card_clip, concat)
            text = (out / "subtitles.srt").read_text(encoding="utf-8")
        self.assertNotIn(ACUTE, text)
        self.assertIn("00:00:01,000", text)
        self.assertIn("1С", text)

    def test_record_route_does_not_call_the_old_editor(self):
        called = {}

        def fake_run(lesson, out_dir, project_root, tts_name, voice, session, grab=None, **kwargs):
            called["voice"] = voice
            called["tts"] = tts_name
            return {"status": "recorded", "video": str(out_dir / "lesson.mp4"), "legacy_fallback": False, "windows_1c_verified": False}

        original = books_sync.run_synced
        books_sync.run_synced = fake_run
        try:
            with tempfile.TemporaryDirectory() as folder:
                result = live.run_record(
                    LESSON_PATH,
                    Path(folder),
                    ROOT,
                    "elevenlabs",
                    books_sync.LILY_VOICE,
                    session=session_for(load_lesson(LESSON_PATH)),
                )
        finally:
            books_sync.run_synced = original
        self.assertEqual(result["status"], "recorded")
        self.assertFalse(result["legacy_fallback"])
        self.assertEqual(called["voice"], books_sync.LILY_VOICE)
        with self.assertRaises(RuntimeError):
            live.record_lesson(FakeSession([]), load_lesson(LESSON_PATH), Path("."), ROOT, "offline", "")

    def test_confirmation_is_still_required(self):
        self.assertEqual(main(["live", "--lesson", "lessons/books-upd.yaml", "--dry-run"]), 1)

    def test_glide_is_not_a_jump(self):
        path = books_sync.glide_path(0, 0, 90, 40, 8)
        self.assertEqual(path[0], (0, 0))
        self.assertEqual(path[-1], (90, 40))
        self.assertTrue(any(point not in (path[0], path[-1]) for point in path))

    def test_launcher_uses_the_py_command(self):
        for name in ("Пробный прогон поступления книг.cmd", "Записать поступление книг.cmd"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("py -3", text)
            self.assertIn("books-upd.yaml", text)
            self.assertNotIn("ELEVENLABS_API_KEY", text)
        script = (ROOT / "tools" / "Build-BooksCreation.ps1").read_text(encoding="utf-8")
        self.assertIn("books-upd.yaml", script)
        self.assertIn("-Legacy", script)


class TabItemPreflightTest(unittest.TestCase):
    """Регрессия прогона 2026-11-08: вкладка Покупки и ложный успех пробного прогона."""

    UIA_BY_STEP = {
        "open-purchases": "ControlType.TabItem",
        "open-list": "ControlType.Hyperlink",
        "open-create": "ControlType.Button",
        "choose-goods": "ControlType.MenuItem",
        "incoming-number": "ControlType.Edit",
        "incoming-date": "ControlType.Edit",
        "supplier-type": "ControlType.ComboBox",
        "supplier-pick": "ControlType.ListItem",
        "contract": "ControlType.ComboBox",
        "warehouse": "ControlType.ComboBox",
        "add-row": "ControlType.Button",
        "item-type": "ControlType.ComboBox",
        "item-pick": "ControlType.ListItem",
        "quantity": "ControlType.Edit",
        "price": "ControlType.Edit",
        "vat-rate": "ControlType.Edit",
        "price-mode": "ControlType.Text",
        "total": "ControlType.Text",
        "vat-amount": "ControlType.Text",
        "net": "ControlType.Text",
        "post": "ControlType.Button",
        "open-upd": "ControlType.Hyperlink",
        "invoice": "ControlType.Text",
        "open-entries": "ControlType.Button",
        "entries": "ControlType.Text",
        "entries-vat": "ControlType.Text",
        "open-invoice-entries": "ControlType.Button",
        "invoice-entries": "ControlType.Text",
    }

    def test_every_locator_matches_a_uia_type_the_executor_understands(self):
        beats = books_sync.beats_from_lesson(load_lesson(LESSON_PATH))
        self.assertTrue(beats[0]["required_now"])
        self.assertEqual(beats[0]["role"], "tab")
        self.assertEqual(canonical_role("ControlType.TabItem"), "tab")
        self.assertNotEqual(canonical_role("ControlType.TabItem"), "menu")
        self.assertEqual(canonical_role("ControlType.SplitButton"), "button")
        self.assertEqual(canonical_role("ControlType.DataItem"), "cell")
        self.assertEqual(canonical_role("ControlType.CheckBox"), "check")
        self.assertEqual(canonical_role("ControlType.RadioButton"), "check")
        self.assertEqual(canonical_role("ControlType.Table"), "other")
        seen = set()
        for beat in beats:
            seen.add(beat["id"])
            expected = self.UIA_BY_STEP[beat["id"]]
            self.assertEqual(canonical_role(expected), canonical_role(beat["role"]), beat["id"])
            if beat["role"] == "menu":
                self.assertNotEqual(canonical_role(beat["element"]), "tab")
        self.assertEqual(seen, set(self.UIA_BY_STEP))

    def test_tabitem_section_is_found_and_a_text_namesake_does_not_make_it_ambiguous(self):
        chosen, _note = unique_match(
            [
                {"name": "Покупки", "role": canonical_role("ControlType.TabItem"), "parents": [], "visible": True},
                {"name": "Покупки", "role": canonical_role("ControlType.Text"), "parents": [], "visible": True},
            ],
            "Покупки",
            role="tab",
        )
        self.assertEqual(chosen["role"], "tab")
        with self.assertRaises(RoleMismatch) as caught:
            unique_match(
                [{"name": "Покупки", "role": canonical_role("ControlType.TabItem"), "parents": [], "visible": True}],
                "Покупки",
                role="menu",
            )
        self.assertEqual(caught.exception.roles, ["tab"])

    def test_missing_start_tab_blocks_preflight_instead_of_a_false_success(self):
        lesson = load_lesson(LESSON_PATH)
        home = FakeSession(
            [
                {"name": "Альфа ООО", "role": "text", "visible": True, "value": "Альфа ООО"},
                {"name": "Главное", "role": "ControlType.TabItem", "visible": True},
            ],
            texts=["Альфа ООО"],
        )
        blocked = books_sync.preflight_synced(home, lesson, now=WHEN)
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(books_sync.preflight_exit_code(blocked), 2)
        self.assertIn("Покупки", blocked["blocking"])
        self.assertNotIn("Покупки", blocked["missing_on_this_screen"])
        self.assertGreater(len(blocked["missing_on_this_screen"]), 0)
        self.assertEqual(home.clicks, [])

        visible = FakeSession(
            [
                {"name": "Альфа ООО", "role": "text", "visible": True, "value": "Альфа ООО"},
                {"name": "Покупки", "role": "ControlType.TabItem", "visible": True, "left": 12, "top": 20, "right": 90, "bottom": 48},
                {"name": "Покупки", "role": "ControlType.Text", "visible": True},
            ],
            texts=["Альфа ООО"],
        )
        ready = books_sync.preflight_synced(visible, lesson, now=WHEN)
        self.assertEqual(ready["status"], "preflight")
        self.assertEqual(ready["blocking"], [])
        self.assertNotIn("Покупки", ready["missing_on_this_screen"])
        self.assertTrue(ready["steps"][0]["found"])
        self.assertIn("Поступление (акты, накладные, УПД)", ready["missing_on_this_screen"])

    def test_recording_does_not_speak_until_the_start_tab_is_confirmed(self):
        lesson = load_lesson(LESSON_PATH)
        spoken = []

        def speak(text, dest):
            spoken.append(text)
            return silence(dest)

        class Grab:
            def __init__(self):
                self.started = False

            def start(self, rect, path):
                self.started = True

            def stop(self):
                return ""

        session = FakeSession(
            [
                {"name": "Альфа ООО", "role": "text", "visible": True, "value": "Альфа ООО"},
                {"name": "Покупки", "role": "menu", "visible": True},
            ],
            texts=["Альфа ООО"],
        )
        grab = Grab()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache = books_sync._cache_dir(root, "offline", "lily")
            cache.mkdir(parents=True)
            marker = cache / "already-paid.wav"
            payload = b"R" * 500
            marker.write_bytes(payload)
            report = books_sync.perform_synced(
                session,
                lesson,
                root / "out",
                root,
                "offline",
                "lily",
                clock=books_sync.Clock(),
                speak=speak,
                grab=grab,
                now=WHEN,
                timeout=0.05,
            )
            self.assertEqual(marker.read_bytes(), payload)
        self.assertEqual(spoken, [])
        self.assertFalse(grab.started)
        self.assertEqual(report["beats"], [])
        self.assertFalse(report["recorded"])
        self.assertFalse(report["filled"])
        self.assertFalse(report["posted"])
        self.assertFalse(report["document_modified"])
        self.assertIn("tab", report["message"])
        self.assertIn("menu", report["message"])


if __name__ == "__main__":
    unittest.main()
