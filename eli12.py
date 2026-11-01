#!/usr/bin/env python3
"""eli12: explain a text (an email, a README, an article) like you are 12. Super short, in Dutch or English.

  eli12 FILE            .pdf .epub .docx .html .txt .md -> stdout
  eli12 < text          stdin -> stdout
  eli12 clip            clipboard -> popup window
  eli12 --popup FILE    file -> popup window (Dolphin service menu)

  -l nl | en | both     language (default nl, or ELI12_LANG); both = Dutch, then English

Uses the same backend and config file as uktr (~/.config/uktr/config, UKTR_URL).
  ELI12_MODEL   default qwen3.5:9b (any chat model; translation-only models do not work)
  ELI12_LANG    default nl
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
SENTENCES = int(uktr.conf.get("ELI12_SENTENCES", "5"))  # length of the explanation
MAX_CHARS = 60000  # ~20k tokens; longer text is cut, the start of a README says the most
LANGS = {"nl": "Dutch", "en": "English"}

TODO = {"nl": "Te doen:", "en": "To do:"}
YOU = {"nl": "Address the reader as 'je', not 'u'. ", "en": ""}
RULES = ("You explain texts to a smart 12-year-old. The text can be anything: an email, a README, "
         "an article, a letter.\n"
         "First line: write [message] if the text is addressed to the reader and asks them for "
         "something (an email, a letter, a notice), or [document] if it is documentation, an article "
         "or a description (a README is a [document]).\n"
         "Then, in at most {n} short sentences (each under 25 words), say what the text is about and "
         "what matters most. Skip greetings, politeness, background and repetition. Use everyday "
         "words; explain or avoid jargon and abbreviations. Use a comparison from normal life only if "
         "it really helps. Only state facts that are in the text; do not guess consequences.\n"
         "For a [message], end with one line that starts with '{todo}' and names every action the "
         "reader must take with its deadline. Copy dates exactly as the text writes them: never add a "
         "year, a weekday or a date that is not in the text.\n"
         "{you}Write only in {lang}, with no other heading. Nothing else.")
KIND = re.compile(r"^\W*\[?(message|document)\]?\W*\n?", re.I)


def explain(text, lang):
    """One explanation in one language (nl or en). Both = call this twice: one language per
    request is far more reliable than asking a small model for two sections.

    The model labels the text first. A small model ignores "no to-do line for documentation",
    but it labels reliably, so for a [document] the code keeps only the first paragraph."""
    rules = RULES.format(todo=TODO[lang], you=YOU[lang], lang=LANGS[lang], n=SENTENCES)
    body = {"model": MODEL, "stream": False, "temperature": 0.3,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": rules},
                         {"role": "user", "content": text[:MAX_CHARS]}]}
    req = urllib.request.Request(uktr.URL + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.load(r)["choices"][0]["message"]["content"].strip()
    m = KIND.match(out)
    if not m:
        return out
    out = out[m.end():].strip()
    if m.group(1).lower() == "document":
        # The explanation is the first paragraph. After it, small models add install steps or
        # "no action needed" in many wordings; drop everything after the first blank line.
        out = re.split(r"\n\s*\n", out, maxsplit=1)[0].strip()
    return out


def main(argv):
    lang = uktr.conf.get("ELI12_LANG", "nl").lower()
    if "-l" in argv:
        i = argv.index("-l")
        lang = argv[i + 1].lower() if i + 1 < len(argv) else ""
        del argv[i:i + 2]
    if lang not in (*LANGS, "both"):
        sys.exit("language must be nl, en or both")
    langs = ["nl", "en"] if lang == "both" else [lang]
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
        return uktr.notify(msg, "eli12") if popup else sys.exit(msg)
    if popup:
        uktr.notify(f"explaining {title}…", "eli12")
    try:
        out = "\n\n".join(explain(text, lg) for lg in langs)
    except OSError as e:
        msg = f"eli12 failed: {e}"
        return uktr.notify(msg, "eli12") if popup else sys.exit(msg)
    if popup:
        uktr.popup(f"eli12: {title}", out)
    else:
        print(out)


if __name__ == "__main__":
    main(sys.argv[1:])
