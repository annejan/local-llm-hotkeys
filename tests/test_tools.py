"""Tests for the logic that needs no LLM server. Run: python3 -m unittest discover -s tests"""
import io
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from typing import ClassVar

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ask
import eli12
import fixtext
import llm


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

    def test_error_chunk(self):
        with self.assertRaises(llm.Error):
            self.stream(['data: {"error":{"message":"model not found"}}'])


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
            finally:
                ask.SAVE_DIR, ask.SAVE = real


if __name__ == "__main__":
    unittest.main()
