import json, sys, urllib.request, os
from pathlib import Path
root = Path(sys.argv[1]); mode = sys.argv[2]
sys.path.insert(0, str(root))
from pipeline.tts import read_dotenv
key = os.environ.get("ELEVENLABS_API_KEY") or read_dotenv(root).get("ELEVENLABS_API_KEY")
H = {"xi-api-key": key, "Content-Type": "application/json"}
if mode == "voices":
    req = urllib.request.Request("https://api.elevenlabs.io/v1/voices", headers=H)
    for v in json.load(urllib.request.urlopen(req, timeout=60))["voices"]:
        l = v.get("labels") or {}
        print(v["voice_id"], v["name"], l.get("accent"), l.get("language"), v.get("category"))
    req = urllib.request.Request("https://api.elevenlabs.io/v1/shared-voices?language=ru&page_size=15&sort=usage_character_count_1y&gender=female", headers=H)
    for v in json.load(urllib.request.urlopen(req, timeout=60)).get("voices", []):
        print("shared", v["voice_id"], v.get("public_owner_id"), v["name"], v.get("accent"), v.get("language"))
    sys.exit()
out = root / "artifacts" / "pron-ab"; out.mkdir(parents=True, exist_ok=True)
spent = 0
for name, voice, model, text, lang in json.loads(Path(sys.argv[3]).read_text(encoding="utf-8")):
    dest = out / f"{name}.mp3"
    if dest.exists():
        print(name, "есть"); continue
    body = {"text": text, "model_id": model, "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0, "use_speaker_boost": True}}
    if lang: body["language_code"] = lang
    req = urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128", data=json.dumps(body).encode(), headers=H, method="POST")
    try:
        dest.write_bytes(urllib.request.urlopen(req, timeout=120).read()); spent += len(text); print(name, "ok", len(text))
    except urllib.error.HTTPError as e:
        print(name, "ошибка", e.code, e.read()[:300].decode("utf-8", "replace"))
print("символов:", spent)
