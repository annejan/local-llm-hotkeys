"""Shared code for uktr, eli12, fixtext and ask: config, the LLM server, clipboard, popups.

Config: ~/.config/uktr/config (KEY=value lines, # comments), overridden by environment
variables that start with UKTR_, ELI12_, FIX_ or ASK_.
"""
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

CONFIG = os.path.expanduser("~/.config/uktr/config")
PREFIXES = ("UKTR_", "ELI12_", "FIX_", "ASK_")


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


class Error(Exception):
    """A problem to show to the user as it is, without a traceback."""


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
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        raise Error(f"unexpected answer from the server: {str(data)[:200]}") from None


def complete(system, text, model, temperature=0.2):
    """The reply to one text with a system prompt."""
    return chat([{"role": "system", "content": system}, {"role": "user", "content": text}],
                model, temperature)


def chat_stream(messages, model, temperature=0.5, timeout=600):
    """Yield the reply in pieces, as the model writes it."""
    with _request(_body(messages, model, temperature, True), timeout) as r:
        for line in r:
            line = line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            chunk = json.loads(data)
            if "error" in chunk:
                raise Error(f"server error: {chunk['error']}")
            piece = (chunk.get("choices") or [{}])[0].get("delta", {}).get("content")
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
    if ext == ".pdf":
        text = run(["pdftotext", "-layout", path, "-"], "PDF files")
        if not text.strip():
            raise Error("no text layer in this PDF (scanned?); OCR it first, e.g. ocrmypdf")
        return text
    if ext in (".epub", ".docx", ".html", ".htm", ".odt"):
        return run(["pandoc", "-t", "plain", "--wrap=none", path], f"{ext} files")
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
    subprocess.run(["notify-send", "-a", app, "-t", str(timeout), app, msg])


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
        msg = str(e) if isinstance(e, Error) else f"{type(e).__name__}: {e}"
        if gui:
            notify(f"failed: {msg}", app, 10000)
            sys.exit(1)
        sys.exit(f"{app}: {msg}")
