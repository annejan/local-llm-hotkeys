"""Tests for the logic that needs no LLM server. Run: python3 -m unittest discover -s tests"""
import io
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
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
        self.assertTrue(request[0]["content"].startswith("Translate the following text into Dutch (nl)."))
        self.assertTrue(request[0]["content"].endswith("\n\n\nПривіт"))
        self.assertIn("into German (de)", uktr.messages("x", "qwen3.5:9b", "de")[0]["content"])

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
        return ask.Chat(model="m-eli12", kind="eli12", first=("ELI12 RULES", eli12.clean), follow_up=" FOLLOW")

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
                chat = ask.Chat(save=True, model="m", kind="eli12", first=("R", eli12.clean),
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


class Cancel(unittest.TestCase):
    def test_cancel_while_waiting_for_the_first_word(self):
        """A server that takes the request and does not answer yet (a model that is loading):
        cancel() must stop the stream at once, not after the timeout."""
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        conns = []
        threading.Thread(target=lambda: conns.append(server.accept()), daemon=True).start()
        real, llm.URL = llm.URL, f"http://127.0.0.1:{server.getsockname()[1]}/v1"
        cancel, out = llm.Cancel(), []
        try:
            t = threading.Thread(target=lambda: out.extend(llm.chat_stream([], "m", cancel=cancel)))
            start = time.monotonic()
            t.start()
            time.sleep(0.3)
            cancel.cancel()
            t.join(5)
            self.assertFalse(t.is_alive(), "the stream did not stop")
            self.assertLess(time.monotonic() - start, 2)
            self.assertEqual(out, [])
        finally:
            llm.URL = real
            server.close()
            for conn, _ in conns:
                conn.close()


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
