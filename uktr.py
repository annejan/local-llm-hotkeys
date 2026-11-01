#!/usr/bin/env python3
"""uktr: rough Ukrainian (or any language) -> English translation with a local LLM.

  uktr                  stdin -> stdout
  uktr clip             clipboard -> popup window (result also copied to clipboard)
  uktr shot             select screen region -> OCR -> popup
  uktr listen [--orig]  live subtitles: system audio -> Whisper -> translation (terminal)
  uktr FILE [-o OUT]    .pdf .epub .docx .odt .html .txt .md -> OUT (default FILE.en.md)
  uktr --notify FILE    same, with notifications instead of terminal output (Dolphin menu)

Backend is any OpenAI-compatible server, set in ~/.config/uktr/config or env:
  UKTR_URL    default http://localhost:52625/v1 (FastFlowLM on the NPU; ollama: :11434/v1)
  UKTR_MODEL  default translategemma:4b
  UKTR_TO     default English
  UKTR_LISTEN_MODEL  translation model for `listen` (default UKTR_MODEL)
  UKTR_LANG   spoken language for `listen`, default uk ("" = Whisper guesses)
"""
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import llm  # noqa: E402

conf = llm.conf
MODEL = conf.get("UKTR_MODEL", "translategemma:4b")
TO = conf.get("UKTR_TO", "English")
CODES = {"english": "en", "dutch": "nl", "german": "de", "french": "fr", "spanish": "es",
         "italian": "it", "polish": "pl", "ukrainian": "uk", "russian": "ru"}
SUFFIX = CODES.get(TO.lower(), TO.lower().replace(" ", "-"))  # FILE.en.md, FILE.nl.md, ...

SYSTEM = (f"You are a translator. Translate the user's text to {TO}. "
          "Output only the translation, keep paragraph breaks and markdown, add no comments.")


def translate(text, model=None):
    model = model or MODEL
    if "hy-mt" in model.lower():
        # Hunyuan-MT is tuned on this exact user-only prompt; a system prompt makes it worse.
        return llm.chat([{"role": "user", "content": f"Translate the following segment into "
                          f"{TO}, without additional explanation.\n\n{text}"}], model)
    return llm.complete(SYSTEM, text, model)


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

RATE = 16000  # Whisper wants 16 kHz mono
HALLUCINATIONS = ("thank you", "thanks for watching", "дякую за перегляд", "субтитри",
                  "продолжение следует", "спасибо за просмотр", "редактор субтитров")


def wav_bytes(pcm):
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def transcribe(pcm, lang):
    boundary = "uktrboundary7d1f"
    fields = {"model": conf.get("UKTR_ASR_MODEL", "whisper-v3:turbo"), "response_format": "json"}
    if lang:
        fields["language"] = lang
    body = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                    for k, v in fields.items())
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
             "Content-Type: audio/wav\r\n\r\n").encode() + wav_bytes(pcm) + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(conf.get("UKTR_ASR_URL", llm.URL).rstrip("/") + "/audio/transcriptions",
                                 body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r).get("text", "").strip()


def rms(pcm):
    import array
    a = array.array("h", pcm)
    return (sum(x * x for x in a[::4]) / max(1, len(a) // 4)) ** 0.5


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
                src = transcribe(pcm, lang)
                if src and src.lower().strip(" .!…") not in HALLUCINATIONS:
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
            level = rms(block)
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
