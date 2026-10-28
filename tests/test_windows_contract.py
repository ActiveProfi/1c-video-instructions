import unittest
from pathlib import Path

from pipeline.drivers import WindowsUiDriver, windows_argv

ROOT = Path(__file__).resolve().parents[1]


class WindowsContractTest(unittest.TestCase):
    def test_command_requires_training_confirmation(self):
        argv = windows_argv(ROOT, ROOT / "artifacts" / "demo", 0, False)
        self.assertIn("-ConfirmTrainingCopy", argv)
        self.assertIn("Run-LessonPlan.ps1", " ".join(argv))
        driver = WindowsUiDriver(ROOT, confirm=False)
        with self.assertRaises(RuntimeError) as caught:
            driver.run({}, {}, ROOT / "artifacts" / "demo")
        self.assertIn("учебн", str(caught.exception).lower())

    def test_local_runner_keeps_safety_checks(self):
        text = (ROOT / "tools" / "Run-LessonPlan.ps1").read_text(encoding="utf-8-sig")
        for piece in (
            "ConfirmTrainingCopy",
            "DryRun",
            "journal.json",
            "gdigrab",
            "ValuePattern",
            "фнс",
            "Inspect-1C.ps1",
        ):
            self.assertIn(piece, text)
