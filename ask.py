#!/usr/bin/env python3
"""ask: chat with the local LLM. The conversation keeps its history until you start a new one.

  ask --popup           chat window (KDE hotkey Meta+Shift+Q; needs PySide6)
  ask                   chat in the terminal (empty line or Ctrl+D quits, /new starts a new chat)
  ask QUESTION...       one question on the command line -> stdout
  ask < question        stdin -> stdout

Window keys: Enter sends, Shift+Enter is a new line, Esc stops an answer or closes the
window, Ctrl+N starts a new chat, Ctrl+Shift+C copies the last answer.

Uses the same backend and config file as uktr (~/.config/uktr/config, UKTR_URL).
  ASK_MODEL   default qwen3.5:9b (any chat model)
"""
import datetime
import os
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import uktr  # noqa: E402  (shares config and the streaming chat request)

MODEL = uktr.conf.get("ASK_MODEL", "qwen3.5:9b")
HISTORY_CHARS = 24000  # older turns are dropped so the prompt stays inside the context window

RULES = ("You are a helpful assistant on the user's Linux (KDE Plasma) computer. Today is {today}. "
         "Answer in the language of the user's last message. Be short and concrete: a direct "
         "answer first, then only the details that matter. Markdown is fine. If you do not know "
         "or are not sure, say so.")


class Chat:
    """A conversation: the messages so far, and the request for the next answer."""

    def __init__(self):
        self.turns = []  # {"role": "user" | "assistant", "content": ...}

    def messages(self):
        today = datetime.date.today().strftime("%A %d %B %Y")
        keep, size = [], 0
        for turn in reversed(self.turns):
            size += len(turn["content"])
            if keep and size > HISTORY_CHARS:
                break
            keep.insert(0, turn)
        return [{"role": "system", "content": RULES.format(today=today)}] + keep

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


def terminal():
    import readline  # noqa: F401  (line editing and arrow-up history for input())
    chat = Chat()
    print(f"ask {MODEL}: empty line or Ctrl+D quits, /new starts a new chat.", file=sys.stderr)
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not question:
            return
        if question == "/new":
            chat = Chat()
            print("(new chat)", file=sys.stderr)
            continue
        answer = chat.ask(question)
        try:
            for piece in answer:
                print(piece, end="", flush=True)
        except KeyboardInterrupt:
            print(" [stopped]", end="")
        except OSError as e:
            print(f"[ask failed: {e}]", end="")
        finally:
            answer.close()
        print()


def window():
    try:
        from PySide6.QtCore import QObject, Qt, Signal
        from PySide6.QtGui import QKeySequence, QShortcut
        from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPlainTextEdit,
                                       QPushButton, QTextBrowser, QVBoxLayout, QWidget)
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
            self.chat, self.busy, self.stop = Chat(), False, threading.Event()
            self.answer, self.error = "", ""
            self.setWindowTitle(f"ask — {MODEL}")
            self.resize(760, 680)

            self.view = QTextBrowser(openExternalLinks=True)
            self.input = Input(self.send)
            self.input.setPlaceholderText("Ask something… (Enter sends, Shift+Enter is a new line)")
            self.input.setFixedHeight(90)
            self.status = QLabel()
            self.button = QPushButton("Send", clicked=self.send)
            new = QPushButton("New chat", clicked=self.new_chat)
            copy = QPushButton("Copy answer", clicked=self.copy_answer)

            row = QHBoxLayout()
            row.addWidget(self.status, 1)
            for b in (new, copy, self.button):
                row.addWidget(b)
            layout = QVBoxLayout(self)
            layout.addWidget(self.view, 1)
            layout.addWidget(self.input)
            layout.addLayout(row)

            QShortcut(QKeySequence("Ctrl+N"), self, self.new_chat)
            QShortcut(QKeySequence("Ctrl+Shift+C"), self, self.copy_answer)
            QShortcut(QKeySequence("Esc"), self, self.escape)

            self.stream = Stream()
            self.stream.piece.connect(self.on_piece)
            self.stream.done.connect(self.on_done)
            self.render()
            self.input.setFocus()

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
                             "The conversation is kept until you press New chat.*")
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
            except OSError as e:
                result = f"ask failed: {e}"
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
            self.render()
            self.input.setFocus()

        def new_chat(self):
            if self.busy:
                return
            self.chat, self.error = Chat(), ""
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
