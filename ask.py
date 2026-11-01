#!/usr/bin/env python3
"""ask: ask the local LLM a quick question.

  ask --popup           input box -> answer in a popup window (answer also copied to the clipboard)
  ask QUESTION...       question on the command line -> stdout
  ask < question        stdin -> stdout

Uses the same backend and config file as uktr (~/.config/uktr/config, UKTR_URL).
  ASK_MODEL   default qwen3.5:9b (any chat model)
"""
import datetime
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import uktr  # noqa: E402  (shares config, chat request, notifications and the popup)

MODEL = uktr.conf.get("ASK_MODEL", "qwen3.5:9b")

RULES = ("You are a helpful assistant on the user's Linux (KDE Plasma) computer. Today is {today}. "
         "Answer in the language of the question. Be short and concrete: a direct answer first, "
         "then only the details that matter. Plain text: no markdown headings, tables or bold; a "
         "simple list or a code block is fine. If you do not know or are not sure, say so.")


def ask(question):
    today = datetime.date.today().strftime("%A %d %B %Y")
    answer = uktr.chat(RULES.format(today=today), question, MODEL, temperature=0.5)
    # Small models add markdown anyway; the popup is plain text, so drop bold and use "-" bullets.
    answer = re.sub(r"\*\*(.+?)\*\*", r"\1", answer)
    return re.sub(r"^(\s*)[*•]\s+", r"\1- ", answer, flags=re.M)


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    if argv != ["--popup"]:
        question = " ".join(argv) or sys.stdin.read()
        return print(ask(question)) if question.strip() else sys.exit("no question")

    box = subprocess.run(["kdialog", "--title", "ask", "--inputbox", f"Ask {MODEL}:"],
                         capture_output=True, text=True)
    question = box.stdout.strip()
    if box.returncode != 0 or not question:
        return  # cancelled
    uktr.notify(f"thinking about: {question[:80]}…", "ask")
    try:
        answer = ask(question)
    except OSError as e:
        return uktr.notify(f"ask failed: {e}", "ask")
    uktr.popup(f"ask: {question[:60]}", answer)


if __name__ == "__main__":
    main(sys.argv[1:])
