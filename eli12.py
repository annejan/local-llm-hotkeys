#!/usr/bin/env python3
"""eli12: explain a text (a README, an article) like you are 12. Super short, Dutch and English.

  eli12 FILE            .pdf .epub .docx .html .txt .md -> stdout
  eli12 < text          stdin -> stdout
  eli12 clip            clipboard -> popup window
  eli12 --popup FILE    file -> popup window (Dolphin service menu)

  -l nl | -l en         one language only (default: both, Dutch first)

Uses the same backend and config file as uktr (~/.config/uktr/config, UKTR_URL).
  ELI12_MODEL   default qwen3.5:9b (any chat model; translation-only models do not work)
"""
import json
import os
import re
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import uktr  # noqa: E402  (shares config, file extraction and the popup)

MODEL = uktr.conf.get("ELI12_MODEL", "qwen3.5:9b")
MAX_CHARS = 60000  # ~20k tokens; longer text is cut, the start of a README says the most
LANGS = {"nl": "Dutch", "en": "English"}

RULES = ("You explain things to a curious 12-year-old who is smart but knows nothing about computers. "
         "Read the text. Say what the thing IS and WHY someone would want it. Use at most 3 short "
         "sentences per language, each under 20 words. Use everyday words and one comparison from "
         "normal life (school, games, cooking, sport). Do not use abbreviations or tech words such as "
         "LLM, NPU, GPU, API, model, server, CLI, OCR. Do not list features. ")


def explain(text, langs):
    if len(langs) == 2:
        fmt = "Write the Dutch version under 'NL:' and the English version under 'EN:'. Nothing else."
    else:
        fmt = f"Write only in {LANGS[langs[0]]}, no heading. Nothing else."
    body = {"model": MODEL, "stream": False, "temperature": 0.4,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": RULES + fmt},
                         {"role": "user", "content": text[:MAX_CHARS]}]}
    req = urllib.request.Request(uktr.URL + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.load(r)["choices"][0]["message"]["content"].strip()
    if len(langs) == 2:
        # Some models put English first despite the instructions; put Dutch first.
        parts = dict(re.findall(r"(?ms)^\W*(NL|EN)\W*:?\s*(.*?)\s*(?=^\W*(?:NL|EN)\W*:|\Z)", out))
        if parts.keys() == {"NL", "EN"}:
            out = f"NL: {parts['NL']}\n\nEN: {parts['EN']}"
    return out


def main(argv):
    langs = ["nl", "en"]
    if "-l" in argv:
        i = argv.index("-l")
        lang = argv[i + 1].lower() if i + 1 < len(argv) else ""
        if lang not in LANGS:
            sys.exit("-l takes nl or en")
        langs = [lang]
        del argv[i:i + 2]
    popup = "--popup" in argv
    argv = [a for a in argv if a != "--popup"]

    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    if argv and argv[0] == "clip":
        text, title, popup = subprocess.run(["wl-paste", "--no-newline"], capture_output=True,
                                            text=True).stdout, "clipboard", True
    elif argv:
        text, title = uktr.extract(argv[0]), os.path.basename(argv[0])
    else:
        text, title = sys.stdin.read(), "stdin"

    if not text.strip():
        msg = f"nothing to explain ({title} is empty)"
        return uktr.notify(msg) if popup else sys.exit(msg)
    if popup:
        uktr.notify(f"explaining {title}…")
    try:
        out = explain(text, langs)
    except OSError as e:
        msg = f"eli12 failed: {e}"
        return uktr.notify(msg) if popup else sys.exit(msg)
    if popup:
        uktr.popup(f"eli12: {title}", out)
    else:
        print(out)


if __name__ == "__main__":
    main(sys.argv[1:])
