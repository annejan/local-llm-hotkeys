#!/usr/bin/env python3
"""uktr: rough translation of Ukrainian (or any language) to your language with a local LLM.

  uktr                  stdin -> stdout
  uktr clip             clipboard -> popup window (result also copied to clipboard)
  uktr shot             select screen region -> OCR -> popup
  uktr listen [--orig]  live subtitles: system audio -> Whisper -> translation (terminal)
  uktr FILE [-o OUT]    .pdf .epub .docx .odt .html .txt .md -> OUT (default FILE.<lang>.md)
  uktr --notify FILE    same, with notifications instead of terminal output (Dolphin menu)

Backend is any OpenAI-compatible server, set in ~/.config/uktr/config or env:
  UKTR_URL    default http://localhost:52625/v1 (FastFlowLM on the NPU; ollama: :11434/v1)
  UKTR_MODEL  default translategemma:4b
  UKTR_FILE_MODEL  translation model for FILE and --notify (default UKTR_MODEL): a document
              can wait for a slower, more careful model
  UKTR_TO     target language, default your language (UKTR_LANGUAGE, else the desktop's)
  UKTR_LISTEN_MODEL  translation model for `listen` (default UKTR_MODEL)
  UKTR_LANG   spoken language for `listen`, default uk ("" = Whisper guesses)
"""
import errno
import os
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import llm

conf = llm.conf
MODEL = conf.get("UKTR_MODEL", "translategemma:4b")
FILE_MODEL = conf.get("UKTR_FILE_MODEL") or MODEL
SUFFIX, TO = llm.parse_language(conf["UKTR_TO"]) if conf.get("UKTR_TO") else llm.language()
# SUFFIX names the output file: FILE.nl.md for Dutch, FILE.en.md for English, ...

# TranslateGemma's own prompt (from its model card), without the source language, which uktr
# does not know. In the user message: with a system prompt, translategemma translated Ukrainian
# news into Portuguese and a Ukrainian sentence into Russian when Dutch was asked. A shorter
# user-message instruction ("Translate the following text into Dutch (nl).") fixed that, but
# returned Ukrainian news of 1284 characters and more untranslated; this prompt translated it at
# every length, and 16 of 16 texts in 8 languages into Dutch and English. Temperature 0: at a
# higher temperature translategemma echoes a longer text untranslated now and then.
PROMPT = ("You are a professional translator to {to} ({code}). Your goal is to accurately convey "
          "the meaning and nuances of the original text while adhering to {to} grammar, vocabulary, "
          "and cultural sensitivities.\nProduce only the {to} translation, without any additional "
          "explanations or commentary. Please translate the following text into {to}:\n\n\n{text}")


def messages(text, model, to):
    """The request to translate text to `to` (a language name or code) with this model."""
    code, name = llm.parse_language(to)
    if "hy-mt" in model.lower():
        # Hunyuan-MT is tuned on this exact user-only prompt; anything else makes it worse.
        return [{"role": "user", "content": f"Translate the following segment into {name}, "
                 f"without additional explanation.\n\n{text}"}]
    return [{"role": "user", "content": PROMPT.format(to=name, code=code, text=text)}]


def translate(text, model=None, to=None):
    """Translate text to `to` (a language name; default TO, your language)."""
    model, to = model or MODEL, to or TO
    return llm.chat(messages(text, model, to), model, temperature=0)


def translate_long(text, out, progress=False, model=None):
    parts = list(llm.chunks(text))
    for i, part in enumerate(parts, 1):
        if progress:
            print(f"\r[{i}/{len(parts)}]", end="", file=sys.stderr, flush=True)
        out.write(translate(part, model) + "\n\n")
        out.flush()
    if progress:
        print(file=sys.stderr)


def translate_popup(text, source):
    if not text.strip():
        return llm.notify(f"nothing to translate ({source} is empty)", "uktr")
    llm.notify(f"translating {len(text)} chars…", "uktr")
    with tempfile.TemporaryFile("w+", encoding="utf-8") as out:
        translate_long(text, out)
        out.seek(0)
        llm.popup(f"uktr → {TO}", out.read().strip())


def open_output(path):
    """Open the output file to write, also when it exists. A link there is refused: a downloaded
    folder can hold FILE.nl.md as a link to ~/.bashrc, and the translation would replace that."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o666)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise llm.Error(f"{path} is a link; not written") from None
        raise llm.Error(str(e)) from None
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:  # a hard link is another file's content too
        os.close(fd)
        raise llm.Error(f"{path} is not a plain file, or has more names (a hard link); not written")
    os.ftruncate(fd, 0)
    return os.fdopen(fd, "w", encoding="utf-8")


def translate_file(src, dst=None, notify=False):
    dst = dst or f"{os.path.splitext(src)[0]}.{SUFFIX}.md"
    text = llm.extract(src)
    if notify:
        llm.notify(f"translating {os.path.basename(src)}…", "uktr")
    else:
        # plain(): a file name from someone else's archive can hold escape codes
        print(llm.plain(f"{src} → {dst} ({len(text)} chars, model {FILE_MODEL})"), file=sys.stderr)
    with open_output(dst) as out:
        translate_long(text, out, progress=not notify, model=FILE_MODEL)
    if notify:
        llm.notify(f"done: {dst}", "uktr", 10000)


def screen_text():
    """Select a screen region with spectacle and OCR it with tesseract ("" if cancelled)."""
    with tempfile.TemporaryDirectory() as d:
        png = os.path.join(d, "shot.png")
        subprocess.run(["spectacle", "-r", "-b", "-n", "-o", png])
        if not os.path.exists(png):
            return None
        return llm.run(["tesseract", png, "-", "-l", conf.get("UKTR_OCR", "ukr+eng")], "OCR")


# --- uktr listen ---

RATE = llm.RATE


def listen(argv):
    """Live subtitles: system audio -> Whisper -> translate -> terminal."""
    import queue
    import threading
    import time
    show_orig = "--orig" in argv
    listen_model = conf.get("UKTR_LISTEN_MODEL", MODEL)  # fast model: subtitles must keep up
    lang = conf.get("UKTR_LANG", "uk")  # "" = let Whisper guess (it guesses badly on short chunks)
    min_s, max_s = 4, float(conf.get("UKTR_LISTEN_MAX", "12"))
    silence = float(conf.get("UKTR_SILENCE", "300"))  # int16 RMS below this = quiet
    win = RATE // 4 * 2  # 0.25 s of s16 audio, in bytes
    rec = subprocess.Popen(["parec", "-d", "@DEFAULT_MONITOR@", "--format=s16le", f"--rate={RATE}",
                            "--channels=1", "--raw", "--latency-msec=250"], stdout=subprocess.PIPE)
    jobs = queue.Queue()

    def worker():
        while True:
            t, pcm = jobs.get()
            try:
                src = llm.transcribe(pcm, lang)
                if src:
                    stamp = time.strftime("%H:%M:%S", time.localtime(t))
                    if show_orig:
                        print(f"\033[2m{stamp}  {src}\033[0m", flush=True)
                    print(f"{stamp}  {translate(src, listen_model)}", flush=True)
            except Exception as e:  # keep going: one bad chunk must not stop the subtitles
                print(f"[uktr] {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            if jobs.qsize() > 3:
                print(f"[uktr] falling behind ({jobs.qsize()} chunks queued)", file=sys.stderr, flush=True)

    threading.Thread(target=worker, daemon=True).start()
    print(f"uktr listen: system audio → {lang or 'auto'} → {TO}. Ctrl+C to stop.", file=sys.stderr)
    chunk, levels, start = bytearray(), [], time.time()
    try:
        while (block := rec.stdout.read(win)):
            level = llm.rms(block)
            chunk += block
            levels.append(level)
            secs = len(chunk) / (RATE * 2)
            if secs < min_s or (level >= silence and secs < max_s):
                continue
            # Cut after the quietest 0.25 s window past min_s (a pause between words, hopefully)
            # and carry the rest over, so continuous speech is not split mid-word at max_s.
            first = int(min_s * 4) - 1
            cut = len(levels) if level < silence else first + min(range(len(levels) - first),
                                                                  key=lambda i: levels[first + i]) + 1
            if sum(lv >= silence for lv in levels[:cut]) >= 4:  # at least ~1 s of sound
                jobs.put((start, bytes(chunk[:cut * win])))
            chunk, levels = chunk[cut * win:], levels[cut:]
            start = time.time() - len(chunk) / (RATE * 2)
    except KeyboardInterrupt:
        pass
    finally:
        rec.terminate()


def main(argv):
    if argv[:1] in (["-h"], ["--help"]):
        return print(__doc__)
    gui = argv[:1] in (["clip"], ["shot"], ["--notify"])
    with llm.errors("uktr", gui):
        if not argv:
            translate_long(sys.stdin.read(), sys.stdout)
        elif argv[0] == "clip":
            translate_popup(llm.clipboard(), "clipboard")
        elif argv[0] == "shot":
            text = screen_text()
            if text is not None:  # None: selection cancelled
                translate_popup(text, "OCR")
        elif argv[0] == "listen":
            listen(argv[1:])
        elif argv[0] == "--notify" and len(argv) == 2:
            translate_file(argv[1], notify=True)
        elif len(argv) == 1:
            translate_file(argv[0])
        elif len(argv) == 3 and argv[1] == "-o":
            translate_file(argv[0], argv[2])
        else:
            sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
