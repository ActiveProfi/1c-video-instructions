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
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import wave
from array import array
from pathlib import Path

from pipeline.proc import run_captured, text_of

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
    result = run_captured([binary, "-v", "ru", "-s", "150", "-w", str(dest), text])
    if result.returncode:
        detail = text_of(result.stderr).strip() or text_of(result.stdout).strip()
        raise RuntimeError("espeak не озвучил фрагмент: " + detail[-800:])


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
    if not mp3.exists() or mp3.stat().st_size == 0:
        raise RuntimeError("NoAudioReceived: Edge вернул пустой файл для голоса " + voice)
    result = run_captured(
        [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1", str(dest)]
    )
    if result.returncode:
        detail = text_of(result.stderr).strip() or text_of(result.stdout).strip()
        raise RuntimeError("Не удалось преобразовать голос Edge в wav: " + detail[-800:])


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
    result = run_captured(
        [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1", str(dest)]
    )
    if result.returncode:
        detail = text_of(result.stderr).strip() or text_of(result.stdout).strip()
        raise RuntimeError("Не удалось преобразовать голос ElevenLabs в wav: " + detail[-800:])


KIE_BASE_URL = "https://api.kie.ai"
KIE_DEFAULT_MODEL = "elevenlabs/text-to-speech-multilingual-v2"
# Лили (pFZP5JQG7iQjIQuC4Bku) и Сары (EXAVITQu4vr4xnSDxMaL) в списке голосов Kie нет.
# Белла есть, и её проба уже одобрялась в artifacts/voice-tests-elevenlabs.
KIE_DEFAULT_VOICE = "hpp4J3VqNfWAUOO0d1Us"
KIE_RETRYABLE_CODES = {429, 455, 500}


# Голоса, которые принимает Kie для моделей ElevenLabs (docs.kie.ai, 02.11.2026).
KIE_VOICES = frozenset({
    "EkK5I93UQWFDigLMpZcX", "Z3R5wn05IrDiVCyEkUrK", "NNl6r8mD7vthiJatiJt1", "YOq2y2Up4RgXP2HyXjE5",
    "B8gJV1IhpuegLxdpXFOE", "2zRM7PkgwBPiau2jvVXc", "1SM7GgM6IMuvQlz2BwM3", "5l5f8iK3YPeGga21rQIX",
    "scOwDtmlUjD3prqpp97I", "NOpBlnGInO9m6vDvFkFC", "BZgkqPqms7Kj9ulSkVzn", "wo6udizrrtpIxWGp2qJk",
    "gU0LNdkMOQCOrPrwtbee", "DGzg6RaUqxGRTHSBjfgF", "x70vRnQBMBu4FAYhjJbO", "Sm1seazb4gs7RSlUVw7c",
    "P1bg08DkjqiVEzOn76yG", "qDuRKMlYmrm8trt5QyBn", "qXpMhyvQqiRxWQs4qSSB", "TX3LPaxmHKxFdv7VOQHJ",
    "N2lVS1w4EtoT3dr4eOWO", "FGY2WhTYpPnrIDTdsKH5", "kPzsL2i3teMYv0FxEYQ6", "UgBBYS2sOqTuMpoF3BR0",
    "hpp4J3VqNfWAUOO0d1Us", "nPczCjzI2devNBz1zQrb", "uYXf8XasLslADfZ2MB4u", "gs0tAILXbY5DNrJrsM6F",
    "DTKMou8ccj1ZaWGBiotd", "vBKc2FfBKJfcZNyEt1n6", "DYkrAHD8iwork3YSUBbs", "56AoDkrOh6qfVPDXZ7Pt",
    "eR40ATw9ArzDf9h3v7t7", "g6xIsTj2HwM6VR4iXFCw", "lcMyyd2HUfFzxdCaC4Ta", "6aDn1KB0hjpdcocrUkmq",
    "Sq93GQT4X1lKDXsQcixO", "flHkNRp1BlvT73UL6gyz", "9yzdeviXkFddZ4Oz8Mok", "pPdl9cQBQq4p6mRkZy2Z",
    "zYcjlYFOd3taleS0gkk3", "nzeAacJi50IvxcyDnMXa", "ruirxsoakN0GWmGNIo04", "TC0Zp7WVFzhA8zpTlRqV",
    "ljo9gAlSqKOvF6D8sOsX", "PPzYpIqttlTYA83688JI", "8JVbfL6oEdmuxKn5DK2C", "iCrDUkL56s3C8sCRl7wb",
    "wJqPPQ618aTW29mptyoc", "EiNlNiXeDU1pqqOPrYMO", "4YYIPFl9wE5c4L2eu2Gb", "6F5Zhi321D3Oq7v1oNT4",
    "YXpFCvM1S3JbWEJhoskW", "LG95yZDEHg6fCZdQjLqj", "CeNX9CMwmxDxUF5Q2Inm", "aD6riP1btT197c6dACmy",
    "mtrellq69YZsNwzUSyXh", "dHd5gvgSOzSfduK4CvEg", "eVItLK1UvXctxuaRV2Oq", "esy0r39YPLQjOczyOib8",
    "Tsns2HvNFKfGiNjllgqo", "1U02n4nD6AdIZ9CjF053", "AeRdCCKzvd23BpJoofzx", "LruHrtVF6PSyGItzMNHS",
    "1wGbFxmAM3Fgw63G1zZJ", "hqfrgApggtO1785R4Fsn", "MJ0RnG71ty4LH3dvNfSd",
})


class KieError(RuntimeError):
    def __init__(self, code, message: str, retryable: bool = False) -> None:
        super().__init__(f"Kie.ai {code}: {message}")
        self.code = code
        self.retryable = retryable


def _kie_request(method: str, url: str, api_key: str, body: dict | None = None, timeout: float = 60) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        raise KieError(error.code, "HTTP ошибка", retryable=error.code in KIE_RETRYABLE_CODES or error.code >= 500) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise KieError("network", type(error).__name__, retryable=True) from None
    code = payload.get("code")
    if code != 200:
        # Kie отвечает HTTP 200 даже при ошибке: смотрим поле code.
        raise KieError(code, str(payload.get("msg") or "ошибка")[:200], retryable=code in KIE_RETRYABLE_CODES)
    return payload.get("data") or {}


def kie_api_key(project_root: Path) -> str:
    return os.environ.get("KIE_API_KEY") or read_dotenv(project_root).get("KIE_API_KEY") or ""


def synthesize_kie(
    text: str,
    voice: str,
    dest: Path,
    project_root: Path,
    request=None,
    download=None,
    sleep=None,
    poll_timeout: float = 300,
) -> dict:
    """Kie.ai: createTask -> опрос recordInfo -> скачать mp3 -> wav.

    request/download/sleep подменяются в тестах. Ключ берётся только из окружения или .env.
    """
    import time as _time

    api_key = kie_api_key(project_root)
    if not api_key:
        raise KieError(401, "ключ KIE_API_KEY не найден в .env")
    settings = read_dotenv(project_root)
    model = os.environ.get("KIE_TTS_MODEL") or settings.get("KIE_TTS_MODEL") or KIE_DEFAULT_MODEL
    request = request or _kie_request
    sleep = sleep or _time.sleep
    created = request(
        "POST",
        f"{KIE_BASE_URL}/api/v1/jobs/createTask",
        api_key,
        {
            "model": model,
            "input": {"text": text, "voice": voice or KIE_DEFAULT_VOICE, "stability": 0.45, "similarity_boost": 0.75, "style": 0, "speed": 1},
        },
    )
    task_id = created.get("taskId")
    if not task_id:
        raise KieError("no-task", "в ответе нет taskId", retryable=True)
    waited = 0.0
    delay = 2.0
    while True:
        info = request("GET", f"{KIE_BASE_URL}/api/v1/jobs/recordInfo?taskId={urllib.parse.quote(task_id)}", api_key, None)
        state = info.get("state")
        if state == "success":
            break
        if state == "fail":
            # Задача создана, но упала на стороне Kie (state=fail). Кредиты за неё не списываются.
            code = info.get("failCode") or 501
            raise KieError(code, f"задача {task_id} не выполнена на стороне Kie: {str(info.get('failMsg') or 'генерация не удалась')[:200]}")
        if waited >= poll_timeout:
            raise KieError(408, f"задача {task_id} не готова за {int(poll_timeout)} с", retryable=True)
        sleep(delay)
        waited += delay
        delay = min(delay * 1.5, 10.0)
    result = info.get("resultJson") or "{}"
    urls = (json.loads(result) if isinstance(result, str) else result).get("resultUrls") or []
    if not urls:
        raise KieError("no-audio", "в результате нет ссылки на звук")
    dest.parent.mkdir(parents=True, exist_ok=True)
    mp3 = dest.with_suffix(".mp3")
    if download is None:
        with urllib.request.urlopen(urls[0], timeout=120) as response:
            mp3.write_bytes(response.read())
    else:
        download(urls[0], mp3)
    if not mp3.exists() or mp3.stat().st_size == 0:
        raise KieError("no-audio", "скачан пустой файл")
    converted = run_captured(
        [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp3), "-ar", "24000", "-ac", "1", str(dest)]
    )
    if converted.returncode:
        detail = text_of(converted.stderr).strip() or text_of(converted.stdout).strip()
        raise RuntimeError("Не удалось преобразовать голос Kie в wav: " + detail[-800:])
    return {"task_id": task_id, "model": model, "credits": info.get("creditsConsumed")}


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
    elif provider == "kie":
        synthesize_kie(speech, voice, cache, project_root)
    else:
        raise RuntimeError(f"Неизвестный способ озвучки: {provider}")
    shutil.copyfile(cache, dest)
    return chosen


DEFAULT_CHAIN = ("elevenlabs", "kie", "edge", "offline")
EDGE_DEFAULT_VOICE = "fr-FR-RemyMultilingualNeural"
ELEVENLABS_DEFAULT_VOICE = "pFZP5JQG7iQjIQuC4Bku"
# Ошибки, после которых повтор того же голоса бесполезен: сервис ответил,
# но звука для этого голоса нет. Сразу переходим к следующему способу.
NO_RETRY_ERRORS = ("NoAudioReceived", "UnknownResponse", "ImportError", "KieFatal")


def parse_chain(value) -> list[str]:
    """«edge,elevenlabs,offline» -> список. Пусто -> порядок по умолчанию."""
    if not value:
        return list(DEFAULT_CHAIN)
    if isinstance(value, str):
        items = [item.strip().lower() for item in value.replace(">", ",").replace(";", ",").split(",")]
    else:
        items = [str(item).strip().lower() for item in value]
    known = {"kie", "edge", "elevenlabs", "offline", "espeak"}
    chain = [item for item in items if item]
    unknown = [item for item in chain if item not in known]
    if unknown:
        raise RuntimeError(f"Неизвестный способ озвучки: {', '.join(unknown)}")
    seen: list[str] = []
    for item in chain:
        if item not in seen:
            seen.append(item)
    return seen


def _error_name(error: BaseException) -> str:
    cause = error.__cause__ or error.__context__
    names = [type(error).__name__]
    if cause is not None:
        names.append(type(cause).__name__)
    names.extend(name for name in NO_RETRY_ERRORS if str(error).startswith(name))
    for name in names:
        if name in NO_RETRY_ERRORS:
            return name
    if names[0] == "RuntimeError" and cause is not None:
        return names[1]
    return names[0]


def _cleanup_partial(dest: Path) -> None:
    """Пустой mp3/wav после сбоя Edge раньше оставался и мешал следующему запуску."""
    for path in dest.parent.glob(dest.stem + "*"):
        try:
            if path.is_file() and path.stat().st_size < 400:
                path.unlink()
        except OSError:
            pass


class TtsLog:
    """Чем озвучен каждый фрагмент. Пишется в tts-log.json рядом с уроком."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def add(self, entry: dict) -> None:
        self.entries.append(entry)

    def voices(self) -> list[str]:
        result = []
        for entry in self.entries:
            key = f"{entry['provider']}:{entry['voice']}"
            if entry.get("provider") and key not in result:
                result.append(key)
        return result

    def mixed_warning(self) -> str | None:
        voices = self.voices()
        if len(voices) <= 1:
            return None
        fallback = [entry["fragment"] for entry in self.entries if entry.get("fallback")]
        return (
            "В уроке смешаны голоса: " + ", ".join(voices) + ". "
            + ("Запасным голосом озвучены: " + ", ".join(fallback) + ". " if fallback else "")
            + "Перед публикацией переозвучьте эти фрагменты одним голосом."
        )

    def to_dict(self) -> dict:
        return {"fragments": self.entries, "voices": self.voices(), "mixed_voices_warning": self.mixed_warning()}

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _voice_for(provider: str, voice: str, voices: dict | None) -> str:
    if voices and voices.get(provider):
        return voices[provider]
    if provider == "edge":
        return voice if voice and not _looks_like_elevenlabs(voice) else EDGE_DEFAULT_VOICE
    if provider == "elevenlabs":
        return voice if voice and _looks_like_elevenlabs(voice) else ELEVENLABS_DEFAULT_VOICE
    if provider == "kie":
        return voice if voice in KIE_VOICES else KIE_DEFAULT_VOICE
    return voice or ""


def _looks_like_elevenlabs(voice: str) -> bool:
    return bool(voice) and "-" not in voice and len(voice) >= 16


def speak_with_fallback(
    text: str,
    dest: Path,
    chain,
    voice: str,
    project_root: Path,
    voices: dict | None = None,
    log: TtsLog | None = None,
    retries: int = 3,
    base_delay: float = 2.0,
    sleep=None,
    fragment: str | None = None,
) -> dict:
    """Озвучивает один фрагмент: повторы Edge с нарастающей паузой, затем следующий способ.

    Возвращает запись журнала: какой способ и голос сработали, сколько было попыток и ошибок.
    Текст ошибок без ключей: в сообщения попадает только тип ошибки и короткое описание.
    """
    import time as _time

    sleep = sleep or _time.sleep
    providers = parse_chain(chain)
    errors: list[dict] = []
    attempts = 0
    for position, provider in enumerate(providers):
        chosen_voice = _voice_for(provider, voice, voices)
        tries = retries if provider in ("edge", "kie") else 1
        for attempt in range(tries):
            attempts += 1
            try:
                synthesize(text, dest, provider, chosen_voice, project_root)
                entry = {
                    "fragment": fragment or dest.stem,
                    "provider": provider,
                    "voice": chosen_voice,
                    "attempts": attempts,
                    "fallback": position > 0,
                    "errors": errors,
                }
                if log is not None:
                    log.add(entry)
                return entry
            except Exception as error:  # noqa: BLE001 - сбой любого сервиса ведёт к следующему
                name = _error_name(error)
                if isinstance(error, KieError):
                    name = f"Kie{error.code}"
                    if not error.retryable:
                        name = "KieFatal"
                errors.append({"provider": provider, "voice": chosen_voice, "attempt": attempt + 1, "error": name, "detail": str(error)[:200]})
                _cleanup_partial(dest)
                if name in NO_RETRY_ERRORS or attempt + 1 >= tries:
                    break
                sleep(base_delay * (2 ** attempt))
    entry = {"fragment": fragment or dest.stem, "provider": None, "voice": None, "attempts": attempts, "fallback": True, "errors": errors}
    if log is not None:
        log.add(entry)
    summary = "; ".join(f"{item['provider']}: {item['error']}" for item in errors)
    raise RuntimeError(f"Фрагмент «{fragment or dest.stem}» не озвучен ни одним способом ({summary}).")


def speak_clip(
    text: str,
    dest: Path,
    provider: str,
    voice: str,
    project_root: Path,
    log: TtsLog | None = None,
    chain=None,
    voices: dict | None = None,
    sleep=None,
) -> tuple[str, str | None]:
    """provider=auto или chain: пофрагментный перебор edge -> elevenlabs -> встроенный.

    Явный provider (edge, elevenlabs, offline, espeak) работает как раньше, но Edge
    теперь повторяется с нарастающей паузой.
    """
    chain = chain or (os.environ.get("LESSON_TTS_CHAIN") if provider in ("auto", "chain") else None)
    if provider in ("auto", "chain") or chain:
        entry = speak_with_fallback(text, dest, chain or DEFAULT_CHAIN, voice, project_root, voices=voices, log=log, sleep=sleep)
        warning = None
        if entry["fallback"]:
            first = entry["errors"][0] if entry["errors"] else {}
            warning = (
                f"Фрагмент «{entry['fragment']}»: {first.get('provider')} не ответил ({first.get('error')}). "
                f"Озвучено: {entry['provider']} / {entry['voice']}."
            )
        return entry["provider"], warning
    entry = speak_with_fallback(text, dest, [provider], voice, project_root, voices={provider: voice} if voice else None, log=log, sleep=sleep)
    return entry["provider"], None


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
