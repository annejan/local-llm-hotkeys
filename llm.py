"""Shared code for uktr, eli12, fixtext, ask and dictate: config, the LLM server, clipboard, popups.

Config: ~/.config/uktr/config (KEY=value lines, # comments), overridden by environment
variables that start with UKTR_, ELI12_, FIX_, ASK_ or DICTATE_.
"""
import contextlib
import html
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

CONFIG = os.path.expanduser("~/.config/uktr/config")
PREFIXES = ("UKTR_", "ELI12_", "FIX_", "ASK_", "DICTATE_")


def read_config(path=CONFIG, environ=None):
    conf = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                key, sep, val = line.strip().partition("=")
                if sep and key and not key.startswith("#"):
                    conf[key.strip()] = val.split(" #")[0].strip()  # "KEY=val   # comment"
    except FileNotFoundError:
        pass
    environ = os.environ if environ is None else environ
    conf.update({k: v for k, v in environ.items() if k.startswith(PREFIXES)})
    return conf


conf = read_config()
URL = conf.get("UKTR_URL", "http://localhost:52625/v1").rstrip("/")  # FastFlowLM on the NPU


LANGUAGES = {"en": "English", "nl": "Dutch", "de": "German", "fr": "French", "es": "Spanish",
             "it": "Italian", "pt": "Portuguese", "pl": "Polish", "uk": "Ukrainian", "ru": "Russian",
             "sv": "Swedish", "da": "Danish", "nb": "Norwegian", "fi": "Finnish", "cs": "Czech",
             "tr": "Turkish", "el": "Greek", "ja": "Japanese", "zh": "Chinese", "ko": "Korean"}


def parse_language(value):
    """'nl', 'nl_NL.UTF-8' or 'Dutch' -> ('nl', 'Dutch'). An unknown name is kept as it is."""
    value = value.strip()
    code = value.split(".")[0].split("_")[0].split("-")[0].lower()
    if code in LANGUAGES:
        return code, LANGUAGES[code]
    for c, name in LANGUAGES.items():
        if name.lower() == value.lower():
            return c, name
    return value.lower().replace(" ", "-"), value


def language(environ=None):
    """Your language: UKTR_LANGUAGE, else the language of the desktop (LANGUAGE, LC_ALL,
    LC_MESSAGES, LANG, in that order). Translations and explanations are written in it."""
    if conf.get("UKTR_LANGUAGE"):
        return parse_language(conf["UKTR_LANGUAGE"])
    environ = os.environ if environ is None else environ
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = environ.get(var, "").split(":")[0]
        if value and value not in ("C", "POSIX") and not value.startswith("C."):
            return parse_language(value)
    return "en", "English"


class Error(Exception):
    """A problem to show to the user as it is, without a traceback."""


# C0 and C1 control characters except tab and newline, and the Unicode characters that change
# the direction or the visibility of text (bidi overrides, zero-width characters).
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")


def plain(text):
    """Text without control characters; newlines and tabs stay. What a model or Whisper writes
    can come from a document or a web page of someone else. Escape sequences in it make a
    terminal act (set the title, write the clipboard, hide a link), an escape or a carriage
    return in text that is pasted into a terminal can run a command, and a bidi override
    shows text in another order than it is."""
    return CONTROL.sub("", text)


# --- the LLM server (OpenAI-compatible) ---

def _request(body, timeout):
    req = urllib.request.Request(URL + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _body(messages, model, temperature, stream):
    # Thinking off: these are quick tools, and a thinking model takes many times longer.
    return {"model": model, "temperature": temperature, "stream": stream,
            "chat_template_kwargs": {"enable_thinking": False}, "messages": messages}


def chat(messages, model, temperature=0.2, timeout=600):
    """The whole reply to a list of chat messages."""
    with _request(_body(messages, model, temperature, False), timeout) as r:
        data = json.load(r)
    try:
        return plain(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError):
        raise Error(f"unexpected answer from the server: {str(data)[:200]}") from None


def complete(system, text, model, temperature=0.2):
    """The reply to one text with a system prompt."""
    return chat([{"role": "system", "content": system}, {"role": "user", "content": text}],
                model, temperature)


class Cancel:
    """Stops a chat_stream() from another thread at once, also while it still waits for the
    first word (the model may be loading). Pass it to chat_stream(), then call cancel()."""

    def __init__(self):
        self.cancelled, self.sock = False, None

    def cancel(self):
        self.cancelled = True
        if self.sock:
            with contextlib.suppress(OSError):
                self.sock.shutdown(socket.SHUT_RDWR)  # wakes up the thread that reads


def _stream_lines(body, timeout, cancel):
    """The lines of a streaming reply. With http.client instead of urllib, so the socket is
    known before the server answers, and cancel() can shut it down."""
    url = urllib.parse.urlsplit(URL + "/chat/completions")
    kind = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    conn = kind(url.hostname, url.port, timeout=timeout)
    try:
        conn.connect()
        cancel.sock = conn.sock
        if cancel.cancelled:
            return
        conn.request("POST", url.path, json.dumps(body).encode(),
                     {"Content-Type": "application/json"})
        r = conn.getresponse()
        if r.status != 200:
            raise Error(f"server error {r.status}: {r.read(300).decode('utf-8', 'replace')}")
        yield from r
    except (OSError, http.client.HTTPException):
        if not cancel.cancelled:
            raise
    finally:
        conn.close()


def chat_stream(messages, model, temperature=0.5, timeout=600, cancel=None):
    """Yield the reply in pieces, as the model writes it. cancel: a Cancel, to stop it."""
    cancel = cancel or Cancel()
    for line in _stream_lines(_body(messages, model, temperature, True), timeout, cancel):
        if cancel.cancelled:
            return
        line = line.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        chunk = json.loads(data)
        if "error" in chunk:
            raise Error(f"server error: {chunk['error']}")
        piece = plain((chunk.get("choices") or [{}])[0].get("delta", {}).get("content") or "")
        if piece:
            yield piece


def chunks(text, size=1500):
    """Split text at paragraph breaks into parts of about `size` characters.
    Small models drift on long input, so long text goes to the model in parts."""
    buf = ""
    for para in text.split("\n\n"):
        if buf and len(buf) + len(para) > size:
            yield buf
            buf = ""
        buf = f"{buf}\n\n{para}" if buf else para
    if buf.strip():
        yield buf


# --- audio: Whisper speech-to-text ---

RATE = 16000  # Whisper wants 16 kHz mono
# Whisper invents these on silence or noise; such a result counts as nothing heard.
HALLUCINATIONS = ("thank you", "thanks for watching", "дякую за перегляд", "субтитри",
                  "продолжение следует", "спасибо за просмотр", "редактор субтитров",
                  "bedankt voor het kijken", "ondertiteling", "untertitel")


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


def transcribe(pcm, lang=""):
    """Text of 16 kHz mono s16 audio ("" for nothing or a known hallucination).
    lang: a code such as uk or nl; "" lets Whisper guess (unreliable on short audio)."""
    boundary = "uktrboundary7d1f"
    fields = {"model": conf.get("UKTR_ASR_MODEL", "whisper-v3:turbo"), "response_format": "json"}
    if lang:
        fields["language"] = lang
    body = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                    for k, v in fields.items())
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
             "Content-Type: audio/wav\r\n\r\n").encode() + wav_bytes(pcm) + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(conf.get("UKTR_ASR_URL", URL).rstrip("/") + "/audio/transcriptions",
                                 body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        text = plain(json.load(r).get("text", "")).strip()
    if not any(c.isalnum() for c in text) or text.lower().strip(" .!…") in HALLUCINATIONS:
        return ""  # "***", "..." or a known hallucination: nothing was said
    return text


def rms(pcm):
    """Loudness of s16 audio (every 4th sample is enough). The average is taken off first:
    some microphones add a constant offset (here -4678) that is not sound."""
    import array
    a = array.array("h", pcm)[::4]
    if not a:
        return 0.0
    mean = sum(a) / len(a)
    return (sum((x - mean) ** 2 for x in a) / len(a)) ** 0.5


# --- files ---

def run(cmd, what):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except FileNotFoundError:
        raise Error(f"{cmd[0]} is not installed (needed for {what})") from None
    except subprocess.CalledProcessError as e:
        raise Error(f"{cmd[0]} failed: {e.stderr.strip()[:200]}") from None


def extract(path):
    """The text of a document: .pdf .epub .docx .odt .html, or any text file."""
    ext = os.path.splitext(path)[1].lower()
    path = os.path.abspath(path)  # a file name that starts with "-" is not read as an option
    if ext == ".pdf":
        text = run(["pdftotext", "-layout", path, "-"], "PDF files")
        if not text.strip():
            raise Error("no text layer in this PDF (scanned?); OCR it first, e.g. ocrmypdf")
        return text
    if ext in (".epub", ".docx", ".html", ".htm", ".odt"):
        # --sandbox: pandoc reads no other file and nothing from the network for this document.
        return run(["pandoc", "--sandbox", "-t", "plain", "--wrap=none", path], f"{ext} files")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError as e:
        raise Error(str(e)) from None


# --- desktop: clipboard, notifications, popup window ---

def clipboard():
    """The clipboard text ("" when it is empty or holds no text, such as an image)."""
    r = subprocess.run(["wl-paste", "--no-newline", "--type", "text"], capture_output=True)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def copy(text):
    subprocess.run(["wl-copy"], input=text, text=True)


def notify(msg, app, timeout=3000):
    # The body is markup (Plasma shows links and bold); msg can hold copied text or a file name.
    subprocess.run(["notify-send", "-a", app, "-t", str(timeout), app, html.escape(msg)])


def popup(title, text):
    """Show text in a window; it is also copied to the clipboard."""
    copy(text)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        subprocess.run(["kdialog", "--title", title, "--textbox", f.name, "800", "500"])


@contextlib.contextmanager
def errors(app, gui):
    """Report any failure: a notification when started from a hotkey or Dolphin (gui=True,
    nobody sees stderr there), else a one-line message on stderr and exit status 1."""
    try:
        yield
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        msg = plain(str(e) if isinstance(e, Error) else f"{type(e).__name__}: {e}")
        if gui:
            notify(f"failed: {msg}", app, 10000)
            sys.exit(1)
        sys.exit(f"{app}: {msg}")
