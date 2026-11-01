#!/usr/bin/env python3
"""uktr: rough Ukrainian (or any language) -> English translation with a local LLM.

  uktr                  stdin -> stdout
  uktr clip             clipboard -> popup window (result also copied to clipboard)
  uktr shot             select screen region -> OCR -> popup
  uktr listen [--orig]  live subtitles: system audio -> Whisper -> translation (terminal)
  uktr FILE [-o OUT]    .pdf .epub .docx .html .txt .md -> OUT (default FILE.en.md)

Backend is any OpenAI-compatible server, set in ~/.config/uktr/config or env:
  UKTR_URL    default http://localhost:11434/v1  (ollama; FastFlowLM NPU = http://localhost:52625/v1)
  UKTR_MODEL  default gemma4:12b
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

conf = {}
try:
    with open(os.path.expanduser("~/.config/uktr/config")) as f:
        for line in f:
            key, sep, val = line.strip().partition("=")
            if sep and not key.startswith("#"):
                conf[key.strip()] = val.strip()
except FileNotFoundError:
    pass
conf.update({k: v for k, v in os.environ.items() if k.startswith("UKTR_")})

URL = conf.get("UKTR_URL", "http://localhost:11434/v1").rstrip("/")
MODEL = conf.get("UKTR_MODEL", "gemma4:12b")
TO = conf.get("UKTR_TO", "English")
CHUNK = 1500  # characters per request; small models drift on long input

SYSTEM = (f"You are a translator. Translate the user's text to {TO}. "
          "Output only the translation, keep paragraph breaks and markdown, add no comments.")


def translate(text, model=None):
    model = model or MODEL
    if "hy-mt" in model.lower():
        # Hunyuan-MT is tuned on this exact user-only prompt; a system prompt makes it worse.
        messages = [{"role": "user", "content":
                     f"Translate the following segment into {TO}, without additional explanation.\n\n{text}"}]
    else:
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]
    body = {"model": model, "temperature": 0.2, "stream": False, "messages": messages}
    req = urllib.request.Request(URL + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip()


def chat(system, text, model, temperature=0.2, timeout=600):
    """One chat request with thinking off. Used by eli12 and fixtext."""
    body = {"model": model, "stream": False, "temperature": temperature,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": text}]}
    req = urllib.request.Request(URL + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip()

def chat_stream(messages, model, temperature=0.5, timeout=600):
    """Stream a chat reply (thinking off): yields text pieces as the model writes them."""
    body = {"model": model, "stream": True, "temperature": temperature,
            "chat_template_kwargs": {"enable_thinking": False}, "messages": messages}
    req = urllib.request.Request(URL + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for line in r:
            line = line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            piece = json.loads(data)["choices"][0].get("delta", {}).get("content")
            if piece:
                yield piece

def chunks(text):
    buf = ""
    for para in text.split("\n\n"):
        if buf and len(buf) + len(para) > CHUNK:
            yield buf
            buf = ""
        buf = f"{buf}\n\n{para}" if buf else para
    if buf.strip():
        yield buf


def translate_long(text, out, progress=False):
    parts = list(chunks(text))
    for i, part in enumerate(parts, 1):
        if progress:
            print(f"\r[{i}/{len(parts)}]", end="", file=sys.stderr, flush=True)
        out.write(translate(part) + "\n\n")
        out.flush()
    if progress:
        print(file=sys.stderr)


def extract(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        text = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True, check=True).stdout
        if text.strip():
            return text
        sys.exit("no text layer in PDF (scanned?) - OCR it first, e.g. ocrmypdf -l ukr")
    if ext in (".epub", ".docx", ".html", ".htm", ".odt"):
        return subprocess.run(["pandoc", "-t", "plain", "--wrap=none", path], capture_output=True, text=True, check=True).stdout
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def popup(title, text):
    subprocess.run(["wl-copy"], input=text, text=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(text)
    subprocess.run(["kdialog", "--title", title, "--textbox", f.name, "800", "500"])
    os.unlink(f.name)


def notify(msg, app="uktr"):
    subprocess.run(["notify-send", "-a", app, "-t", "3000", app, msg])


def gui(text, source):
    if not text.strip():
        notify(f"nothing to translate ({source} empty)")
        return
    notify(f"translating {len(text)} chars…")
    try:
        with tempfile.TemporaryFile("w+") as out:
            translate_long(text, out)
            out.seek(0)
            popup(f"uktr → {TO}", out.read().strip())
    except OSError as e:
        notify(f"translation failed: {e}")


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
    req = urllib.request.Request(conf.get("UKTR_ASR_URL", URL).rstrip("/") + "/audio/transcriptions", body,
                                 {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["text"].strip()


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
                if not src or src.lower().strip(" .!…") in HALLUCINATIONS:
                    continue
                stamp = time.strftime("%H:%M:%S", time.localtime(t))
                if show_orig:
                    print(f"\033[2m{stamp}  {src}\033[0m", flush=True)
                print(f"{stamp}  {translate(src, listen_model)}", flush=True)
            except OSError as e:
                print(f"[uktr] {e}", file=sys.stderr, flush=True)
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
    if not argv:
        translate_long(sys.stdin.read(), sys.stdout)
    elif argv[0] == "clip":
        gui(subprocess.run(["wl-paste", "--no-newline"], capture_output=True, text=True).stdout, "clipboard")
    elif argv[0] == "shot":
        with tempfile.TemporaryDirectory() as d:
            png = os.path.join(d, "shot.png")
            subprocess.run(["spectacle", "-r", "-b", "-n", "-o", png])
            if not os.path.exists(png):
                return
            ocr = subprocess.run(["tesseract", png, "-", "-l", conf.get("UKTR_OCR", "ukr+eng")],
                                 capture_output=True, text=True).stdout
        gui(ocr, "OCR")
    elif argv[0] == "listen":
        listen(argv[1:])
    elif argv[0] in ("-h", "--help"):
        print(__doc__)
    else:
        src = argv[0]
        dst = argv[2] if len(argv) > 2 and argv[1] == "-o" else os.path.splitext(src)[0] + ".en.md"
        text = extract(src)
        print(f"{src} → {dst} ({len(text)} chars, model {MODEL})", file=sys.stderr)
        with open(dst, "w", encoding="utf-8") as out:
            translate_long(text, out, progress=True)


if __name__ == "__main__":
    main(sys.argv[1:])
