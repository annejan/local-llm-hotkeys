#!/usr/bin/env python3
"""aikey: chat with the local LLM. The conversation keeps its history until you start a new one.

  aikey --popup         chat window (KDE hotkey Meta+Shift+Q or Copilot; needs PySide6)
  aikey --screen        screenshot of the active window -> chat window that explains it
                        (Ctrl+Alt+Copilot or Meta+Shift+W); ask follow-up questions about it
  aikey --region        the same for a screen region that you select (Meta+Shift+X)
  aikey                 chat in the terminal (/new starts a new chat, Ctrl+D or /quit quits)
  aikey --list          saved chats, newest first
  aikey --resume [N]    continue saved chat N in the terminal (default 1, the newest)
  aikey QUESTION...     one question on the command line -> stdout
  aikey < question      stdin -> stdout

Window keys: Enter sends, Shift+Enter is a new line, Esc stops an answer or closes the
window, Ctrl+N starts a new chat, Ctrl+H shows or hides the saved chats, Ctrl+Shift+C copies
the last answer. Click a saved chat to continue it; right-click or Del removes it.

Chats from the window and the terminal chat are saved after every answer, as JSON in
~/.local/share/local-llm-hotkeys/chats/. One-shot questions (aikey QUESTION, aikey < question)
are not saved.

Uses the shared config file (~/.config/local-llm-hotkeys/config, HOTKEYS_URL).
  AIKEY_MODEL   default qwen3.5:9b (any chat model)
  AIKEY_MODELS  models to choose from in the window, such as qwen3.5:9b,gemma4-it:e4b,gpt-oss:20b;
                with two or more, a menu picks the model per chat (/model in the terminal)
  AIKEY_HISTORY default 1; 0 = do not save chats
  AIKEY_SCREEN_PROMPT  the first question for --screen ({language} = your language)
  (before 0.4 these were ASK_MODEL, ASK_MODELS ...; those names still work)
"""
import base64
import contextlib
import datetime
import functools
import html
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import llm

MODEL = llm.conf.get("AIKEY_MODEL", "qwen3.5:9b")
# Models to choose from (AIKEY_MODELS=a,b,c). Not the server's list: FastFlowLM lists every model it
# knows, also the ones that are not downloaded, and does not say which can chat or read images.
MODELS = list(dict.fromkeys(m.strip() for m in llm.conf.get("AIKEY_MODELS", "").split(",") if m.strip()))


def choosing():
    """Choosing the model per chat is on: AIKEY_MODELS names two or more. Off, nothing changes."""
    return len(MODELS) >= 2
HISTORY_CHARS = 24000  # older turns are dropped so the prompt stays inside the context window
SAVE = llm.conf.get("AIKEY_HISTORY", "1") != "0"


def chats_folder(data_home):
    """Where the chats are saved. Before 0.4 that was uktr/ask; until install.sh has moved
    that folder, it is used as it is."""
    new, old = os.path.join(data_home, "local-llm-hotkeys", "chats"), os.path.join(data_home, "uktr", "ask")
    return old if os.path.isdir(old) and not os.path.isdir(new) else new


SAVE_DIR = chats_folder(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"))

SCREEN_PROMPT = llm.conf.get("AIKEY_SCREEN_PROMPT", (
    "What is on this screen? Say briefly what it shows and what matters. If there is an error, "
    "a question or a choice to make, explain it and say what to do. Answer in {language}."))
IMAGE_SIZE = 1280  # longest side in pixels; bigger is slower and does not read better
IMAGE_CHARS = 2000  # what an image counts for in HISTORY_CHARS
MAX_IMAGES = 4  # pasted images per question; each one is about IMAGE_CHARS of the history

RULES = ("You are a helpful assistant on the user's computer: {system}. Today is {today}. "
         "Answer in the language of the user's last message. Be short and concrete: a direct "
         "answer first, then only the details that matter. Markdown is fine. If you do not know "
         "or are not sure, say so.")


@functools.cache
def system():
    """For example "openSUSE Tumbleweed, KDE Plasma 6.8.1 on Wayland", so answers fit this
    computer (zypper, not apt; Plasma 6 settings, not Plasma 5)."""
    parts = []
    try:
        with open("/etc/os-release") as f:
            info = dict(line.rstrip().split("=", 1) for line in f if "=" in line)
        parts.append(info.get("PRETTY_NAME", "Linux").strip('"'))
    except OSError:
        parts.append("Linux")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if "KDE" in desktop:
        try:
            out = subprocess.run(["plasmashell", "--version"], capture_output=True, text=True,
                                 timeout=3).stdout.split()
            desktop = f"KDE Plasma {out[-1]}" if out else "KDE Plasma"
        except (OSError, subprocess.TimeoutExpired):
            desktop = "KDE Plasma"
    session = os.environ.get("XDG_SESSION_TYPE", "")
    if desktop:
        parts.append(desktop + (f" on {session.capitalize()}" if session else ""))
    return ", ".join(parts)


def can_window():
    """The parts of PySide6 that the window needs are there. Debian splits PySide6 into packages
    (python3-pyside6.qtwidgets ...): without QtWidgets, window() would open Konsole instead."""
    try:
        return all(importlib.util.find_spec(f"PySide6.{m}") for m in ("QtCore", "QtGui", "QtWidgets"))
    except ImportError:  # no PySide6 at all
        return False


class First:
    """How a chat from another tool makes its first answer; every part is optional.
    rules: the system prompt. clean(answer): what is shown and kept, also while it streams in.
    stream(question, cancel): yields the answer in pieces, instead of one request to the model
    (uktr translates a long text part by part, with its own model). done(answer): called when
    the answer is complete (not when it was stopped or failed)."""

    def __init__(self, rules=None, clean=None, stream=None, done=None):
        self.rules, self.clean, self.stream, self.done = rules, clean, stream, done


class Chat:
    """A conversation: the messages so far, and the request for the next answer.
    With save=True it is written to SAVE_DIR after every answer.

    Another tool can start a chat with its own first answer (eli12 explains a text, uktr
    translates it, then you ask about it): kind names the tool, model is the model for the
    answers, first (a First) makes the first answer, and follow_up is added to the rules for
    the answers after it."""

    def __init__(self, save=False, path=None, turns=None, model=None, kind="", first=None, follow_up=""):
        self.save, self.path = save and SAVE, path
        self.turns = turns or []  # {"role": "user" | "assistant", "content": ...}
        self.model, self.kind, self.first, self.follow_up = model or MODEL, kind, first, follow_up

    @classmethod
    def load(cls, path):
        """A saved chat. ValueError if the file is not one (only you can write there, but a
        broken file must not take the window down)."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        turns = data.get("turns") if isinstance(data, dict) else None
        if not isinstance(turns, list) or not all(valid_turn(t) for t in turns):
            raise ValueError(f"{path} is not a saved chat")
        kind = data.get("kind") if data.get("kind") in KINDS else ""
        model, follow_up = KINDS[kind]() if kind else (MODEL, "")
        if choosing() and data.get("model") in MODELS:  # picked for this chat, and still offered
            model = data["model"]
        return cls(save=True, path=path, turns=turns, model=model, kind=kind, follow_up=follow_up)

    @property
    def title(self):
        first = next((t["content"] for t in self.turns if t["role"] == "user"), "")
        if images_of(first) or self.kind:
            # Screenshot chats start with the same question, and an explained text starts with
            # "Dear ..."; the answer says what it was about.
            answer = next((t["content"] for t in self.turns if t["role"] == "assistant"), "")
            icon = ICONS.get(self.kind, "📷 ")
            return icon + title_of(re.sub(r"[*#`>_]", "", answer) or "screenshot")
        return title_of(first)

    def first_answer(self):
        """True while the answer that is asked or written now is the first one."""
        return sum(t["role"] == "user" for t in self.turns) <= 1

    def shown(self, answer):
        """The answer as it is shown and kept: the first one goes through first's clean, also
        while it streams in (a half label such as "[mess" is not shown)."""
        if not (self.first and self.first.clean and self.first_answer()):
            return answer
        if answer.lstrip().startswith("[") and "]" not in answer:
            return ""
        return self.first.clean(answer)

    def write(self):
        if not self.save or not self.turns:
            return
        if not self.path:
            # Only you can read the chats: they can hold screenshots of private things.
            os.makedirs(SAVE_DIR, mode=0o700, exist_ok=True)
            os.chmod(SAVE_DIR, 0o700)  # also a folder made by an older version
            name = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            path, n = os.path.join(SAVE_DIR, name + ".json"), 1
            while os.path.exists(path):  # two chats started in the same second
                n += 1
                path = os.path.join(SAVE_DIR, f"{name}-{n}.json")
            self.path = path
        tmp = self.path + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            json.dump({"model": self.model, "kind": self.kind, "title": self.title, "turns": self.turns},
                      f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)  # a crash never leaves half a file

    def messages(self):
        """The request for the next answer: the rules, then as many of the newest turns as fit in
        HISTORY_CHARS. A chat from another tool (eli12, uktr) is about its first text and answer:
        those two are always kept, each cut to a third of HISTORY_CHARS if it is longer, so that a
        follow-up question still has the text that it is about."""
        today = datetime.date.today().strftime("%A %d %B %Y")
        turns, head, size = self.turns, [], 0
        if self.kind and len(turns) > 2:
            head = [cut(t, HISTORY_CHARS // 3) for t in turns[:2]]
            turns, size = turns[2:], sum(len(text_of(t["content"])) for t in head)
        keep = []
        for turn in reversed(turns):
            size += len(text_of(turn["content"])) + IMAGE_CHARS * len(images_of(turn["content"]))
            if keep and size > HISTORY_CHARS:
                break
            keep.insert(0, turn)
        if head and keep[0]["role"] == "assistant":
            keep.pop(0)  # after the kept first answer comes a question: user and assistant alternate
        keep = head + keep
        own = self.first and self.first.rules and self.first_answer()  # another tool's first answer
        rules = self.first.rules if own else RULES.format(system=system(), today=today) + self.follow_up
        return [{"role": "system", "content": rules}, *keep]

    def ask(self, question, cancel=None):
        """Yield the answer in pieces. The answer goes into the history when the stream ends,
        also when it is stopped or fails, so a follow-up question still has the context.
        cancel: an llm.Cancel, to stop the answer from another thread."""
        self.turns.append({"role": "user", "content": question})
        first = self.first if self.first and self.first_answer() else None
        if first and first.stream:
            pieces = first.stream(question, cancel)
        else:
            pieces = llm.chat_stream(self.messages(), self.model, cancel=cancel)
        answer, complete = "", False
        try:
            for piece in pieces:
                answer += piece
                yield piece
            complete = not (cancel and cancel.cancelled)
        finally:
            answer = self.shown(answer).strip()
            self.turns.append({"role": "assistant", "content": answer or "(no answer)"})
            self.write()
            if complete and first and first.done and answer:
                first.done(answer)


def _eli12():
    import eli12
    return eli12.MODEL, eli12.FOLLOW_UP


def _uktr():
    import uktr
    return MODEL, uktr.FOLLOW_UP  # translation models cannot chat: questions go to AIKEY_MODEL


KINDS = {"eli12": _eli12, "uktr": _uktr}  # a chat started by another tool: kind -> (model, follow_up)
ICONS = {"eli12": "💡 ", "uktr": "🌐 "}  # before the title of such a chat; a screenshot gets 📷


def valid_turn(turn):
    """A turn as Chat keeps it: a role and a content that text_of() and images_of() can read."""
    if not isinstance(turn, dict) or turn.get("role") not in ("user", "assistant"):
        return False
    content = turn.get("content")
    if isinstance(content, str):
        return True
    return isinstance(content, list) and all(
        isinstance(p, dict) and (p.get("type") != "text" or isinstance(p.get("text"), str))
        and (p.get("type") != "image_url" or isinstance((p.get("image_url") or {}).get("url"), str))
        for p in content)


def cut(turn, chars):
    """The turn with a text content cut to about chars characters (a turn with images as it is)."""
    content = turn["content"]
    if isinstance(content, str) and len(content) > chars:
        return {**turn, "content": f"{content[:chars]} … ({len(content) - chars} more characters)"}
    return turn


def text_of(content):
    """The text of a message. Content is a string, or a list of parts with a screenshot."""
    if isinstance(content, str):
        return content
    return "\n".join(p["text"] for p in content if p.get("type") == "text")


def images_of(content):
    """The images of a message, as data URLs."""
    if isinstance(content, str):
        return []
    return [p["image_url"]["url"] for p in content if p.get("type") == "image_url"]


def screenshot(region=False):
    """The active window, or a region you select, as a JPEG data URL (longest side
    IMAGE_SIZE). None if it failed or the selection was cancelled."""
    from PySide6.QtGui import QImage
    with tempfile.TemporaryDirectory() as d:
        png = os.path.join(d, "shot.png")
        mode = "--region" if region else "--activewindow"
        subprocess.run(["spectacle", mode, "--background", "--nonotify", "-o", png],
                       capture_output=True)
        image = QImage(png)
    return None if image.isNull() else data_url(image)


def data_url(image):
    """A QImage as the model gets it: a JPEG data URL, longest side at most IMAGE_SIZE."""
    from PySide6.QtCore import QBuffer, Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    if max(image.width(), image.height()) > IMAGE_SIZE:
        image = image.scaled(IMAGE_SIZE, IMAGE_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    if image.hasAlphaChannel():  # JPEG has no transparency: put the image on white
        flat = QImage(image.size(), QImage.Format_RGB32)
        flat.fill(QColor("white"))
        painter = QPainter(flat)
        painter.drawImage(0, 0, image)
        painter.end()
        image = flat
    buf = QBuffer()
    buf.open(QBuffer.WriteOnly)
    image.save(buf, "JPG", 85)
    return "data:image/jpeg;base64," + base64.b64encode(bytes(buf.data())).decode()


def _image_files(mime):
    """The local files in pasted or dropped data that are images. QImageReader.canRead looks
    at the start of a file only, so a big file that is no image is not read."""
    from PySide6.QtGui import QImageReader
    paths = [url.toLocalFile() for url in mime.urls()] if mime.hasUrls() else []
    return [p for p in paths if p and os.path.isfile(p) and QImageReader(p).canRead()]


def has_images(mime):
    """Does pasted or dropped data (a QMimeData) hold an image? Cheap: Qt asks this while
    you drag over the window."""
    return mime.hasImage() or bool(_image_files(mime))


def pasted_images(mime):
    """The images in pasted or dropped data, as QImages: an image itself (copied in Spectacle
    or a browser), or image files (copied or dragged from Dolphin). Empty when there are none;
    then it is pasted as text."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QImageReader
    if mime.hasImage():
        image = QImage(mime.imageData())
        if not image.isNull():
            return [image]
    images = []
    for path in _image_files(mime):
        reader = QImageReader(path)
        reader.setAutoTransform(True)  # a phone photo turned as the camera says (EXIF)
        size = reader.size()
        if size.isValid() and max(size.width(), size.height()) > IMAGE_SIZE:
            # read a big photo at the size it gets anyway, not first at full size
            reader.setScaledSize(size.scaled(IMAGE_SIZE, IMAGE_SIZE, Qt.KeepAspectRatio))
        image = reader.read()
        if not image.isNull():
            images.append(image)
    return images


def title_of(question):
    question = text_of(question)
    line = question.strip().splitlines()[0] if question.strip() else "(empty)"
    return line if len(line) <= 60 else line[:59] + "…"


_seen = {}  # path -> (mtime, title, messages): read each saved chat only when it changed


def saved_chats():
    """[(path, title, modified datetime, messages)], newest first. Broken files are skipped."""
    found = []
    try:
        names = [n for n in os.listdir(SAVE_DIR) if n.endswith(".json")]
    except FileNotFoundError:
        return []
    for name in names:
        path = os.path.join(SAVE_DIR, name)
        try:
            mtime = os.stat(path).st_mtime
            if _seen.get(path, (None,))[0] != mtime:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                _seen[path] = (mtime, str(data.get("title") or name), len(data.get("turns", [])))
            _, title, n = _seen[path]
            found.append((path, title, datetime.datetime.fromtimestamp(mtime), n))
        except (OSError, ValueError, TypeError, AttributeError):  # not a saved chat
            continue
    return sorted(found, key=lambda c: c[2], reverse=True)


def questions(messages):
    n = messages // 2
    return f"{n} question{'s' if n != 1 else ''}"


def when_text(when):
    today = datetime.date.today()
    if when.date() == today:
        return when.strftime("%H:%M")
    if (today - when.date()).days < 7:
        return when.strftime("%a %H:%M")
    return when.strftime("%d %b %Y")


def terminal(chat=None):
    import readline  # noqa: F401  (line editing and arrow-up history for input())
    chat = chat or Chat(save=True)
    picked = None  # the model picked with /model: new chats take it, not a resumed chat's model
    print(f"aikey {chat.model}: /new starts a new chat, {'/model NAME picks a model, ' if choosing() else ''}"
          "Ctrl+D or /quit quits.", file=sys.stderr)
    for t in chat.turns:  # a resumed chat: show it first
        # plain(): a saved question can be a copied text (uktr, eli12) with escape codes in it
        if t["role"] == "user":
            shots = " [screenshot]" * len(images_of(t["content"]))
            print(f"\n> {llm.plain(text_of(t['content']))}{shots}")
        else:
            print(llm.plain(t["content"]))
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not question:
            continue
        if question in ("/quit", "/exit"):
            return
        if question == "/new":
            chat = Chat(save=True, model=picked)
            print("(new chat)", file=sys.stderr)
            continue
        if question.split()[0] == "/model" and not choosing():
            print("(to choose the model per chat, list two or more in AIKEY_MODELS)", file=sys.stderr)
            continue
        if question.split()[0] == "/model":
            name = question[len("/model"):].strip()
            if name:
                chat.model = picked = name
            others = ", ".join(m for m in MODELS if m != chat.model)
            print(f"(model: {chat.model}{'; also: ' + others if others else ''})", file=sys.stderr)
            continue
        answer = chat.ask(question)
        try:
            for piece in answer:
                print(piece, end="", flush=True)
        except KeyboardInterrupt:
            print(" [stopped]", end="")
        except Exception as e:
            print(f"[aikey failed: {type(e).__name__}: {e}]", end="")
        finally:
            answer.close()
        print()


QUOTE_CHARS = 2000  # a longer question (a whole letter) is shown cut; the model gets all of it
VERBATIM = {"uktr"}  # chats whose first answer is a text to show as it is (a translation), not Markdown
_MARKUP = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~=])")


def verbatim(text, prefix=""):
    """Markdown that shows text exactly as it is: every line stays a line, no character is read
    as markup (#, *, -, 1. ...), and an indent stays an indent instead of becoming code. For what
    you sent, and for a translation: there, merged lines and lost characters change the text."""
    lines = text.split("\n")
    out = []
    for i, line in enumerate(lines):
        body = line.lstrip(" \t")
        indent = "\u00a0" * len(line[:len(line) - len(body)].expandtabs(4))
        if not body:
            indent = "\u00a0"  # an empty line stays an empty line (Qt shows no paragraph gap)
        hard_break = "\\" if i + 1 < len(lines) else ""  # every line ends a line
        out.append(prefix + indent + _MARKUP.sub(r"\\\1", body) + hard_break)
    return "\n".join(out)


def window(first=None, chat=None, title=None):
    """The chat window. first: a question to send at once. chat: a Chat to start with (from
    another tool). title: the window title."""
    try:
        from PySide6.QtCore import QByteArray, QObject, Qt, QTimer, Signal
        from PySide6.QtGui import QDesktopServices, QIcon, QImage, QKeySequence, QPixmap, QShortcut, QTextDocument
        from PySide6.QtWidgets import (
            QApplication,
            QComboBox,
            QHBoxLayout,
            QLabel,
            QListWidget,
            QListWidgetItem,
            QMenu,
            QMessageBox,
            QPlainTextEdit,
            QPushButton,
            QSplitter,
            QTextBrowser,
            QVBoxLayout,
            QWidget,
        )
    except ImportError:
        # No Qt: chat in a terminal window instead.
        os.execvp("konsole", ["konsole", "-e", os.path.realpath(__file__)])

    class Stream(QObject):
        """Carries pieces from the request thread to the window (Qt queues the signals)."""
        piece = Signal(str)
        done = Signal(str)  # "" when complete, "stopped", or an error text

    def confirm(parent, text):
        """Yes or No. Plain text: a chat title or a link must not show as rich text."""
        box = QMessageBox(QMessageBox.Question, "aikey", text, QMessageBox.Yes | QMessageBox.No, parent)
        box.setTextFormat(Qt.PlainText)
        return box.exec() == QMessageBox.Yes

    class View(QTextBrowser):
        """Shows screenshots in the chat as thumbnails: markdown ![](shot:N) -> self.images.

        An answer can come from text that someone else wrote (a web page in a screenshot), so
        a link in it can carry your chat to their server: a link opens only after you saw the
        whole address and said yes. Raw HTML is not rendered (it hides where a link goes), and
        no image is loaded except the screenshots."""

        def __init__(self):
            super().__init__(openLinks=False)
            self.anchorClicked.connect(self.open_link)
            self.images = {}

        def loadResource(self, kind, url):
            # Always an empty value, never None: for None, Qt loads the file itself.
            if kind == QTextDocument.ImageResource:
                return self.images.get(url.toString(), QImage())
            return QByteArray()

        def show_markdown(self, text):
            self.document().setMarkdown(text, QTextDocument.MarkdownDialectGitHub | QTextDocument.MarkdownNoHTML)

        def open_link(self, url):
            # The encoded form, as browsers show it: a host with Cyrillic letters that looks
            # like a known site shows as xn--…, not as the look-alike.
            shown = url.toEncoded().data().decode("ascii", "replace")
            if url.scheme() in ("http", "https") and confirm(self, f"Open this link?\n\n{shown}"):
                QDesktopServices.openUrl(url)

        def add_image(self, data_url):
            name = f"shot:{len(self.images)}"
            image = QImage.fromData(base64.b64decode(data_url.split(",", 1)[1]))
            self.images[name] = image.scaled(480, 480, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            return name

    class Input(QPlainTextEdit):
        """The question box. A pasted or dropped image goes to attach(), not into the text."""

        def __init__(self, send, attach):
            super().__init__()
            self.send, self.attach = send, attach

        def canInsertFromMimeData(self, source):
            return has_images(source) or super().canInsertFromMimeData(source)

        def insertFromMimeData(self, source):
            images = pasted_images(source)
            if images:
                self.attach(images)
            else:
                super().insertFromMimeData(source)

        def keyPressEvent(self, e):
            if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not e.modifiers() & Qt.ShiftModifier:
                self.send()
            else:
                super().keyPressEvent(e)

    class Window(QWidget):
        def __init__(self, first=None, chat=None, title=None):
            super().__init__()
            self.chat, self.busy, self.stop = chat or Chat(save=True), False, threading.Event()
            self.answer, self.error, self.render_due = "", "", False
            # picked: the model chosen in the menu, for new chats (a tool's chat has its own model)
            self.title, self.last_model, self.picked = title, None, None
            self.setWindowTitle(title or f"aikey — {self.chat.model}")
            self.resize(1000, 680)

            self.list = QListWidget()
            self.list.setWordWrap(True)
            self.list.setSpacing(3)
            self.list.itemClicked.connect(self.open_chat)
            self.list.setContextMenuPolicy(Qt.CustomContextMenu)
            self.list.customContextMenuRequested.connect(self.list_menu)
            self.list.setVisible(SAVE)

            self.view = View()
            self.input = Input(self.send, self.attach)
            self.input.setPlaceholderText("Ask something… (Enter sends, Shift+Enter is a new line; "
                                          "paste or drop an image to ask about it)")
            self.input.setFixedHeight(90)
            # Pasted images wait here, as data URLs, for the next question.
            self.pending = []
            self.thumbs = QHBoxLayout()
            self.thumbs.setContentsMargins(0, 0, 0, 0)
            self.attached = QWidget()
            bar = QHBoxLayout(self.attached)
            bar.setContentsMargins(0, 0, 0, 0)
            bar.addLayout(self.thumbs)
            self.attached_text = QLabel()
            bar.addWidget(self.attached_text, 1)
            bar.addWidget(QPushButton("Remove", clicked=lambda: self.attach(None)))
            self.attached.setVisible(False)
            self.status = QLabel()
            self.button = QPushButton("Send", clicked=lambda: self.send())
            new = QPushButton("New chat", clicked=self.new_chat)
            copy = QPushButton("Copy answer", clicked=self.copy_answer)
            saved = QPushButton("Saved chats", clicked=self.toggle_list, enabled=SAVE)

            # A menu of models, only when AIKEY_MODELS names two or more: the model of this chat.
            self.models = None
            if choosing():
                self.models = QComboBox()
                self.models.setToolTip("The model that answers in this chat (AIKEY_MODELS)")
                self.models.activated.connect(lambda _: self.pick_model(self.models.currentText()))

            row = QHBoxLayout()
            row.addWidget(saved)
            row.addWidget(self.status, 1)
            for b in (self.models, new, copy, self.button):
                if b:
                    row.addWidget(b)
            right = QWidget()
            layout = QVBoxLayout(right)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self.view, 1)
            layout.addWidget(self.attached)
            layout.addWidget(self.input)
            layout.addLayout(row)
            split = QSplitter()
            split.addWidget(self.list)
            split.addWidget(right)
            split.setStretchFactor(1, 1)
            split.setSizes([240, 760])
            QVBoxLayout(self).addWidget(split)

            QShortcut(QKeySequence("Ctrl+N"), self, self.new_chat)
            QShortcut(QKeySequence("Ctrl+H"), self, self.toggle_list)
            del_key = QShortcut(QKeySequence("Del"), self.list, self.delete_chat)
            del_key.setContext(Qt.WidgetShortcut)
            QShortcut(QKeySequence("Ctrl+Shift+C"), self, self.copy_answer)
            QShortcut(QKeySequence("Esc"), self, self.escape)

            self.show_model()
            self.stream = Stream()
            self.stream.piece.connect(self.on_piece)
            self.stream.done.connect(self.on_done)
            self.fill_list()
            self.render()
            self.input.setFocus()
            if first:
                self.send(first)

        def fill_list(self):
            self.list.clear()
            for path, title, when, n in saved_chats():
                item = QListWidgetItem(f"{title}\n{when_text(when)} · {questions(n)}")
                item.setData(Qt.UserRole, path)
                item.setToolTip(f"<qt>{html.escape(title)}</qt>")  # a tooltip renders tags
                self.list.addItem(item)
                if path == self.chat.path:
                    item.setSelected(True)
                    self.list.setCurrentItem(item)

        def toggle_list(self):
            if SAVE:
                self.list.setVisible(not self.list.isVisible())

        def show_model(self):
            """The menu and the title show the model of this chat (also one not in AIKEY_MODELS)."""
            if self.models:
                items = list(dict.fromkeys([*MODELS, self.chat.model]))  # no stale extra from before
                if [self.models.itemText(i) for i in range(self.models.count())] != items:
                    self.models.clear()
                    self.models.addItems(items)
                self.models.setCurrentText(self.chat.model)
            if not self.title:
                self.setWindowTitle(f"aikey — {self.chat.model}")

        def pick_model(self, model):
            if self.busy or model == self.chat.model:
                return self.show_model()
            self.chat.model = self.picked = model
            self.show_model()
            self.render()  # the empty-chat text names the model
            self.status.setText(f"next answer by {model}")
            self.input.setFocus()

        def open_chat(self, item):
            if self.busy:
                return
            path = item.data(Qt.UserRole)
            try:
                self.chat = Chat.load(path)
            except (OSError, ValueError) as e:
                self.status.setText(f"cannot open: {e}")
                return
            self.error = ""
            self.status.setText("")
            self.show_model()
            self.render(follow=True)
            self.input.setFocus()

        def list_menu(self, pos):
            item = self.list.itemAt(pos)
            if item:
                menu = QMenu(self)
                menu.addAction("Delete chat", lambda: self.delete_chat(item))
                menu.exec(self.list.mapToGlobal(pos))

        def delete_chat(self, item=None):
            item = item or self.list.currentItem()
            if not item or self.busy:
                return
            path = item.data(Qt.UserRole)
            if not confirm(self, f"Delete this chat?\n\n{item.toolTip()}"):
                return
            with contextlib.suppress(OSError):
                os.remove(path)
            if path == self.chat.path:
                self.chat = Chat(save=True, model=self.picked)
                self.show_model()
                self.render()
            self.fill_list()

        def render(self, follow=False):
            """Show the chat. Stay at the bottom while an answer streams in, unless you
            scrolled up to read; follow=True always jumps to the end."""
            self.render_due = False
            bar = self.view.verticalScrollBar()
            at_end, pos = bar.value() >= bar.maximum() - 8, bar.value()
            turns = list(self.chat.turns)
            live = self.busy and (not turns or turns[-1]["role"] == "user")
            if live:
                turns.append({"role": "assistant", "content": self.chat.shown(self.answer)})
            parts, answers = [], 0
            self.view.images.clear()
            for t in turns:
                if t["role"] == "user":
                    text = text_of(t["content"])
                    if len(text) > QUOTE_CHARS:
                        text = f"{text[:QUOTE_CHARS]}… ({len(text) - QUOTE_CHARS} more characters)"
                    shots = "".join(f"\n\n![screenshot]({self.view.add_image(url)})"
                                    for url in images_of(t["content"]))
                    parts.append(f"**You**\n\n{verbatim(text, '> ')}{shots}")
                else:
                    answers += 1
                    as_is = answers == 1 and self.chat.kind in VERBATIM  # a translation
                    parts.append(verbatim(t["content"]) if as_is else t["content"])
            if live:
                parts[-1] += " ▍"
            if self.error:
                parts.append(f"*{self.error}*")
            if not parts:
                parts.append(f"*Chat with {self.chat.model} on the local server. "
                             + ("Chats are saved; open an old one on the left.*" if SAVE else
                                "The conversation is kept until you press New chat.*"))
            self.view.show_markdown("\n\n---\n\n".join(parts))
            bar.setValue(bar.maximum() if follow or at_end else pos)

        def attach(self, images):
            """Add pasted images (QImages) to the next question; None removes them all."""
            if images is None:
                self.pending = []
            else:
                room = MAX_IMAGES - len(self.pending)
                self.pending += [data_url(image) for image in images[:room]]
                if len(images) > room:
                    self.status.setText(f"at most {MAX_IMAGES} images per question")
            while self.thumbs.count():
                self.thumbs.takeAt(0).widget().deleteLater()
            for url in self.pending:
                thumb = QLabel()
                thumb.setPixmap(QPixmap.fromImage(QImage.fromData(base64.b64decode(url.split(",", 1)[1])))
                                .scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.thumbs.addWidget(thumb)
            n = len(self.pending)
            self.attached_text.setText(f"{n} image{'s' if n > 1 else ''} with your next question")
            self.attached.setVisible(bool(n))
            self.input.setFocus()

        def send(self, question=None):
            """Send the input box with the pasted images, or a given question (text, or a list
            with a screenshot)."""
            if self.busy:
                return self.stop_answer()
            if not question:
                text = self.input.toPlainText().strip()
                if not text and not self.pending:
                    return
                question = text
                if self.pending:
                    # Only an image: ask what is on it, as for a screenshot.
                    text = text or SCREEN_PROMPT.replace("{language}", llm.language()[1])
                    question = [{"type": "text", "text": text},
                                *({"type": "image_url", "image_url": {"url": url}} for url in self.pending)]
                    self.attach(None)
                self.input.clear()
            self.busy, self.answer, self.error = True, "", ""
            self.stop.clear()
            self.cancel = llm.Cancel()
            self.button.setText("Stop")
            switching = self.last_model and self.chat.model != self.last_model
            # FastFlowLM loads one model at a time: a switch takes a few seconds more
            self.status.setText(f"loading {self.chat.model}…" if switching else "thinking…")
            first = self.chat.first
            own_stream = first and first.stream and self.chat.first_answer()
            # uktr's translation comes from its own model, so the next question switches models
            self.last_model = "(the tool's own model)" if own_stream else self.chat.model
            if self.models:
                self.models.setEnabled(False)
            self.worker = threading.Thread(target=self.work, args=(question,), daemon=True)
            self.worker.start()
            self.render(follow=True)

        def work(self, question):
            answer, result = self.chat.ask(question, self.cancel), ""
            try:
                for piece in answer:
                    if self.stop.is_set():
                        result = "stopped"
                        break
                    self.stream.piece.emit(piece)
                if self.stop.is_set():
                    result = "stopped"  # stopped before the first word came
            except Exception as e:  # anything, or the window stays busy forever
                result = f"aikey failed: {type(e).__name__}: {e}"
            finally:
                # Closes the connection (the server stops generating) and puts the answer in the
                # history. Only then report done, so the window shows the saved answer.
                answer.close()
            self.stream.done.emit(result)

        def on_piece(self, piece):
            self.answer += piece
            self.status.setText(f"writing… {len(self.answer.split())} words")
            if not self.render_due:  # redraw at most every 100 ms, not on every token
                self.render_due = True
                QTimer.singleShot(100, self.render)

        def on_done(self, result):
            self.busy = False
            if self.models:
                self.models.setEnabled(True)
            self.error = "" if result == "stopped" else result
            self.button.setText("Send")
            self.status.setText(result if result == "stopped" else "")
            self.fill_list()
            self.render()
            self.input.setFocus()

        def new_chat(self):
            if self.busy:
                return
            # a new chat takes the model picked in the menu; nothing picked (or no menu): AIKEY_MODEL
            self.chat, self.error = Chat(save=True, model=self.picked), ""
            self.show_model()
            self.list.clearSelection()
            self.status.setText("new chat")
            self.render()
            self.input.setFocus()

        def copy_answer(self):
            last = next((t["content"] for t in reversed(self.chat.turns)
                         if t["role"] == "assistant"), "")
            if last:
                llm.copy(last)  # wl-copy keeps it on the clipboard after this window closes
                self.status.setText("answer copied")

        def stop_answer(self):
            """Stop now: also while the model is still loading or before the first word."""
            self.stop.set()
            self.cancel.cancel()

        def closeEvent(self, event):
            """Closing while an answer comes in: stop it first, as Stop does, so that the server
            can end it (see llm._Stream); the program waits for that at exit, after the window
            is gone. Killing the connection with the window made FastFlowLM keep it counted as
            open."""
            if self.busy:
                self.stop_answer()
                self.worker.join(2)  # the answer stops at once; this only lets it save the chat
            super().closeEvent(event)

        def escape(self):
            if self.busy:
                self.stop_answer()
            else:
                self.close()

    app = QApplication.instance() or QApplication(sys.argv[:1])  # one already there: the tests
    app.setApplicationName("aikey")
    # Plasma takes the icon from the desktop file of the chat hotkey (aikey.desktop).
    app.setDesktopFileName("aikey")
    app.setWindowIcon(QIcon(os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                         "kde", "icons", "local-llm-hotkeys.svg")))
    w = Window(first, chat, title)
    w.show()
    w.raise_()
    w.activateWindow()
    sys.exit(app.exec())


def main(argv):
    if argv[:1] in (["-h"], ["--help"]):
        return print(__doc__)
    if argv == ["--popup"]:
        return window()
    if argv in (["--screen"], ["--region"]):
        # Take the screenshot before our window opens and becomes the active window.
        with llm.errors("aikey", gui=True):
            if argv == ["--region"]:
                shot = screenshot(region=True)
                if not shot:
                    return  # selection cancelled
            elif not (shot := screenshot()):
                raise llm.Error("screenshot failed (is spectacle installed?)")
        prompt = SCREEN_PROMPT.replace("{language}", llm.language()[1])
        return window([{"type": "text", "text": prompt},
                       {"type": "image_url", "image_url": {"url": shot}}])
    if argv == ["--list"]:
        for i, (_path, title, when, n) in enumerate(saved_chats(), 1):
            print(f"{i:3}  {when:%Y-%m-%d %H:%M}  {title}  ({questions(n)})")
        return
    if argv[:1] == ["--resume"]:
        chats = saved_chats()
        try:
            n = int(argv[1]) if len(argv) > 1 else 1
        except ValueError:
            sys.exit("usage: aikey --resume [N]  (N from aikey --list)")
        if not 1 <= n <= len(chats):
            sys.exit(f"no saved chat {n} (see aikey --list)")
        try:
            chat = Chat.load(chats[n - 1][0])
        except (OSError, ValueError) as e:
            sys.exit(f"aikey: {e}")
        return terminal(chat)
    if not argv and sys.stdin.isatty():
        return terminal()
    with llm.errors("aikey", gui=False):
        question = " ".join(argv) or sys.stdin.read()
        if not question.strip():
            raise llm.Error("no question")
        for piece in Chat().ask(question):
            print(piece, end="", flush=True)
        print()


if __name__ == "__main__":
    main(sys.argv[1:])
