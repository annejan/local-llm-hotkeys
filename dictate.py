#!/usr/bin/env python3
"""dictate: speak, and the text is typed into the active window. Whisper runs on the NPU.

  dictate           start listening (KDE hotkey Meta+Shift+D); it stops after a pause in
                    your speech, or when you press the hotkey again
  dictate --print   the same from a terminal: print the text instead of typing it
  dictate --translate [--print]
                    the same, but the text is translated first (to DICTATE_TO, default
                    English): speak your language, type another (Meta+Ctrl+Shift+D)

The text is typed by pasting: it goes on the clipboard and the primary selection, and a
virtual keyboard presses Shift+Insert (that pastes in Qt, GTK, browsers and Konsole). This
needs write access to /dev/uinput; without it the text stays on the clipboard for Ctrl+V.

Uses the same server and config file as uktr (~/.config/uktr/config, UKTR_ASR_URL).
  DICTATE_LANG      language you speak, default your language (UKTR_LANGUAGE, else the
                    desktop's); "" = Whisper guesses
  DICTATE_SILENCE   microphone level that counts as quiet, default 150
  DICTATE_PAUSE     seconds of quiet after speech that end the dictation, default 2
  DICTATE_MAX       longest dictation in seconds, default 120
  DICTATE_TO        language for --translate, default English
  DICTATE_MODEL     translation model for --translate, default UKTR_MODEL; a fast one
                    (hy-mt2-flash:1.8b) types sooner
"""
import contextlib
import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import llm
import uktr

LANG = llm.conf["DICTATE_LANG"] if "DICTATE_LANG" in llm.conf else llm.language()[0]
SILENCE = float(llm.conf.get("DICTATE_SILENCE", "150"))
PAUSE = float(llm.conf.get("DICTATE_PAUSE", "2"))
MAX = float(llm.conf.get("DICTATE_MAX", "120"))
TO = llm.parse_language(llm.conf.get("DICTATE_TO", "English"))[1]
MODEL = llm.conf.get("DICTATE_MODEL") or uktr.MODEL
WAIT = 8  # seconds to wait for the first word before giving up
BLOCK = llm.RATE // 4 * 2  # 0.25 s of 16 kHz s16 audio, in bytes
# Only in your own runtime folder: in /tmp another user can make the file first.
RUNTIME = os.environ.get("XDG_RUNTIME_DIR")
PIDFILE = os.path.join(RUNTIME, "dictate.pid") if RUNTIME else None
ME = os.path.realpath(__file__)
STOP = []  # not empty once the hotkey was pressed again (SIGUSR1)


class Keyboard:
    """A virtual keyboard (uinput) that can press Shift+Insert. Created at the start, because
    the desktop needs about a second to pick up a new input device."""
    SHIFT, INSERT = 42, 110

    def __init__(self):
        import fcntl
        import struct
        self.fcntl, self.struct = fcntl, struct
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, 0x40045564, 1)  # UI_SET_EVBIT EV_KEY
        for key in (self.SHIFT, self.INSERT):
            fcntl.ioctl(self.fd, 0x40045565, key)  # UI_SET_KEYBIT
        os.write(self.fd, struct.pack("80sHHHHi", b"dictate", 3, 1, 1, 1, 0) + bytes(4 * 64 * 4))
        fcntl.ioctl(self.fd, 0x5501)  # UI_DEV_CREATE

    def key(self, code, down):
        for kind, c, value in ((1, code, int(down)), (0, 0, 0)):  # EV_KEY, then EV_SYN
            os.write(self.fd, self.struct.pack("llHHi", 0, 0, kind, c, value))
        time.sleep(0.02)

    def paste(self):
        self.key(self.SHIFT, True)
        self.key(self.INSERT, True)
        self.key(self.INSERT, False)
        self.key(self.SHIFT, False)

    def close(self):
        time.sleep(0.2)  # let the last events through before the device goes away
        self.fcntl.ioctl(self.fd, 0x5502)  # UI_DEV_DESTROY
        os.close(self.fd)


def running():
    """The pid of a dictation that is listening now, or None. The process must run this
    script: the pid in the file can be old and belong to another program now, and SIGUSR1
    stops a program that does not handle it."""
    if not PIDFILE:
        return None
    try:
        with open(PIDFILE) as f:
            pid = int(f.read())
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            args = f.read().decode("utf-8", "replace").split("\0")
    except (OSError, ValueError):
        return None
    # python3 /path/dictate (a hotkey) or /path/dictate: the script is the 1st or 2nd argument.
    cwd = f"/proc/{pid}/cwd"
    return pid if any(os.path.realpath(os.path.join(cwd, a)) == ME for a in args[:2] if a) else None


def catch_stop():
    """From now on SIGUSR1 ends the recording. Before this call it ends the process, so it
    must come before the pid file exists: a second press during start-up killed the first
    dictation silently."""
    signal.signal(signal.SIGUSR1, lambda *_: STOP.append(True))


def record():
    """Record the microphone until a pause after speech, SIGUSR1 (hotkey pressed again),
    Ctrl+C or DICTATE_MAX. Returns the audio, or b"" if nothing was said."""
    catch_stop()
    rec = subprocess.Popen(["parec", "-d", "@DEFAULT_SOURCE@", "--format=s16le",
                            f"--rate={llm.RATE}", "--channels=1", "--raw", "--latency-msec=100"],
                           stdout=subprocess.PIPE)
    audio, heard, quiet, n = bytearray(), False, 0.0, 0
    try:
        while not STOP and (block := rec.stdout.read(BLOCK)):
            n += 1
            if n <= 2:
                continue  # the first 0.5 s holds the click of the microphone switching on
            audio += block
            if llm.rms(block) >= SILENCE:
                heard, quiet = True, 0.0
            else:
                quiet += 0.25
            secs = len(audio) / (llm.RATE * 2)
            if (heard and quiet >= PAUSE) or secs >= MAX or (not heard and secs >= WAIT):
                break
    except KeyboardInterrupt:
        pass
    finally:
        rec.terminate()
    return bytes(audio) if heard else b""


def text_of(audio, translate):
    """What was said, translated to TO with translate=True ("" if nothing was said). It is one
    line: a line break pasted into a terminal is Enter, and runs what came before it."""
    text = llm.transcribe(audio, LANG) if audio else ""
    text = uktr.translate(text, MODEL, to=TO) if text and translate else text
    return " ".join(text.split())


def main(argv):
    if argv[:1] in (["-h"], ["--help"]):
        return print(__doc__)
    translate = "--translate" in argv
    argv = [a for a in argv if a != "--translate"]
    if argv == ["--print"]:
        with llm.errors("dictate", gui=False):
            to = f" → {TO}" if translate else ""
            print(f"listening ({LANG or 'any language'}{to}); stop talking or press Ctrl+C…",
                  file=sys.stderr)
            print(text_of(record(), translate) or "(nothing heard)")
        return
    if argv:
        sys.exit(__doc__)

    if not PIDFILE:
        with llm.errors("dictate", gui=True):
            raise llm.Error("XDG_RUNTIME_DIR is not set (is this a desktop session?)")
    with llm.errors("dictate", gui=True):
        pid = running()
        if pid:  # the hotkey again: stop that dictation, it types the text
            with contextlib.suppress(ProcessLookupError):  # it ended just now
                os.kill(pid, signal.SIGUSR1)
            return
        catch_stop()
        with open(PIDFILE, "w") as f:
            f.write(str(os.getpid()))
    keyboard = None
    try:
        with llm.errors("dictate", gui=True):
            try:
                keyboard = Keyboard()
            except OSError:
                keyboard = None  # no /dev/uinput access: the text stays on the clipboard
            to = f", typed in {TO}" if translate else ""
            llm.notify(f"listening{to}… (stop talking, or press the hotkey again)", "dictate", 2500)
            text = text_of(record(), translate)
            if not text:
                return llm.notify("nothing heard", "dictate")
            llm.copy(text)
            subprocess.run(["wl-copy", "--primary"], input=text, text=True)
            if keyboard:
                keyboard.paste()
            else:
                llm.notify(f"on the clipboard, press Ctrl+V: {text[:80]}", "dictate", 8000)
    finally:
        if keyboard:
            keyboard.close()
        if running() == os.getpid():
            os.unlink(PIDFILE)


if __name__ == "__main__":
    main(sys.argv[1:])
