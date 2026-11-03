"""Совместимость: словарь произношения теперь в pipeline/pronunciation.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.pronunciation import pronounce, strip_stress  # noqa: E402,F401
