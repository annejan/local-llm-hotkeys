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
  UKTR_TO     target language, default your language (UKTR_LANGUAGE, else the desktop's)
  UKTR_LISTEN_MODEL  translation model for `listen` (default UKTR_MODEL)
  UKTR_LANG   spoken language for `listen`, default uk ("" = Whisper guesses)
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import llm

conf = llm.conf
MODEL = conf.get("UKTR_MODEL", "translategemma:4b")
SUFFIX, TO = llm.parse_language(conf["UKTR_TO"]) if conf.get("UKTR_TO") else llm.language()
# SUFFIX names the output file: FILE.nl.md for Dutch, FILE.en.md for English, ...

SYSTEM = ("You are a translator. Translate the user's text to {to}. "
          "Output only the translation, keep paragraph breaks and markdown, add no comments.")


def translate(text, model=None, to=None):
    """Translate text to `to` (a language name; default TO, your language)."""
    model, to = model or MODEL, to or TO
    if "hy-mt" in model.lower():
        # Hunyuan-MT is tuned on this exact user-only prompt; a system prompt makes it worse.
        return llm.chat([{"role": "user", "content": f"Translate the following segment into "
                          f"{to}, without additional explanation.\n\n{text}"}], model)
    return llm.complete(SYSTEM.format(to=to), text, model)


def translate_long(text, out, progress=False):
    parts = list(llm.chunks(text))
    for i, part in enumerate(parts, 1):
        if progress:
            print(f"\r[{i}/{len(parts)}]", end="", file=sys.stderr, flush=True)
        out.write(translate(part) + "\n\n")
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


def translate_file(src, dst=None, notify=False):
    dst = dst or f"{os.path.splitext(src)[0]}.{SUFFIX}.md"
    text = llm.extract(src)
    if notify:
        llm.notify(f"translating {os.path.basename(src)}…", "uktr")
    else:
        print(f"{src} → {dst} ({len(text)} chars, model {MODEL})", file=sys.stderr)
    with open(dst, "w", encoding="utf-8") as out:
        translate_long(text, out, progress=not notify)
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
