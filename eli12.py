#!/usr/bin/env python3
"""eli12: explain a text (an email, a README, an article) like you are 12. Super short, in your language.

  eli12 FILE            .pdf .epub .docx .odt .html .txt .md -> stdout
  eli12 < text          stdin -> stdout
  eli12 clip            clipboard -> popup window
  eli12 --popup FILE    file -> popup window (Dolphin service menu)

  -l LANG               language: a code or name (nl, de, English); both = yours, then English

Uses the same backend and config file as uktr (~/.config/uktr/config, UKTR_URL).
  ELI12_MODEL       default qwen3.5:9b (any chat model; translation-only models do not work)
  ELI12_LANG        default your language (UKTR_LANGUAGE, else the desktop's)
  ELI12_SENTENCES   default 5 (longest explanation)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import llm

MODEL = llm.conf.get("ELI12_MODEL", "qwen3.5:9b")
SENTENCES = int(llm.conf.get("ELI12_SENTENCES", "5"))
MAX_CHARS = 60000  # ~20k tokens; longer text is cut, the start of a README says the most
# The label of the to-do line, per language. Other languages get the English label.
TODO = {"en": "To do:", "nl": "Te doen:", "de": "Zu tun:", "fr": "À faire :", "es": "Por hacer:",
        "it": "Da fare:", "pt": "A fazer:", "pl": "Do zrobienia:", "uk": "Що зробити:",
        "ru": "Что сделать:"}
YOU = {"nl": "Address the reader as 'je', not 'u'. ", "de": "Address the reader as 'du'. "}
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


def clean(out):
    """Remove the [message]/[document] label. A small model ignores "no to-do line for
    documentation", but it labels reliably, so for a [document] keep only the first paragraph."""
    m = KIND.match(out)
    if not m:
        return out
    out = out[m.end():].strip()
    if m.group(1).lower() == "document":
        # After the explanation, small models add install steps or "no action needed" in many
        # wordings; drop everything after the first blank line.
        out = re.split(r"\n\s*\n", out, maxsplit=1)[0].strip()
    return out


def explain(text, lang):
    """One explanation in one language (a code such as nl). Both = call this twice: one
    language per request is far more reliable than asking a small model for two sections."""
    code, name = llm.parse_language(lang)
    rules = RULES.format(todo=TODO.get(code, "To do:"), you=YOU.get(code, ""), lang=name,
                         n=SENTENCES)
    return clean(llm.complete(rules, text[:MAX_CHARS], MODEL, temperature=0.3))


def main(argv):
    if argv[:1] in (["-h"], ["--help"]):
        return print(__doc__)
    lang = llm.conf.get("ELI12_LANG") or llm.language()[0]
    if "-l" in argv:
        i = argv.index("-l")
        if i + 1 >= len(argv):
            sys.exit("usage: -l LANG (a code or name such as nl or German, or both)")
        lang = argv[i + 1]
        del argv[i:i + 2]
    if lang.lower() == "both":
        mine = llm.parse_language(llm.conf.get("ELI12_LANG") or llm.language()[0])[0]
        langs = list(dict.fromkeys([mine, "en"]))  # yours, then English (once if yours is English)
    else:
        langs = [lang]
    gui = "--popup" in argv or argv[:1] == ["clip"]
    argv = [a for a in argv if a != "--popup"]

    with llm.errors("eli12", gui):
        if argv[:1] == ["clip"]:
            text, title = llm.clipboard(), "clipboard"
        elif argv:
            text, title = llm.extract(argv[0]), os.path.basename(argv[0])
        else:
            text, title = sys.stdin.read(), "stdin"
        if not text.strip():
            raise llm.Error(f"nothing to explain ({title} is empty)")
        if gui:
            llm.notify(f"explaining {title}…", "eli12")
        out = "\n\n".join(explain(text, lg) for lg in langs)
        if gui:
            llm.popup(f"eli12: {title}", out)
        else:
            print(out)


if __name__ == "__main__":
    main(sys.argv[1:])
