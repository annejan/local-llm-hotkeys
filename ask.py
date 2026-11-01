#!/usr/bin/env python3
"""ask: chat with the local LLM. The conversation keeps its history until you start a new one.

  ask --popup           chat window (KDE hotkey Meta+Shift+Q; needs PySide6)
  ask                   chat in the terminal (empty line or Ctrl+D quits, /new starts a new chat)
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
"""
import datetime
import functools
import json
import os
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import uktr  # noqa: E402  (shares config and the streaming chat request)

MODEL = uktr.conf.get("ASK_MODEL", "qwen3.5:9b")
HISTORY_CHARS = 24000  # older turns are dropped so the prompt stays inside the context window
SAVE = uktr.conf.get("ASK_HISTORY", "1") != "0"
SAVE_DIR = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                        "uktr", "ask")

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
        return title_of(first)

    def write(self):
        if not self.save or not self.turns:
            return
        if not self.path:
            os.makedirs(SAVE_DIR, exist_ok=True)
            name = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            self.path = os.path.join(SAVE_DIR, name + ".json")
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"model": MODEL, "title": self.title, "turns": self.turns}, f,
                      ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)  # a crash never leaves half a file

    def messages(self):
        today = datetime.date.today().strftime("%A %d %B %Y")
        keep, size = [], 0
        for turn in reversed(self.turns):
            size += len(turn["content"])
            if keep and size > HISTORY_CHARS:
                break
            keep.insert(0, turn)
        return [{"role": "system", "content": RULES.format(system=system(), today=today)}] + keep

    def ask(self, question):
        """Yield the answer in pieces. The answer goes into the history when the stream ends,
        also when it is stopped or fails, so a follow-up question still has the context."""
        self.turns.append({"role": "user", "content": question})
        answer = ""
        try:
            for piece in uktr.chat_stream(self.messages(), MODEL):
                answer += piece
                yield piece
        finally:
            self.turns.append({"role": "assistant", "content": answer.strip() or "(no answer)"})
            self.write()


def title_of(question):
    line = question.strip().splitlines()[0] if question.strip() else "(empty)"
    return line if len(line) <= 60 else line[:59] + "…"


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
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            when = datetime.datetime.fromtimestamp(os.path.getmtime(path))
            found.append((path, data.get("title") or name, when, len(data.get("turns", []))))
        except (OSError, ValueError):
            continue
    return sorted(found, key=lambda c: c[2], reverse=True)


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
    print(f"ask {MODEL}: empty line or Ctrl+D quits, /new starts a new chat.", file=sys.stderr)
    for t in chat.turns:  # a resumed chat: show it first
        print(f"\n> {t['content']}" if t["role"] == "user" else t["content"])
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not question:
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


def window():
    try:
        from PySide6.QtCore import QObject, Qt, Signal
        from PySide6.QtGui import QKeySequence, QShortcut
        from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget,
                                       QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
                                       QPushButton, QSplitter, QTextBrowser, QVBoxLayout,
                                       QWidget)
    except ImportError:
        # No Qt: chat in a terminal window instead.
        os.execvp("konsole", ["konsole", "-e", os.path.realpath(__file__)])

    class Stream(QObject):
        """Carries pieces from the request thread to the window (Qt queues the signals)."""
        piece = Signal(str)
        done = Signal(str)  # "" when complete, "stopped", or an error text

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
        def __init__(self):
            super().__init__()
            self.chat, self.busy, self.stop = Chat(save=True), False, threading.Event()
            self.answer, self.error = "", ""
            self.setWindowTitle(f"ask — {MODEL}")
            self.resize(1000, 680)

            self.list = QListWidget()
            self.list.setWordWrap(True)
            self.list.setSpacing(3)
            self.list.itemClicked.connect(self.open_chat)
            self.list.setContextMenuPolicy(Qt.CustomContextMenu)
            self.list.customContextMenuRequested.connect(self.list_menu)
            self.list.setVisible(SAVE)

            self.view = QTextBrowser(openExternalLinks=True)
            self.input = Input(self.send)
            self.input.setPlaceholderText("Ask something… (Enter sends, Shift+Enter is a new line)")
            self.input.setFixedHeight(90)
            self.status = QLabel()
            self.button = QPushButton("Send", clicked=self.send)
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

        def fill_list(self):
            self.list.clear()
            for path, title, when, n in saved_chats():
                item = QListWidgetItem(f"{title}\n{when_text(when)} · {n // 2} question{'s' if n // 2 != 1 else ''}")
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
            self.render()
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
            try:
                os.remove(path)
            except OSError:
                pass
            if path == self.chat.path:
                self.chat = Chat(save=True)
                self.render()
            self.fill_list()

        def render(self):
            turns = list(self.chat.turns)
            if self.busy and (not turns or turns[-1]["role"] == "user"):
                turns.append({"role": "assistant", "content": self.answer + " ▍"})
            parts = []
            for t in turns:
                if t["role"] == "user":
                    quoted = "\n".join("> " + line for line in t["content"].splitlines())
                    parts.append(f"**You**\n\n{quoted}")
                else:
                    parts.append(t["content"])
            if self.error:
                parts.append(f"*{self.error}*")
            if not parts:
                parts.append(f"*Chat with {MODEL} on the local server. "
                             + ("Chats are saved; open an old one on the left.*" if SAVE else
                                "The conversation is kept until you press New chat.*"))
            self.view.setMarkdown("\n\n---\n\n".join(parts))
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())

        def send(self):
            if self.busy:
                return self.stop.set()
            question = self.input.toPlainText().strip()
            if not question:
                return
            self.input.clear()
            self.busy, self.answer, self.error = True, "", ""
            self.stop.clear()
            self.button.setText("Stop")
            self.status.setText("thinking…")
            threading.Thread(target=self.work, args=(question,), daemon=True).start()
            self.render()

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
            self.render()

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
                # wl-copy keeps the text on the clipboard after this window closes.
                subprocess.run(["wl-copy"], input=last, text=True)
                self.status.setText("answer copied")

        def escape(self):
            if self.busy:
                self.stop.set()
            else:
                self.close()

    app = QApplication(sys.argv[:1])
    app.setApplicationName("ask")
    app.setDesktopFileName("ask")
    w = Window()
    w.show()
    w.raise_()
    w.activateWindow()
    sys.exit(app.exec())


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    if argv == ["--popup"]:
        return window()
    if argv == ["--list"]:
        for i, (path, title, when, n) in enumerate(saved_chats(), 1):
            print(f"{i:3}  {when:%Y-%m-%d %H:%M}  {title}  ({n // 2} question{'s' if n // 2 != 1 else ''})")
        return
    if argv[:1] == ["--resume"]:
        chats = saved_chats()
        n = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 1
        if not 1 <= n <= len(chats):
            sys.exit(f"no saved chat {n} (see ask --list)")
        return terminal(Chat.load(chats[n - 1][0]))
    if not argv and sys.stdin.isatty():
        return terminal()
    question = " ".join(argv) or sys.stdin.read()
    if not question.strip():
        sys.exit("no question")
    for piece in Chat().ask(question):
        print(piece, end="", flush=True)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
