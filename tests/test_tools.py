"""Tests for the logic that needs no LLM server. Run: python3 -m unittest discover -s tests"""
import builtins
import importlib.util
import io
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from typing import ClassVar

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ask
import dictate
import eli12
import fixtext
import llm
import uktr


class Config(unittest.TestCase):
    def test_file_comments_and_env(self):
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
            f.write("# a comment\nUKTR_MODEL=a:1b\nELI12_LANG=nl   # nl, en or both\n"
                    "#ASK_MODEL=off\nnot a setting\n")
        try:
            conf = llm.read_config(f.name, {"ASK_MODEL": "b:2b", "HOME": "/x", "UKTR_TO": "Dutch"})
        finally:
            os.unlink(f.name)
        self.assertEqual(conf, {"UKTR_MODEL": "a:1b", "ELI12_LANG": "nl",
                                "ASK_MODEL": "b:2b", "UKTR_TO": "Dutch"})

    def test_missing_file(self):
        self.assertEqual(llm.read_config("/nonexistent/config", {}), {})


class Language(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(llm.parse_language("nl"), ("nl", "Dutch"))
        self.assertEqual(llm.parse_language("nl_NL.UTF-8"), ("nl", "Dutch"))
        self.assertEqual(llm.parse_language("dutch"), ("nl", "Dutch"))
        self.assertEqual(llm.parse_language("pt-BR"), ("pt", "Portuguese"))
        self.assertEqual(llm.parse_language("Klingon"), ("klingon", "Klingon"))

    def test_from_desktop(self):
        real = llm.conf.pop("UKTR_LANGUAGE", None)
        try:
            # The interface language counts, not the formats (LC_TIME and so on).
            self.assertEqual(llm.language({"LANG": "en_US.UTF-8", "LC_TIME": "nl_NL.UTF-8"}),
                             ("en", "English"))
            self.assertEqual(llm.language({"LANGUAGE": "de:en", "LANG": "en_US.UTF-8"}),
                             ("de", "German"))
            self.assertEqual(llm.language({"LANG": "C.UTF-8"}), ("en", "English"))
            llm.conf["UKTR_LANGUAGE"] = "Dutch"
            self.assertEqual(llm.language({"LANG": "en_US.UTF-8"}), ("nl", "Dutch"))
        finally:
            llm.conf.pop("UKTR_LANGUAGE", None)
            if real is not None:
                llm.conf["UKTR_LANGUAGE"] = real


class Chunks(unittest.TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(list(llm.chunks("a\n\nb")), ["a\n\nb"])

    def test_split_at_paragraphs(self):
        text = "\n\n".join(["x" * 600] * 5)
        parts = list(llm.chunks(text, size=1500))
        self.assertEqual(len(parts), 3)
        self.assertEqual("\n\n".join(parts), text)

    def test_no_empty_chunk(self):
        self.assertEqual(list(llm.chunks("  \n\n ")), [])


class Stream(unittest.TestCase):
    def stream(self, lines):
        def fake(body, timeout, cancel):
            yield from io.BytesIO("".join(f"{line}\n" for line in lines).encode())
        real, llm._stream_lines = llm._stream_lines, fake
        try:
            return "".join(llm.chat_stream([], "m"))
        finally:
            llm._stream_lines = real

    def test_pieces(self):
        self.assertEqual(self.stream([
            'data: {"choices":[{"delta":{"role":"assistant","content":"Hel"}}]}', "",
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            'data: {"choices":[{"delta":{}}]}', "data: [DONE]",
            'data: {"choices":[{"delta":{"content":"after done"}}]}']), "Hello")

    def test_control_characters_removed(self):
        # OSC 52 (write the clipboard) and a carriage return; newlines and tabs stay.
        self.assertEqual(self.stream([
            'data: {"choices":[{"delta":{"content":"a\\u001b]52;c;eA==\\u0007b"}}]}',
            'data: {"choices":[{"delta":{"content":"\\u001b"}}]}',
            'data: {"choices":[{"delta":{"content":"\\r\\n\\tc\\u009b"}}]}']), "a]52;c;eA==b\n\tc")

    def test_error_chunk(self):
        with self.assertRaises(llm.Error):
            self.stream(['data: {"error":{"message":"model not found"}}'])


class Desktop(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(llm.plain("x\x1b[31my\x7f\r\nz\tö"), "x[31my\nz\tö")
        # A bidi override turns "pay 10 EUR" into "pay 01 EUR" on screen; zero-width characters hide.
        self.assertEqual(llm.plain("pay \u202e01\u202c EUR\u200b"), "pay 01 EUR")

    def test_textbox_lines(self):
        lines = llm.textbox_lines("a <b>x</b> & y\n\n| a   | b |")
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], '<qt><span style="white-space:pre-wrap">a &lt;b&gt;x&lt;/b&gt; &amp; y</span></qt>')
        self.assertIn("| a   | b |", lines[2])

    def test_notify_escapes_markup(self):
        calls = []
        real, llm.subprocess.run = llm.subprocess.run, lambda cmd, **kw: calls.append(cmd)
        try:
            llm.notify('<a href="https://x">a & b</a>', "uktr")
        finally:
            llm.subprocess.run = real
        self.assertEqual(calls[0][-1], "&lt;a href=&quot;https://x&quot;&gt;a &amp; b&lt;/a&gt;")


class Output(unittest.TestCase):
    def test_writes_and_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "doc.nl.md")
            for text in ("a long first translation", "second"):
                with uktr.open_output(path) as f:
                    f.write(text)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "second")

    def test_refuses_links(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "bashrc")
            with open(target, "w") as f:
                f.write("keep")
            os.symlink(target, os.path.join(d, "sym.nl.md"))
            os.link(target, os.path.join(d, "hard.nl.md"))
            for name in ("sym.nl.md", "hard.nl.md"):
                with self.assertRaises(llm.Error):
                    uktr.open_output(os.path.join(d, name))
            with open(target) as f:
                self.assertEqual(f.read(), "keep")


class Run(unittest.TestCase):
    def test_timeout(self):
        with self.assertRaises(llm.Error) as e:
            llm.run([sys.executable, "-c", "import time; time.sleep(5)"], "x", timeout=0.3)
        self.assertIn("stopped", str(e.exception))

    def test_utf8_with_replacement(self):
        out = llm.run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'a\\xffb')"], "x")
        self.assertEqual(out, "a\ufffdb")

    def test_not_installed(self):
        with self.assertRaises(llm.Error) as e:
            llm.run(["no-such-helper-xyz"], "PDF files")
        self.assertIn("not installed", str(e.exception))


class Audio(unittest.TestCase):
    def test_rms_odd_length_block(self):
        self.assertEqual(llm.rms(b"\x00\x00\x00"), 0.0)
        self.assertEqual(llm.rms(b""), 0.0)
        loud = b"\x00\x10" + bytes(6) + b"\x00\xf0" + bytes(6)  # samples 0 and 4 differ (every 4th counts)
        self.assertGreater(llm.rms(loud + b"\x01"), 0)


class TranslatePrompt(unittest.TestCase):
    """The instruction goes in the user message with the language code (translategemma ignored
    a system prompt for some texts and translated into Portuguese or Russian), at temperature 0."""

    def test_messages(self):
        request = uktr.messages("Привіт", "translategemma:4b", "Dutch")
        self.assertEqual([m["role"] for m in request], ["user"])
        self.assertTrue(request[0]["content"].startswith("You are a professional translator to Dutch (nl)."))
        self.assertIn("Please translate the following text into Dutch:", request[0]["content"])
        self.assertTrue(request[0]["content"].endswith("\n\n\nПривіт"))
        self.assertIn("translator to German (de)", uktr.messages("x", "qwen3.5:9b", "de")[0]["content"])

    def test_hy_mt_keeps_its_prompt(self):
        request = uktr.messages("Привіт", "hy-mt2-flash:1.8b", "nl")
        self.assertEqual(request, [{"role": "user", "content": "Translate the following segment into Dutch, "
                                    "without additional explanation.\n\nПривіт"}])

    def test_temperature_zero(self):
        sent, real = [], llm.chat
        llm.chat = lambda messages, model, temperature=0.2, timeout=600: sent.append(temperature) or "ok"
        try:
            uktr.translate("x", "translategemma:4b", "Dutch")
        finally:
            llm.chat = real
        self.assertEqual(sent, [0])


class FollowUpContext(unittest.TestCase):
    """A follow-up question about a long text that another tool started the chat with."""

    def test_first_text_and_answer_are_kept(self):
        n = ask.HISTORY_CHARS
        turns = [{"role": "user", "content": "S" * n}, {"role": "assistant", "content": "T" * n},
                 {"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"},
                 {"role": "user", "content": "what does that word mean?"}]
        msgs = ask.Chat(kind="uktr", turns=turns).messages()
        self.assertEqual([m["role"] for m in msgs], ["system", "user", "assistant", "user", "assistant", "user"])
        self.assertTrue(msgs[1]["content"].startswith("S" * 100))  # the source, cut
        self.assertTrue(msgs[2]["content"].startswith("T" * 100))  # its translation, cut
        self.assertEqual(msgs[-1]["content"], "what does that word mean?")
        self.assertLessEqual(sum(len(m["content"]) for m in msgs[1:]), n + 200)

    def test_alternates_when_the_middle_is_dropped(self):
        n = ask.HISTORY_CHARS
        turns = [{"role": "user", "content": "S" * 10}, {"role": "assistant", "content": "T" * 10},
                 {"role": "user", "content": "q1"}, {"role": "assistant", "content": "A" * n},
                 {"role": "user", "content": "q2"}]
        roles = [m["role"] for m in ask.Chat(kind="eli12", turns=turns).messages()]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])  # q2 right after the first answer

    def test_a_normal_chat_keeps_the_newest(self):
        old = {"role": "user", "content": "x" * ask.HISTORY_CHARS}
        turns = [old, {"role": "assistant", "content": "a"}, {"role": "user", "content": "newest"}]
        msgs = ask.Chat(turns=turns).messages()
        self.assertNotIn(old, msgs)


class Verbatim(unittest.TestCase):
    """A translation and what you sent are shown as they are, not as Markdown."""
    TEXT = ("# not a heading\nline two of the same paragraph\n\n* not a bullet\n1. not a list\n"
            "    indented, not code\n> not a quote\n| a | b |\n---\n*not italic* and **not bold** \\o/ [x](y)\nend")

    def test_escapes(self):
        self.assertEqual(ask.verbatim("# a\nb"), "\\# a\\\nb")
        self.assertEqual(ask.verbatim("a\n\nb", "> "), "> a\\\n> \u00a0\\\n> b")

    @unittest.skipUnless(ask.can_window(), "needs PySide6")
    def test_qt_shows_the_text_as_it_is(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtGui import QTextDocument
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841
        for prefix in ("", "> "):
            doc = QTextDocument()
            doc.setMarkdown(ask.verbatim(self.TEXT, prefix),
                            QTextDocument.MarkdownDialectGitHub | QTextDocument.MarkdownNoHTML)
            # Qt makes every line a block of its own; an empty line is a block with a no-break space,
            # and toPlainText() writes no-break spaces as spaces.
            shown = [b if b.strip() else "" for b in doc.toPlainText().split("\n")]
            self.assertEqual(shown, self.TEXT.split("\n"), f"prefix {prefix!r}")


class AskModels(unittest.TestCase):
    """ASK_MODELS: the model is chosen per chat, and kept with it."""

    def setUp(self):
        self.real = ask.MODELS, ask.SAVE_DIR, ask.SAVE
        self.dir = tempfile.TemporaryDirectory()
        ask.MODELS, ask.SAVE_DIR, ask.SAVE = ["a:1b", "b:2b"], self.dir.name, True

    def tearDown(self):
        ask.MODELS, ask.SAVE_DIR, ask.SAVE = self.real
        self.dir.cleanup()

    def saved(self, model, kind=""):
        chat = ask.Chat(save=True, model=model, kind=kind,
                        turns=[{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}])
        chat.write()
        return ask.Chat.load(chat.path)

    def test_a_picked_model_is_kept(self):
        self.assertEqual(self.saved("b:2b").model, "b:2b")

    def test_a_model_no_longer_offered_is_not(self):
        self.assertEqual(self.saved("gone:7b").model, ask.MODEL)

    def test_a_tool_chat_keeps_a_picked_model_too(self):
        self.assertEqual(self.saved("b:2b", kind="eli12").model, "b:2b")
        self.assertEqual(self.saved("gone:7b", kind="eli12").model, eli12.MODEL)

    def test_terminal_model_command(self):
        lines = iter(["/model", "/model b:2b", "/new"])
        out, real_input, real_err = io.StringIO(), builtins.input, sys.stderr
        chats = []
        real_chat = ask.Chat

        def read(prompt=""):
            try:
                return next(lines)
            except StopIteration:
                raise EOFError from None
        def chat(*a, **k):
            chats.append(real_chat(*a, **k))
            return chats[-1]
        builtins.input, sys.stderr, ask.Chat = read, out, chat
        try:
            ask.terminal(real_chat(model="a:1b"))
        finally:
            builtins.input, sys.stderr, ask.Chat = real_input, real_err, real_chat
        self.assertIn("(model: a:1b; also: b:2b)", out.getvalue())
        self.assertIn("(model: b:2b; also: a:1b)", out.getvalue())
        self.assertEqual(chats[-1].model, "b:2b")  # /new keeps the picked model


class Resume(unittest.TestCase):
    def test_saved_text_is_printed_plain(self):
        """A resumed chat holds copied text (uktr, eli12): no escape codes to the terminal."""
        chat = ask.Chat(turns=[{"role": "user", "content": "a\x1b]52;c;eA==\x07b \u202eevil"},
                               {"role": "assistant", "content": "c\x1b[2Jd"}])
        out, real_input, real_stdout = io.StringIO(), builtins.input, sys.stdout

        def eof(prompt=""):
            raise EOFError  # Ctrl+D at once: only the saved turns are printed
        builtins.input, sys.stdout = eof, out
        try:
            ask.terminal(chat)
        finally:
            builtins.input, sys.stdout = real_input, real_stdout
        self.assertNotIn("\x1b", out.getvalue())
        self.assertNotIn("\u202e", out.getvalue())
        self.assertIn("a]52;c;eA==b", out.getvalue())


class CanWindow(unittest.TestCase):
    def test_needs_qtwidgets(self):
        """Debian splits PySide6: the package can be there without QtWidgets."""
        real = importlib.util.find_spec
        importlib.util.find_spec = lambda name, *a: None if name == "PySide6.QtWidgets" else real(name, *a)
        try:
            self.assertFalse(ask.can_window())
        finally:
            importlib.util.find_spec = real

    def test_no_pyside6(self):
        real = importlib.util.find_spec
        def missing(name, *a):
            raise ModuleNotFoundError(name)
        importlib.util.find_spec = missing
        try:
            self.assertFalse(ask.can_window())
        finally:
            importlib.util.find_spec = real


class UktrWindow(unittest.TestCase):
    """uktr in the ask window: the translation streams part by part; questions go to ASK_MODEL."""

    def fake(self, sent):
        def stream(messages, model, temperature=0.5, timeout=600, cancel=None):
            sent.append((messages, model, temperature))
            if cancel and cancel.cancelled:
                return
            yield "T(" + messages[-1]["content"][-3:] + ")"
        return stream

    def test_stream_in_parts_and_cancel(self):
        sent, real = [], llm.chat_stream
        llm.chat_stream = self.fake(sent)
        text = "\n\n".join(["a" * 1000 + "AAA", "b" * 1000 + "BBB", "c" * 1000 + "CCC"])
        try:
            self.assertEqual("".join(uktr.translate_stream(text, model="m")), "T(AAA)\n\nT(BBB)\n\nT(CCC)")
            self.assertEqual({(model, t) for _, model, t in sent}, {("m", 0)})
            cancel, out = llm.Cancel(), []
            for piece in uktr.translate_stream(text, cancel, model="m"):
                out.append(piece)
                cancel.cancelled = True  # Stop after the first part
            self.assertEqual(out, ["T(AAA)"])
        finally:
            llm.chat_stream = real

    def chat(self, done):
        first = ask.First(stream=lambda q, cancel: uktr.translate_stream(q, cancel, model="mt"), done=done.append)
        return ask.Chat(kind="uktr", first=first, follow_up=uktr.FOLLOW_UP)

    def test_copied_when_complete_and_follow_up_to_ask_model(self):
        sent, done, real = [], [], llm.chat_stream
        llm.chat_stream = self.fake(sent)
        try:
            chat = self.chat(done)
            self.assertEqual("".join(chat.ask("Привіт XYZ")), "T(XYZ)")
            self.assertEqual(done, ["T(XYZ)"])
            list(chat.ask("Wat betekent dat woord?"))
            messages, model, _ = sent[-1]
            self.assertEqual(model, ask.MODEL)  # a translation model cannot answer questions
            self.assertTrue(messages[0]["content"].endswith(uktr.FOLLOW_UP))
            self.assertEqual(chat.title, "🌐 T(XYZ)")
        finally:
            llm.chat_stream = real

    def test_not_copied_when_stopped(self):
        sent, done, real = [], [], llm.chat_stream
        llm.chat_stream = self.fake(sent)
        try:
            chat, cancel = self.chat(done), llm.Cancel()
            answer = chat.ask("Привіт XYZ", cancel)
            next(answer)
            cancel.cancel()
            answer.close()  # the reader leaves: Ctrl+C, an error, a break
            self.assertEqual(done, [])
            self.assertEqual(chat.turns[-1]["content"], "T(XYZ)")  # kept in the history
        finally:
            llm.chat_stream = real

    def test_not_copied_when_the_stream_ends_after_stop(self):
        """The window's usual Stop: cancel() makes the stream end by itself, and the reader
        reads on to that end; nothing closes the generator. Only the check that the answer was
        complete keeps a half translation off the clipboard."""
        def stream(messages, model, temperature=0.5, timeout=600, cancel=None):
            yield "first "
            if cancel and cancel.cancelled:
                return  # like llm.chat_stream once cancel() ended the connection
            yield "second"
        done, real = [], llm.chat_stream
        llm.chat_stream = stream
        try:
            chat, cancel = self.chat(done), llm.Cancel()
            answer = chat.ask("Привіт XYZ", cancel)
            self.assertEqual(next(answer), "first ")
            cancel.cancel()
            self.assertEqual(list(answer), [])  # read on to the end, as Window.work does
            self.assertEqual(done, [])
            self.assertEqual(chat.turns[-1]["content"], "first")  # kept in the history
        finally:
            llm.chat_stream = real

    def test_reload_keeps_kind(self):
        with tempfile.TemporaryDirectory() as d:
            real = ask.SAVE_DIR, ask.SAVE
            ask.SAVE_DIR, ask.SAVE = d, True
            try:
                chat = ask.Chat(save=True, kind="uktr", turns=[{"role": "user", "content": "Привіт"},
                                                               {"role": "assistant", "content": "Hallo"}])
                chat.write()
                loaded = ask.Chat.load(chat.path)
                self.assertEqual((loaded.kind, loaded.model, loaded.follow_up), ("uktr", ask.MODEL, uktr.FOLLOW_UP))
            finally:
                ask.SAVE_DIR, ask.SAVE = real


class ModelPerUse(unittest.TestCase):
    def test_file_model(self):
        used = []
        real = uktr.translate, llm.extract, llm.notify, uktr.FILE_MODEL
        uktr.translate = lambda text, model=None, to=None: used.append(model) or text
        llm.extract = lambda path: "a\n\nb"
        llm.notify = lambda *args, **kwargs: None
        uktr.FILE_MODEL = "slow-but-careful"
        try:
            with tempfile.TemporaryDirectory() as d:
                uktr.translate_file(os.path.join(d, "doc.txt"), os.path.join(d, "doc.nl.md"), notify=True)
        finally:
            uktr.translate, llm.extract, llm.notify, uktr.FILE_MODEL = real
        self.assertEqual(used, ["slow-but-careful"])

    def test_dictate_model(self):
        used = []
        real = llm.transcribe, uktr.translate, dictate.MODEL
        llm.transcribe = lambda audio, lang: "hallo"
        uktr.translate = lambda text, model=None, to=None: used.append((model, to)) or "hello"
        dictate.MODEL = "fast"
        try:
            self.assertEqual(dictate.text_of(b"audio", True), "hello")
        finally:
            llm.transcribe, uktr.translate, dictate.MODEL = real
        self.assertEqual(used, [("fast", dictate.TO)])


class Eli12Chat(unittest.TestCase):
    """eli12 in the ask window: its rules and clean() for the first answer, ask's after that."""

    def chat(self):
        return ask.Chat(model="m-eli12", kind="eli12", first=ask.First(rules="ELI12 RULES", clean=eli12.clean),
                        follow_up=" FOLLOW")

    def stream(self, chat, question, pieces):
        real, sent = llm.chat_stream, []
        llm.chat_stream = lambda messages, model, cancel=None: sent.append((messages, model)) or iter(pieces)
        try:
            shown = [chat.shown(a) for a in ("".join(pieces[:i]) for i in range(1, len(pieces) + 1))]
            list(chat.ask(question))
        finally:
            llm.chat_stream = real
        return sent[0], shown

    def test_first_answer_then_follow_up(self):
        chat = self.chat()
        (messages, model), shown = self.stream(chat, "Geachte heer, betaal 10 euro.",
                                               ["[mess", "age]\nJe moet betalen.", "\n\nTe doen: betaal"])
        self.assertEqual((messages[0]["content"], model), ("ELI12 RULES", "m-eli12"))
        self.assertEqual(shown[0], "")  # a half label is not shown
        self.assertEqual(shown[1], "Je moet betalen.")
        self.assertEqual(chat.turns[-1]["content"], "Je moet betalen.\n\nTe doen: betaal")
        (messages, model), _ = self.stream(chat, "Wat als ik te laat ben?", ["[document] Dan ", "kost het meer."])
        self.assertIn("helpful assistant", messages[0]["content"])  # ask's rules, not eli12's
        self.assertTrue(messages[0]["content"].endswith(" FOLLOW"))
        self.assertEqual(model, "m-eli12")
        self.assertEqual(chat.turns[-1]["content"], "[document] Dan kost het meer.")  # not cleaned

    def test_title_save_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            real = ask.SAVE_DIR, ask.SAVE
            ask.SAVE_DIR, ask.SAVE = d, True
            try:
                chat = ask.Chat(save=True, model="m", kind="eli12", first=ask.First(rules="R", clean=eli12.clean),
                                turns=[{"role": "user", "content": "Geachte heer Brouwer,"},
                                       {"role": "assistant", "content": "De **gemeente** wil geld."}])
                chat.write()
                self.assertEqual(chat.title, "💡 De gemeente wil geld.")
                loaded = ask.Chat.load(chat.path)
                self.assertEqual((loaded.kind, loaded.model, loaded.follow_up), ("eli12", eli12.MODEL, eli12.FOLLOW_UP))
                self.assertEqual(ask.saved_chats()[0][1], "💡 De gemeente wil geld.")
            finally:
                ask.SAVE_DIR, ask.SAVE = real

    def test_window_ok(self):
        real = eli12.WINDOW
        try:
            eli12.WINDOW = True
            self.assertFalse(eli12.window_ok(["nl", "en"]))  # both: two explanations, text box
            eli12.WINDOW = False
            self.assertFalse(eli12.window_ok(["nl"]))
        finally:
            eli12.WINDOW = real


class Dictate(unittest.TestCase):
    def test_stop_signal_is_caught(self):
        """After catch_stop(), the hotkey's SIGUSR1 ends the recording, not the process."""
        import signal
        dictate.STOP.clear()
        dictate.catch_stop()
        os.kill(os.getpid(), signal.SIGUSR1)
        self.assertTrue(dictate.STOP)
        dictate.STOP.clear()

    def test_one_line(self):
        real = llm.transcribe
        llm.transcribe = lambda audio, lang: "ls\nrm -rf x\t now"
        try:
            self.assertEqual(dictate.text_of(b"audio", False), "ls rm -rf x now")
        finally:
            llm.transcribe = real


class DictateRunning(unittest.TestCase):
    """The hotkey sends SIGUSR1 only to a process that runs dictate, not to any pid in the file."""

    def check(self, args):
        with tempfile.TemporaryDirectory() as d:
            script = os.path.join(d, "dictate.py")
            with open(script, "w") as f:
                f.write("import time\ntime.sleep(10)\n")
            real = dictate.PIDFILE, dictate.ME
            dictate.PIDFILE, dictate.ME = os.path.join(d, "dictate.pid"), script
            proc = subprocess.Popen([sys.executable, *args(script)], cwd=d)
            try:
                with open(dictate.PIDFILE, "w") as f:
                    f.write(str(proc.pid))
                for _ in range(50):  # until exec() has set the command line
                    with open(f"/proc/{proc.pid}/cmdline", "rb") as f:
                        if f.read().count(b"\0") >= len(args(script)) + 1:
                            break
                    time.sleep(0.02)
                return dictate.running() == proc.pid
            finally:
                proc.kill()
                proc.wait()
                dictate.PIDFILE, dictate.ME = real

    def test_this_script(self):
        self.assertTrue(self.check(lambda script: [script]))
        self.assertTrue(self.check(lambda script: ["dictate.py"]))  # relative to its folder

    def test_another_program(self):
        # The old check: "dictate" anywhere in the command line, such as vim dictate.py.
        self.assertFalse(self.check(lambda script: ["-c", "import time; time.sleep(10)", script]))

    def test_no_pid_file(self):
        real, dictate.PIDFILE = dictate.PIDFILE, None
        try:
            self.assertIsNone(dictate.running())
        finally:
            dictate.PIDFILE = real


class FakeFLM:
    """A streaming server that behaves like FastFlowLM when a client leaves: it ends the answer
    with more than one write (the last piece, then the end), and records whether those writes
    succeeded. When one fails, FastFlowLM never frees the connection (issue #680); the first write
    to a closed connection still succeeds, the next one fails, so two writes tell them apart.
    mode: "stream" writes pieces until the client is gone; "prefill" writes nothing until the
    client is gone, like a model that is still loading; "silent" never answers; "hang_up" takes
    the connection and closes it, like FastFlowLM at its limit."""

    def __init__(self, mode="stream"):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.mode, self.saw_eof, self.end_written = mode, None, None
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.getsockname()[1]}/v1"

    def serve(self):
        conn, _ = self.server.accept()
        with conn:
            if self.mode == "hang_up":
                return
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(65536)
            head, body = data.split(b"\r\n\r\n", 1)
            length = int(re.search(rb"Content-Length: (\d+)", head).group(1))
            while len(body) < length:
                body += conn.recv(65536)
            if self.mode == "silent":
                time.sleep(3)
                return
            if self.mode == "prefill":
                conn.settimeout(5)
                self.saw_eof = conn.recv(1) == b""  # nothing until the client is gone
                conn.settimeout(None)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n")
            if self.mode == "stream":
                conn.setblocking(False)
                for i in range(200):
                    piece = f'data: {{"choices":[{{"delta":{{"content":"w{i} "}}}}]}}\n\n'.encode()
                    conn.sendall(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
                    time.sleep(0.02)
                    try:
                        if conn.recv(1) == b"":  # the client closed its sending side: it is gone
                            self.saw_eof = True
                            break
                    except BlockingIOError:
                        continue
                    except OSError:
                        self.saw_eof = False  # a reset, not a clean close
                        break
                conn.setblocking(True)
            end = b'data: {"choices":[{"delta":{},"finish_reason":"cancel"}]}\n\ndata: [DONE]\n\n'
            try:
                for chunk in (b'data: {"choices":[{"delta":{"content":"last"}}]}\n\n', end):
                    conn.sendall(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    time.sleep(0.1)
                conn.sendall(b"0\r\n\r\n")
                self.end_written = True
            except OSError:
                self.end_written = False

    def close(self):
        self.thread.join(5)
        self.server.close()


class Cancel(unittest.TestCase):
    def setUp(self):
        self.real = llm.URL

    def tearDown(self):
        llm.URL = self.real

    def test_cancel_while_waiting_for_the_first_word(self):
        """A server that takes the request and does not answer (a model that is loading): the
        stream stops at once on cancel(), not after the timeout."""
        server = FakeFLM("silent")
        llm.URL = server.url
        cancel, out, stopped = llm.Cancel(), [], []

        def read():
            out.extend(llm.chat_stream([], "m", cancel=cancel))
            stopped.append(time.monotonic())
        t = threading.Thread(target=read)
        t.start()
        time.sleep(0.3)
        start = time.monotonic()
        cancel.cancel()
        t.join(5)
        self.assertFalse(t.is_alive(), "the stream did not stop")
        self.assertLess(stopped[0] - start, 0.5)
        self.assertEqual(out, [])
        server.close()

    def test_stop_lets_the_server_end_the_answer(self):
        """Stop after the first words: at once for the reader, and the server can still write
        the end of its answer."""
        server = FakeFLM()
        llm.URL = server.url
        cancel, out = llm.Cancel(), []
        for piece in llm.chat_stream([], "m", cancel=cancel):
            out.append(piece)
            if len(out) == 3:
                start = time.monotonic()
                cancel.cancel()
        self.assertLess(time.monotonic() - start, 0.5)
        server.close()
        self.assertEqual(len(out), 3)
        self.assertTrue(server.saw_eof, "the server did not see a clean close")
        self.assertTrue(server.end_written, "the server could not write the end of the answer")

    def test_stop_before_the_first_word(self):
        """Stop while the server has not answered yet: at once for the reader, and the server,
        which answers only after it sees the client go, can still write its whole end."""
        server = FakeFLM("prefill")
        llm.URL = server.url
        cancel = llm.Cancel()
        threading.Timer(0.3, cancel.cancel).start()
        start = time.monotonic()
        self.assertEqual(list(llm.chat_stream([], "m", cancel=cancel)), [])
        self.assertLess(time.monotonic() - start, 0.8)
        server.close()
        self.assertTrue(server.saw_eof)
        self.assertTrue(server.end_written, "the server could not write the end of the answer")

    def test_ctrl_c_before_the_first_word(self):
        """Ctrl+C in the terminal while the model is still loading: KeyboardInterrupt reaches
        the main thread, not the read, and the server can still finish."""
        server = FakeFLM("prefill")
        llm.URL = server.url
        stream = llm.chat_stream([], "m")
        # A real SIGINT, as Ctrl+C sends: it interrupts the wait for the first line at once
        # (_thread.interrupt_main() would only raise once the main thread runs Python again).
        threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGINT)).start()
        start = time.monotonic()
        with self.assertRaises(KeyboardInterrupt):
            next(stream)
        self.assertLess(time.monotonic() - start, 1)
        stream.close()
        server.close()
        self.assertTrue(server.saw_eof)
        self.assertTrue(server.end_written, "the server could not write the end of the answer")

    def test_reader_that_leaves_early(self):
        """Ctrl+C after the first words, or an error: the reader leaves without cancel(). The
        stream ends the same way, so the server can still finish."""
        server = FakeFLM()
        llm.URL = server.url
        stream = llm.chat_stream([], "m")
        self.assertEqual(next(stream), "w0 ")
        stream.close()
        server.close()
        self.assertTrue(server.saw_eof)
        self.assertTrue(server.end_written)

    def test_server_that_hangs_up(self):
        """FastFlowLM at its connection limit takes the connection and closes it: say what that
        is and what to do, naming the server that was asked, not BrokenPipeError."""
        calls = (lambda url: list(llm.chat_stream([], "m")), lambda url: llm.chat([], "m"),
                 lambda url: llm._open(urllib.request.Request(url + "/audio/transcriptions", b"x"), 5))
        for call in calls:
            server = FakeFLM("hang_up")
            llm.URL = server.url
            with self.assertRaises(llm.Error) as e:
                call(server.url)
            self.assertIn("issues/680", str(e.exception))
            self.assertIn(server.url, str(e.exception))
            server.close()


class Fixtext(unittest.TestCase):
    def test_changes(self):
        n, summary = fixtext.changes("teh quick fox jumpd", "The quick fox jumped")
        self.assertEqual(n, 2)
        self.assertEqual(summary, "teh → The\njumpd → jumped")

    def test_changes_limit(self):
        n, summary = fixtext.changes("a b c d", "A B C D", limit=6)
        self.assertEqual(n, 1)  # one replaced run of words
        n, summary = fixtext.changes("a x b x c x d", "A x B x C x D", limit=2)
        self.assertEqual(n, 4)
        self.assertTrue(summary.endswith("… and 2 more"))

    def test_plausible(self):
        self.assertTrue(fixtext.plausible("Can you tel me wich train?", "Can you tell me which train?"))
        self.assertFalse(fixtext.plausible("Can you tell me which train to take tomorrow?", "Yes."))


class Eli12(unittest.TestCase):
    def test_message_keeps_todo(self):
        out = eli12.clean("[message]\nPiet wants money.\n\nTe doen: pay before 15 November.")
        self.assertEqual(out, "Piet wants money.\n\nTe doen: pay before 15 November.")

    def test_document_keeps_first_paragraph(self):
        out = eli12.clean("[document] This tool translates text.\n\nInstall it with pip.\nTe doen: none")
        self.assertEqual(out, "This tool translates text.")

    def test_no_label(self):
        self.assertEqual(eli12.clean("Just an answer."), "Just an answer.")


class Ask(unittest.TestCase):
    shot: ClassVar[list] = [{"type": "text", "text": "What is on this screen?"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}]

    def test_text_and_images(self):
        self.assertEqual(ask.text_of("hi"), "hi")
        self.assertEqual(ask.text_of(self.shot), "What is on this screen?")
        self.assertEqual(ask.images_of(self.shot), ["data:image/jpeg;base64,AAAA"])
        self.assertEqual(ask.images_of("hi"), [])

    def test_title(self):
        self.assertEqual(ask.title_of("short\nsecond line"), "short")
        self.assertEqual(len(ask.title_of("x" * 100)), 60)
        chat = ask.Chat(turns=[{"role": "user", "content": self.shot},
                               {"role": "assistant", "content": "A **Konsole** window."}])
        self.assertEqual(chat.title, "📷 A Konsole window.")

    def test_history_keeps_newest(self):
        old = {"role": "user", "content": "x" * ask.HISTORY_CHARS}
        new = {"role": "user", "content": "newest"}
        msgs = ask.Chat(turns=[old, new]).messages()
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[1:], [new])

    def test_history_keeps_one_long_turn(self):
        long = {"role": "user", "content": "x" * (ask.HISTORY_CHARS * 2)}
        self.assertEqual(ask.Chat(turns=[long]).messages()[1:], [long])

    def test_questions(self):
        self.assertEqual(ask.questions(2), "1 question")
        self.assertEqual(ask.questions(6), "3 questions")

    def test_valid_turn(self):
        self.assertTrue(ask.valid_turn({"role": "user", "content": "hi"}))
        self.assertTrue(ask.valid_turn({"role": "user", "content": self.shot}))
        for bad in ({"role": "system", "content": "x"}, {"role": "user"}, {"role": "user", "content": 3},
                    {"role": "user", "content": [{"type": "text"}]},
                    {"role": "user", "content": [{"type": "image_url", "image_url": {}}]}, "hi", None):
            self.assertFalse(ask.valid_turn(bad), bad)

    def test_broken_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            real, ask.SAVE_DIR = ask.SAVE_DIR, d
            try:
                for name, text in (("list.json", "[]"), ("turns.json", '{"turns": 3}'),
                                   ("title.json", '{"title": {"a": 1}, "turns": []}'), ("bad.json", "{")):
                    with open(os.path.join(d, name), "w") as f:
                        f.write(text)
                self.assertEqual([t for _, t, _, _ in ask.saved_chats()], ["{'a': 1}"])  # a title is a string
                for name in ("list.json", "turns.json"):
                    with self.assertRaises(ValueError):
                        ask.Chat.load(os.path.join(d, name))
            finally:
                ask.SAVE_DIR = real

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            real = ask.SAVE_DIR, ask.SAVE
            ask.SAVE_DIR, ask.SAVE = d, True
            try:
                a = ask.Chat(save=True, turns=[{"role": "user", "content": "first"}])
                b = ask.Chat(save=True, turns=[{"role": "user", "content": "second"}])
                a.write()
                b.write()  # same second: must not overwrite a
                self.assertNotEqual(a.path, b.path)
                self.assertEqual(sorted(t for _, t, _, _ in ask.saved_chats()), ["first", "second"])
                self.assertEqual(ask.Chat.load(a.path).turns, a.turns)
                self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(os.stat(a.path).st_mode), 0o600)
            finally:
                ask.SAVE_DIR, ask.SAVE = real


if __name__ == "__main__":
    unittest.main()
