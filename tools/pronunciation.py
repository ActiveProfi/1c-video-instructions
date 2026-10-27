"""Apply the project's whole-word pronunciation rules to narration only."""
import json
from pathlib import Path
import re

RULES = json.loads((Path(__file__).resolve().parents[1]/'config/pronunciation.json').read_text(encoding='utf-8-sig'))['rules']

def pronounce(text):
    for rule in RULES:
        replacement = rule['pronunciation']
        def replace(match):
            word = match.group()
            if word.isupper():
                return replacement.upper()
            if word[0].isupper():
                return replacement[0].upper()+replacement[1:]
            return replacement
        text = re.sub(r'\b'+re.escape(rule['word'])+r'\b',replace,text,flags=re.IGNORECASE)
    return text
