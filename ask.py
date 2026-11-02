#!/usr/bin/env python3
"""ask: chat with the local LLM. The conversation keeps its history until you start a new one.

  ask --popup           chat window (KDE hotkey Meta+Shift+Q or Copilot; needs PySide6)
  ask --screen          screenshot of the active window -> chat window that explains it
                        (Ctrl+Alt+Copilot); ask follow-up questions about it
  ask                   chat in the terminal (/new starts a new chat, Ctrl+D or /quit quits)
  ask --list            saved chats, newest first
  ask --resume [N]      continue saved chat N in the terminal (default 1, the newest)
  ask QUESTION...       one question on the command line -> stdout
  ask < question        stdin -> stdout

Window keys: Enter sends, Shift+Enter is a new line, Esc stops an answer or closes the
window, Ctrl+N starts a new chat, Ctrl+H shows or hides the saved chats, Ctrl+Shift+C copies
the last answer. Click a saved chat to continue it; right-click or Del removes it.

Chats from the window and the terminal chat are saved after every answer, as JSON in
~/.local/share/uktr/ask/. One-shot questions (ask QUESTION, ask < question) are not saved.

Uses the same backend and config file as uktr (~/.config/uktr/config, UKTR_URL).
  ASK_MODEL     default qwen3.5:9b (any chat model)
  ASK_HISTORY   default 1; 0 = do not save chats
  ASK_SCREEN_PROMPT  the first question for --screen ({language} = your language)
"""
import base64
import contextlib
import datetime
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import llm

MODEL = llm.conf.get("ASK_MODEL", "qwen3.5:9b")
HISTORY_CHARS = 24000  # older turns are dropped so the prompt stays inside the context window
SAVE = llm.conf.get("ASK_HISTORY", "1") != "0"
SAVE_DIR = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                        "uktr", "ask")

SCREEN_PROMPT = llm.conf.get("ASK_SCREEN_PROMPT", (
    "What is on this screen? Say briefly what it shows and what matters. If there is an error, "
    "a question or a choice to make, explain it and say what to do. Answer in {language}."))
IMAGE_SIZE = 1280  # longest side in pixels; bigger is slower and does not read better
IMAGE_CHARS = 2000  # what an image counts for in HISTORY_CHARS

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


class Chat:
    """A conversation: the messages so far, and the request for the next answer.
    With save=True it is written to SAVE_DIR after every answer."""

    def __init__(self, save=False, path=None, turns=None):
        self.save, self.path = save and SAVE, path
        self.turns = turns or []  # {"role": "user" | "assistant", "content": ...}

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls(save=True, path=path, turns=json.load(f)["turns"])

    @property
    def title(self):
        first = next((t["content"] for t in self.turns if t["role"] == "user"), "")
        if images_of(first):
            # All screenshot chats start with the same question; the answer says what it was.
            answer = next((t["content"] for t in self.turns if t["role"] == "assistant"), "")
            return "📷 " + title_of(re.sub(r"[*#`>_]", "", answer) or "screenshot")
        return title_of(first)

    def write(self):
        if not self.save or not self.turns:
            return
        if not self.path:
            os.makedirs(SAVE_DIR, exist_ok=True)
            name = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            path, n = os.path.join(SAVE_DIR, name + ".json"), 1
            while os.path.exists(path):  # two chats started in the same second
                n += 1
                path = os.path.join(SAVE_DIR, f"{name}-{n}.json")
            self.path = path
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"model": MODEL, "title": self.title, "turns": self.turns}, f,
                      ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)  # a crash never leaves half a file

    def messages(self):
        today = datetime.date.today().strftime("%A %d %B %Y")
        keep, size = [], 0
        for turn in reversed(self.turns):
            size += len(text_of(turn["content"])) + IMAGE_CHARS * len(images_of(turn["content"]))
            if keep and size > HISTORY_CHARS:
                break
            keep.insert(0, turn)
        return [{"role": "system", "content": RULES.format(system=system(), today=today)}, *keep]

    def ask(self, question):
        """Yield the answer in pieces. The answer goes into the history when the stream ends,
        also when it is stopped or fails, so a follow-up question still has the context."""
        self.turns.append({"role": "user", "content": question})
        answer = ""
        try:
            for piece in llm.chat_stream(self.messages(), MODEL):
                answer += piece
                yield piece
        finally:
            self.turns.append({"role": "assistant", "content": answer.strip() or "(no answer)"})
            self.write()


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


def screenshot():
    """The active window as a JPEG data URL (longest side IMAGE_SIZE), or None if it failed."""
    from PySide6.QtCore import QBuffer, Qt
    from PySide6.QtGui import QImage
    with tempfile.TemporaryDirectory() as d:
        png = os.path.join(d, "window.png")
        subprocess.run(["spectacle", "--activewindow", "--background", "--nonotify", "-o", png],
                       capture_output=True)
        image = QImage(png)
    if image.isNull():
        return None
    if max(image.width(), image.height()) > IMAGE_SIZE:
        image = image.scaled(IMAGE_SIZE, IMAGE_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    buf = QBuffer()
    buf.open(QBuffer.WriteOnly)
    image.save(buf, "JPG", 85)
    return "data:image/jpeg;base64," + base64.b64encode(bytes(buf.data())).decode()


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
                _seen[path] = (mtime, data.get("title") or name, len(data.get("turns", [])))
            _, title, n = _seen[path]
            found.append((path, title, datetime.datetime.fromtimestamp(mtime), n))
        except (OSError, ValueError):
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
    print(f"ask {MODEL}: /new starts a new chat, Ctrl+D or /quit quits.", file=sys.stderr)
    for t in chat.turns:  # a resumed chat: show it first
        if t["role"] == "user":
            shots = " [screenshot]" * len(images_of(t["content"]))
            print(f"\n> {text_of(t['content'])}{shots}")
        else:
            print(t["content"])
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
            chat = Chat(save=True)
            print("(new chat)", file=sys.stderr)
            continue
        answer = chat.ask(question)
        try:
            for piece in answer:
                print(piece, end="", flush=True)
        except KeyboardInterrupt:
            print(" [stopped]", end="")
        except Exception as e:
            print(f"[ask failed: {type(e).__name__}: {e}]", end="")
        finally:
            answer.close()
        print()


def window(first=None):
    try:
        from PySide6.QtCore import QObject, Qt, QTimer, Signal
        from PySide6.QtGui import QImage, QKeySequence, QShortcut, QTextDocument
        from PySide6.QtWidgets import (
            QApplication,
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

    class View(QTextBrowser):
        """Shows screenshots in the chat as thumbnails: markdown ![](shot:N) -> self.images."""

        def __init__(self):
            super().__init__(openExternalLinks=True)
            self.images = {}

        def loadResource(self, kind, url):
            if kind == QTextDocument.ImageResource and url.toString() in self.images:
                return self.images[url.toString()]
            return super().loadResource(kind, url)

        def add_image(self, data_url):
            name = f"shot:{len(self.images)}"
            image = QImage.fromData(base64.b64decode(data_url.split(",", 1)[1]))
            self.images[name] = image.scaled(480, 480, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            return name

    class Input(QPlainTextEdit):
        def __init__(self, send):
            super().__init__()
            self.send = send

        def keyPressEvent(self, e):
            if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not e.modifiers() & Qt.ShiftModifier:
                self.send()
            else:
                super().keyPressEvent(e)

    class Window(QWidget):
        def __init__(self, first=None):
            super().__init__()
            self.chat, self.busy, self.stop = Chat(save=True), False, threading.Event()
            self.answer, self.error, self.render_due = "", "", False
            self.setWindowTitle(f"ask — {MODEL}")
            self.resize(1000, 680)

            self.list = QListWidget()
            self.list.setWordWrap(True)
            self.list.setSpacing(3)
            self.list.itemClicked.connect(self.open_chat)
            self.list.setContextMenuPolicy(Qt.CustomContextMenu)
            self.list.customContextMenuRequested.connect(self.list_menu)
            self.list.setVisible(SAVE)

            self.view = View()
            self.input = Input(self.send)
            self.input.setPlaceholderText("Ask something… (Enter sends, Shift+Enter is a new line)")
            self.input.setFixedHeight(90)
            self.status = QLabel()
            self.button = QPushButton("Send", clicked=lambda: self.send())
            new = QPushButton("New chat", clicked=self.new_chat)
            copy = QPushButton("Copy answer", clicked=self.copy_answer)
            saved = QPushButton("Saved chats", clicked=self.toggle_list, enabled=SAVE)

            row = QHBoxLayout()
            row.addWidget(saved)
            row.addWidget(self.status, 1)
            for b in (new, copy, self.button):
                row.addWidget(b)
            right = QWidget()
            layout = QVBoxLayout(right)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self.view, 1)
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
                item.setToolTip(title)
                self.list.addItem(item)
                if path == self.chat.path:
                    item.setSelected(True)
                    self.list.setCurrentItem(item)

        def toggle_list(self):
            if SAVE:
                self.list.setVisible(not self.list.isVisible())

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
            if QMessageBox.question(self, "ask", f"Delete this chat?\n\n{item.toolTip()}") \
                    != QMessageBox.Yes:
                return
            with contextlib.suppress(OSError):
                os.remove(path)
            if path == self.chat.path:
                self.chat = Chat(save=True)
                self.render()
            self.fill_list()

        def render(self, follow=False):
            """Show the chat. Stay at the bottom while an answer streams in, unless you
            scrolled up to read; follow=True always jumps to the end."""
            self.render_due = False
            bar = self.view.verticalScrollBar()
            at_end, pos = bar.value() >= bar.maximum() - 8, bar.value()
            turns = list(self.chat.turns)
            if self.busy and (not turns or turns[-1]["role"] == "user"):
                turns.append({"role": "assistant", "content": self.answer + " ▍"})
            parts = []
            self.view.images.clear()
            for t in turns:
                if t["role"] == "user":
                    quoted = "\n".join("> " + line for line in text_of(t["content"]).splitlines())
                    shots = "".join(f"\n\n![screenshot]({self.view.add_image(url)})"
                                    for url in images_of(t["content"]))
                    parts.append(f"**You**\n\n{quoted}{shots}")
                else:
                    parts.append(t["content"])
            if self.error:
                parts.append(f"*{self.error}*")
            if not parts:
                parts.append(f"*Chat with {MODEL} on the local server. "
                             + ("Chats are saved; open an old one on the left.*" if SAVE else
                                "The conversation is kept until you press New chat.*"))
            self.view.setMarkdown("\n\n---\n\n".join(parts))
            bar.setValue(bar.maximum() if follow or at_end else pos)

        def send(self, question=None):
            """Send the input box, or a given question (text, or a list with a screenshot)."""
            if self.busy:
                return self.stop.set()
            if not question:
                question = self.input.toPlainText().strip()
                if not question:
                    return
                self.input.clear()
            self.busy, self.answer, self.error = True, "", ""
            self.stop.clear()
            self.button.setText("Stop")
            self.status.setText("thinking…")
            threading.Thread(target=self.work, args=(question,), daemon=True).start()
            self.render(follow=True)

        def work(self, question):
            answer, result = self.chat.ask(question), ""
            try:
                for piece in answer:
                    if self.stop.is_set():
                        result = "stopped"
                        break
                    self.stream.piece.emit(piece)
            except Exception as e:  # anything, or the window stays busy forever
                result = f"ask failed: {type(e).__name__}: {e}"
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
            self.error = "" if result == "stopped" else result
            self.button.setText("Send")
            self.status.setText(result if result == "stopped" else "")
            self.fill_list()
            self.render()
            self.input.setFocus()

        def new_chat(self):
            if self.busy:
                return
            self.chat, self.error = Chat(save=True), ""
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

        def escape(self):
            if self.busy:
                self.stop.set()
            else:
                self.close()

    app = QApplication(sys.argv[:1])
    app.setApplicationName("ask")
    app.setDesktopFileName("ask")
    w = Window(first)
    w.show()
    w.raise_()
    w.activateWindow()
    sys.exit(app.exec())


def main(argv):
    if argv[:1] in (["-h"], ["--help"]):
        return print(__doc__)
    if argv == ["--popup"]:
        return window()
    if argv == ["--screen"]:
        # Take the screenshot before our window opens and becomes the active window.
        with llm.errors("ask", gui=True):
            shot = screenshot()
            if not shot:
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
        if len(argv) > 1 and not argv[1].isdigit():
            sys.exit("usage: ask --resume [N]  (N from ask --list)")
        n = int(argv[1]) if len(argv) > 1 else 1
        if not 1 <= n <= len(chats):
            sys.exit(f"no saved chat {n} (see ask --list)")
        return terminal(Chat.load(chats[n - 1][0]))
    if not argv and sys.stdin.isatty():
        return terminal()
    with llm.errors("ask", gui=False):
        question = " ".join(argv) or sys.stdin.read()
        if not question.strip():
            raise llm.Error("no question")
        for piece in Chat().ask(question):
            print(piece, end="", flush=True)
        print()


if __name__ == "__main__":
    main(sys.argv[1:])
