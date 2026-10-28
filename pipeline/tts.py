"""Озвучка. Встроенный голос работает без интернета и ключей.

Для ролика, который можно показывать людям, на своём компьютере выберите
голос edge (нужен пакет edge-tts) или ElevenLabs (ключ только в .env).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import unicodedata
import urllib.request
import wave
from array import array
from pathlib import Path

ROOT_TOOLS = Path(__file__).resolve().parents[1] / "tools"


def pronounce(text: str) -> str:
    import sys

    tools = str(ROOT_TOOLS)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from pronunciation import pronounce as apply

    return apply(text)


def prepare_speech(text: str) -> str:
    """Ударения только для синтеза. Субтитры остаются обычным текстом."""
    return pronounce(text)


def read_dotenv(project_root: Path) -> dict:
    path = project_root / ".env"
    settings = {}
    if not path.exists():
        return settings
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name:
            settings[name] = value
    return settings


def _strip_marks(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


FORMANTS = {
    "а": (700, 1200),
    "е": (450, 1900),
    "ё": (450, 1600),
    "и": (300, 2400),
    "о": (500, 900),
    "у": (320, 700),
    "ы": (350, 1600),
    "э": (500, 1800),
    "ю": (300, 1900),
    "я": (450, 2100),
}


def synthesize_offline(text: str, dest: Path, rate: int = 22050) -> None:
    """Короткий синтез по слогам. Это проверка пайплайна, не дикторский голос."""
    plain = _strip_marks(text)
    rng = random.Random(hashlib.sha256(plain.encode("utf-8")).digest())
    samples: list[float] = []

    def add_silence(duration: float) -> None:
        samples.extend([0.0] * int(rate * duration))

    def add_vowel(f1: float, f2: float, duration: float) -> None:
        count = int(rate * duration)
        pitch = 125 + rng.uniform(-3, 3)
        for index in range(count):
            t = index / rate
            envelope = min(1.0, index / 180, (count - index) / 180)
            voice = (
                0.5 * math.sin(2 * math.pi * pitch * t)
                + 0.28 * math.sin(2 * math.pi * f1 * t)
                + 0.18 * math.sin(2 * math.pi * f2 * t)
            )
            samples.append(voice * envelope * 0.45)

    def add_noise(duration: float) -> None:
        count = int(rate * duration)
        for index in range(count):
            envelope = min(1.0, index / 40, (count - index) / 40)
            samples.append(rng.uniform(-1, 1) * 0.08 * envelope)

    add_silence(0.12)
    cluster = False
    for char in plain.lower():
        if char in FORMANTS:
            if cluster:
                add_noise(0.035)
                cluster = False
            f1, f2 = FORMANTS[char]
            add_vowel(f1, f2, 0.085)
        elif char.isalpha():
            cluster = True
        elif char in ".!?":
            cluster = False
            add_silence(0.16)
        elif char in ",;:":
            cluster = False
            add_silence(0.07)
        else:
            if cluster:
                add_noise(0.03)
                cluster = False
    if cluster:
        add_noise(0.04)
    add_silence(0.2)
    if len(samples) < rate:
        add_silence(1)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pcm = array("h", (max(-32767, min(32767, int(sample * 32767))) for sample in samples))
    with wave.open(str(dest), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def synthesize_espeak(text: str, dest: Path) -> None:
    binary = shutil.which("espeak-ng") or shutil.which("espeak")
    if not binary:
        raise RuntimeError("Не найден espeak-ng. Установите его или выберите --tts offline.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([binary, "-v", "ru", "-s", "150", "-w", str(dest), text], check=True)


def synthesize_edge(text: str, voice: str, dest: Path) -> None:
    try:
        import asyncio
        import edge_tts
    except ImportError as error:
        raise RuntimeError(
            "Не установлен пакет edge-tts. Выполните: pip install edge-tts. "
            "Либо запустите сборку с --tts offline."
        ) from error

    async def _save(mp3: Path) -> None:
        communicator = edge_tts.Communicate(text, voice, rate="+0%", pitch="+0Hz")
        await communicator.save(str(mp3))

    dest.parent.mkdir(parents=True, exist_ok=True)
    mp3 = dest.with_suffix(".mp3")
    asyncio.run(_save(mp3))
    subprocess.run(
        [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1", str(dest)],
        check=True,
    )


def synthesize_elevenlabs(text: str, voice: str, dest: Path, project_root: Path) -> None:
    settings = read_dotenv(project_root)
    api_key = os.environ.get("ELEVENLABS_API_KEY") or settings.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Ключ ElevenLabs не найден. Откройте «Подключить ElevenLabs.cmd» "
            "или выберите --tts offline. Ключ не пишите в сценарий."
        )
    model = settings.get("ELEVENLABS_MODEL_ID") or "eleven_multilingual_v2"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128"
    body = json.dumps(
        {
            "text": text,
            "model_id": model,
            "voice_settings": {"stability": 0.45, "similarity_boost": 0.75, "style": 0, "use_speaker_boost": True},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    mp3 = dest.with_suffix(".mp3")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            mp3.write_bytes(response.read())
    except Exception as error:
        raise RuntimeError(
            "ElevenLabs не озвучил фрагмент. Проверьте ключ, голос и остаток кредитов."
        ) from error
    subprocess.run(
        [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1", str(dest)],
        check=True,
    )


def synthesize(text: str, dest: Path, provider: str, voice: str, project_root: Path) -> str:
    speech = prepare_speech(text)
    signature = hashlib.sha256(f"{provider}\n{voice}\n{speech}".encode("utf-8")).hexdigest()[:16]
    cache = dest.parent / f"{dest.stem}-{signature}.wav"
    if cache.exists() and cache.stat().st_size > 400:
        if cache != dest:
            shutil.copyfile(cache, dest)
        return provider
    chosen = provider
    if provider == "offline":
        synthesize_offline(speech, cache)
    elif provider == "espeak":
        synthesize_espeak(speech, cache)
    elif provider == "edge":
        synthesize_edge(speech, voice, cache)
    elif provider == "elevenlabs":
        synthesize_elevenlabs(speech, voice, cache, project_root)
    else:
        raise RuntimeError(f"Неизвестный способ озвучки: {provider}")
    shutil.copyfile(cache, dest)
    return chosen


def speak_clip(text: str, dest: Path, provider: str, voice: str, project_root: Path) -> tuple[str, str | None]:
    chosen, warning = resolve_provider(provider)
    try:
        synthesize(text, dest, chosen, voice, project_root)
        return chosen, warning
    except Exception as error:
        if provider == "auto" and chosen != "offline":
            synthesize(text, dest, "offline", voice, project_root)
            return "offline", f"Голос «{chosen}» не ответил ({type(error).__name__}). Включён встроенный голос."
        raise


def resolve_provider(name: str) -> tuple[str, str | None]:
    """auto: edge, если пакет есть, иначе espeak, иначе встроенный голос."""
    if name != "auto":
        return name, None
    try:
        import edge_tts  # noqa: F401

        return "edge", None
    except ImportError:
        pass
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        return "espeak", None
    return "offline", "Пакет edge-tts не найден, включён встроенный голос."
