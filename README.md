# local-llm-hotkeys

[![CI](https://github.com/annejan/local-llm-hotkeys/actions/workflows/ci.yml/badge.svg)](https://github.com/annejan/local-llm-hotkeys/actions/workflows/ci.yml)

**Your laptop has an AI key. Make it do something, on your own NPU.**

New laptops have a Copilot key. On Linux it does nothing. This project gives it four jobs,
and runs all of them on the AMD Ryzen AI NPU that is already in the laptop. There is no cloud,
no account and no subscription. Nothing leaves your computer, it works offline, and the GPU
stays free.

| Press | You get |
|---|---|
| **Copilot** | a chat window. Ask anything, then ask follow-up questions. |
| **Alt+Copilot** | the copied text, translated to your language. |
| **Ctrl+Copilot** | the copied text explained in a few plain sentences, in your language, with a to-do list if it asks you to do something. |
| **Ctrl+Alt+Copilot** | the window in front of you explained: "what is this error, and what do I do?" |

Some examples:

- A letter from the tax office that you do not understand: copy it, press Ctrl+Copilot,
  and read what it says and what you must do before which date.
- A Ukrainian news article, a German invoice, a Japanese error message: copy it and press
  Alt+Copilot.
- An error dialog, a settings page, a chart: press Ctrl+Alt+Copilot, then ask "and how do I
  fix that?"

"Your language" is the language of your desktop, or the one you set (`UKTR_LANGUAGE`). No AI
key? Every action also has a Meta+Shift shortcut.

## Hotkeys

| Key | Action |
|---|---|
| Copilot or Meta+Shift+Q | chat (`ask --popup`) |
| Alt+Copilot or Meta+Shift+T | translate the clipboard to your language (`uktr clip`) |
| Ctrl+Copilot or Meta+Shift+E | explain the clipboard (`eli12 clip`) |
| Ctrl+Alt+Copilot or Meta+Shift+W | explain the active window (`ask --screen`) |
| Meta+Shift+Y | translate a screen region (`uktr shot`) |
| Meta+Shift+F | fix spelling and grammar of the clipboard (`fixtext clip`) |
| Meta+Shift+D | dictate: speak, and the text is typed into the active window (`dictate`) |

In Dolphin, right-click a document for "Translate (uktr)" and "Explain like I'm 12 (eli12)".
AltGr works as Alt. See [Install](#install) for the setup and [the Copilot key](#the-copilot-key).

### The tools

- **uktr**: rough translation of Ukrainian (or any other language) to your language: the
  clipboard, a screen region, documents, or live subtitles for audio.
- **eli12**: explains a text like you are 12.
- **fixtext**: fixes spelling and grammar of the clipboard.
- **ask**: a chat window for quick questions and follow-ups, also about a screenshot.
- **dictate**: speak, and the text is typed into the active window.

All of them talk to any OpenAI-compatible server. The default setup runs everything on the NPU
with [FastFlowLM](https://github.com/FastFlowLM/FastFlowLM); ollama on a GPU also works.
Python standard library only, except the `ask` chat window, which uses PySide6.

## uktr: translate

Rough translation with a local LLM. It is for reading, not for publishing. The output is
rough, and some mistakes are funny.

| Command | Input | Output |
|---|---|---|
| `uktr` | stdin | stdout |
| `uktr clip` | clipboard | popup window, result also copied to the clipboard |
| `uktr shot` | screen region (spectacle + tesseract OCR) | popup window |
| `uktr FILE [-o OUT]` | `.pdf` `.epub` `.docx` `.odt` `.html` `.txt` `.md` | `FILE.nl.md` (your language) |
| `uktr --notify FILE` | same, with notifications (Dolphin menu) | `FILE.nl.md` |
| `uktr listen [--orig]` | system audio (live) | subtitles in the terminal |

The target language is your language (`UKTR_LANGUAGE`, else the desktop's). Set `UKTR_TO` to
translate to another one. The output file gets the language code: `FILE.nl.md` for Dutch,
`FILE.en.md` for English. Long text goes to the model in chunks of about 1500 characters,
split at paragraph breaks.

### `uktr listen`

Live subtitles for what plays on the speakers (YouTube, a call, a podcast):

1. `parec` records the default sink monitor at 16 kHz mono.
2. Audio is cut into chunks of 4–12 s. The cut goes at the quietest 0.25 s, so words
   are not split. Silent chunks are dropped (Whisper invents "Thank you." on silence).
3. Whisper transcribes each chunk (`/v1/audio/transcriptions`).
4. The translation model translates the text.

`--orig` also shows the transcription, dimmed. Lines appear about 12 s after the speech.
On the NPU, 30 s of audio takes about 7 s to transcribe and translate.

The spoken language is set with `UKTR_LANG` (default `uk`). Whisper language detection
is unreliable on short chunks, so set it.

```sh
konsole -e uktr listen --orig
```

## eli12: explain it like I'm 12

`eli12` reads a text (a long boring email, a README, an article, a PDF) and says in at most
5 short sentences what it is about and what matters most, in plain words.

If the text asks you to do something, as an email or letter does, `eli12` ends with a line
`Te doen:` (`To do:` in English) that lists every action with its deadline. Dates are copied
as written. For documentation such as a README there is no such line: the model first labels
the text as a message or a document, and the code drops everything after the explanation for a
document.

| Command | Input | Output |
|---|---|---|
| `eli12 FILE` | same file types as uktr | stdout |
| `eli12 < text` | stdin | stdout |
| `eli12 clip` | clipboard | popup window |
| `eli12 --popup FILE` | file | popup window (Dolphin menu) |

It writes in your language. `-l LANG` picks another one (`-l en`, `-l German`), and `-l both`
gives your language followed by English.

It uses a chat model (`ELI12_MODEL`, default `qwen3.5:9b`), because translation models cannot
explain. A long README (about 2000 words) takes about 25 s on the NPU. Text after the first
60,000 characters is ignored.

Example (a long email from a facility team about a new booking system and lockers):

> Het facilitair team introduceert een nieuw reserveringssysteem en vervangt kluisjes. Je moet
> je oude boekingen opnieuw maken en persoonlijke spullen uit de kasten halen.
>
> Te doen: […] terugkerende werkplekreserveringen in FlexDesk 4 uiterlijk vrijdag 12 december
> […]; persoonlijke eigendommen uit kluisjes op de tweede verdieping vóór maandag 8 december.

For an email in Thunderbird: select the text, press Ctrl+C, then Meta+Shift+E.

## fixtext: fix spelling and grammar

Copy a text, press Meta+Shift+F, and paste. `fixtext` fixes spelling, grammar, punctuation
and capitalization in any language. It does not translate or rephrase, and keeps line breaks,
names, URLs and code.

| Command | Input | Output |
|---|---|---|
| `fixtext clip` | clipboard | clipboard |
| `fixtext < text` | stdin | stdout |
| `fixtext FILE` | same file types as uktr | stdout |

With `clip` you get two notifications: "fixing N words…" at the start, and at the end the
changes (`wich → which`, `tomorow? → tomorrow?`) with the fixed text on the clipboard. If
there are no mistakes, the clipboard stays as it was. A short paragraph takes 3–6 s on the
NPU.

The model sometimes answers a question in the text instead of fixing it. If a fixed part is
much shorter or longer than the original, `fixtext` keeps the original part. Model:
`FIX_MODEL`, default `qwen3.5:9b`. Text over 20,000 characters is refused.

## ask: chat

Meta+Shift+Q (or the Copilot key) opens a chat window. Type a question and press Enter; the
answer streams in as the model writes it, with markdown (code blocks, lists). Ask follow-up
questions: the model sees the whole conversation. It answers in the language of your message
and knows today's date, the distribution and the desktop (for example "openSUSE Tumbleweed,
KDE Plasma 6.8 on Wayland"), so commands fit your system.

| Key | Action |
|---|---|
| Enter / Shift+Enter | send / new line |
| Esc | stop the answer, or close the window |
| Ctrl+N | new chat |
| Ctrl+H | show or hide the saved chats |
| Ctrl+Shift+C | copy the last answer |

| Command | Input | Output |
|---|---|---|
| `ask --popup` | chat window | chat window |
| `ask --screen` | screenshot of the active window | chat window |
| `ask` | chat in the terminal (`/new` starts a new chat, Ctrl+D or `/quit` quits) | terminal |
| `ask --list` | saved chats, newest first | stdout |
| `ask --resume [N]` | continue saved chat N (default 1, the newest) | terminal |
| `ask QUESTION...` | command line | stdout |
| `ask < question` | stdin | stdout |

Model: `ASK_MODEL`, default `qwen3.5:9b`. The first words come after about 1 s. When a
conversation gets longer than about 24,000 characters, the oldest messages are dropped. A 9B
model makes mistakes, so check facts that matter.

### Explain the active window

Ctrl+Alt+Copilot or Meta+Shift+W (`ask --screen`) takes a screenshot of the active window and
opens the chat with it. The model says what is on the screen, and explains an error, a question or a choice
if there is one. Then ask follow-up questions about it ("what does this setting do?").

`qwen3.5:9b` reads images, so this is the same model as the chat: about 8 s for the first
answer on the NPU. The screenshot is scaled to 1280 pixels and saved with the chat. Set
`ASK_SCREEN_PROMPT` to change the first question. The answer is in your language.

### Saved chats

Every chat from the window or the terminal is saved after each answer, as a JSON file in
`~/.local/share/uktr/ask/`. The window shows them on the left, newest first: click one to read
it and ask more. Right-click or Del removes a chat (after a confirmation). One-shot questions
(`ask QUESTION`, `ask < question`) are not saved. Set `ASK_HISTORY=0` to save nothing.

## dictate: speak instead of type

Press Meta+Shift+D and speak. When you stop talking for 2 seconds (or press Meta+Shift+D
again), Whisper on the NPU writes down what you said, and the text is typed into the window
that has the focus: a chat, an email, a terminal.

| Command | Input | Output |
|---|---|---|
| `dictate` | microphone | typed into the active window |
| `dictate --print` | microphone | stdout |

You speak your language (`DICTATE_LANG`, default `UKTR_LANGUAGE`). The text is typed by
pasting: it goes on the clipboard and the primary selection, and a virtual keyboard presses
Shift+Insert, which pastes in Qt and GTK apps, browsers and Konsole. The virtual keyboard
needs write access to `/dev/uinput`. KDE Connect installs a udev rule that gives it to the logged-in user (on Debian and Ubuntu, see [Requirements](#requirements));
check with `test -w /dev/uinput && echo ok`. Without it, the text stays on the clipboard for
Ctrl+V.

If it stops too early or does not stop, set `DICTATE_SILENCE` (the microphone level that
counts as quiet, default 150) or `DICTATE_PAUSE` (seconds, default 2).

## Install

### Requirements

- KDE Plasma 6 on Wayland, for the hotkeys and popups. Tested on openSUSE Tumbleweed; the
  installer knows the package names of openSUSE, Debian/Ubuntu, Fedora and Arch.
- Python 3.9+
- A server with an OpenAI-compatible API: FastFlowLM on an AMD Ryzen AI NPU (see
  [below](#backend-fastflowlm-on-the-npu)), or ollama on a GPU/CPU.

`./install.sh` checks all of this and prints the install command for your distribution.
These are the same lines:

```sh
# openSUSE
sudo zypper install wl-clipboard kdialog libnotify-tools spectacle python313-pyside6 \
    tesseract-ocr tesseract-ocr-traineddata-ukr poppler-tools pandoc-cli pulseaudio-utils
# Debian 13, Ubuntu 25.10+
sudo apt install wl-clipboard kdialog libnotify-bin kde-spectacle python3-pyside6.qtwidgets \
    tesseract-ocr tesseract-ocr-ukr poppler-utils pandoc pulseaudio-utils
# Fedora
sudo dnf install wl-clipboard kdialog libnotify spectacle python3-pyside6 \
    tesseract tesseract-langpack-ukr poppler-utils pandoc-cli pulseaudio-utils
# Arch
sudo pacman -S --needed wl-clipboard kdialog libnotify spectacle pyside6 \
    tesseract tesseract-data-ukr poppler pandoc-cli libpulse
```

| Package | Needed for |
|---|---|
| wl-clipboard, kdialog, libnotify | the hotkeys: clipboard, popup, notifications |
| PySide6 | the `ask` window (without it, `ask` opens in Konsole) |
| spectacle | `uktr shot`, `ask --screen` |
| tesseract with Ukrainian data | `uktr shot` (OCR) |
| poppler (`pdftotext`), pandoc | PDF, and epub/docx/odt/html files |
| pulseaudio-utils (`parec`) | `uktr listen`, `dictate` |

`dictate` types with a virtual keyboard, which needs write access to `/dev/uinput`. KDE
Connect gives the logged-in user that access on openSUSE, Fedora and Arch. On Debian and
Ubuntu, add the same rule from this repository:

```sh
sudo cp udev/60-local-llm-hotkeys-uinput.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger --sysname-match=uinput
```

Kubuntu 24.04 has Plasma 5 and no PySide6: the commands work, but the hotkeys are not tested
there and `ask` opens in Konsole.

### Install with the script

```sh
git clone https://github.com/annejan/local-llm-hotkeys.git
cd local-llm-hotkeys
./install.sh
```

`install.sh` needs no root. It:

- checks the packages above and prints the install line for your distribution (zypper, apt,
  dnf or pacman), and the udev rule for `dictate` if it is needed,
- links the commands into `~/.local/bin` (they point into this directory, so keep it where
  it is; after a `git pull` you have the new version at once),
- creates `~/.config/uktr/config` if there is none, and shows your language,
- installs the KDE hotkeys, the Dolphin menu and the Copilot key map,
- checks the model server and lists the models that are missing.

Run it again after an update; it changes only what is missing or new. `./install.sh --flm`
also installs and starts the FastFlowLM service, and `./install.sh --uninstall` removes
everything it added (your config and saved chats stay).

### Check a release download

Every release has a source tarball and `SHA256SUMS`. GitHub signs where they came from (built
by this repository's release workflow, from that tag), and the maintainer signs `SHA256SUMS`
with OpenPGP:

```sh
gh attestation verify local-llm-hotkeys-0.1.0.tar.gz -R annejan/local-llm-hotkeys
sha256sum -c SHA256SUMS
gpg --verify SHA256SUMS.asc SHA256SUMS   # key and fingerprint: see SECURITY.md
```

### Install by hand

```sh
mkdir -p ~/.local/bin ~/.config/uktr
for tool in uktr eli12 fixtext ask dictate; do ln -s "$PWD/$tool.py" ~/.local/bin/$tool; done
cp config.example ~/.config/uktr/config
cp kde/*.desktop ~/.local/share/applications/
kbuildsycoca6
mkdir -p ~/.local/share/kio/servicemenus
cp kde/servicemenu/uktr.desktop ~/.local/share/kio/servicemenus/
chmod +x ~/.local/share/kio/servicemenus/uktr.desktop
```

`~/.local/bin` must be in your `PATH` (it is by default on openSUSE). Check with `uktr --help`.
The shortcuts come from `X-KDE-Shortcuts` in the `.desktop` files. After `kbuildsycoca6`,
KDE (Plasma 6) registers them at once. To change a key, use System Settings → Shortcuts.

### The Copilot key

The Copilot key (on newer laptops, such as the ThinkPad T14 Gen 6) sends Meta+Shift+F23.
Recent keyboard maps (xkeyboard-config, `inet` symbols) turn that into the key symbol
`XF86Assistant`. Qt does not know that symbol, so KDE cannot use the key as a shortcut. The
files in `kde/xkb/` keep the key as plain F23 for every layout:

```sh
mkdir -p ~/.config/xkb && cp -r kde/xkb/* ~/.config/xkb/
```

Log out and in again, so that KWin loads the new keyboard map. KDE then sees Meta+Shift+F23.
With an older keyboard map, KDE sees "Meta+Shift+Touchpad Off" instead; the `.desktop` files
list both names, so the shortcuts work either way.

Ctrl and Alt are free to add, so there are four combinations: Copilot (ask), Ctrl+Copilot
(eli12), Alt+Copilot (translate) and Ctrl+Alt+Copilot (explain the active window). AltGr
works as Alt here.

## Backend: FastFlowLM on the NPU

FastFlowLM runs on the NPU of AMD Ryzen AI 300 laptops (XDNA2) with the `amdxdna` kernel
driver. Its portable tarball works on any distribution. Tested on a Ryzen AI 7 PRO 350 with
openSUSE Tumbleweed, kernel 7.2+. Without such an NPU, use ollama (see [Models](#models)).

1. Get the portable Linux tarball (`fastflowlm_<version>_linux.tar.gz`) from the FastFlowLM
   releases. It includes XRT and the xdna plugin, so you do not have to build XRT. Put `flm`
   in `~/.local/bin`.
2. Raise the locked-memory limit. The NPU needs more than the 8 MB default. Plasma apps
   get their limits from the systemd user manager, not from PAM, so use a drop-in for
   `user@.service`:

   ```ini
   # /etc/systemd/system/user@.service.d/memlock.conf
   [Service]
   LimitMEMLOCK=infinity
   ```

   Log in again, then check that `ulimit -l` shows `unlimited`.
3. Get the models:

   ```sh
   flm pull translategemma:4b    # translation (uktr), ~4.5 GB
   flm pull hy-mt2-flash:1.8b    # fast translation (uktr listen), ~1.4 GB
   flm pull whisper-v3:turbo     # speech-to-text (uktr listen, dictate), ~0.6 GB
   flm pull qwen3.5:9b           # chat and images (eli12, fixtext, ask)
   ```

4. Install and start the server (OpenAI API on port 52625):

   ```sh
   ./install.sh --flm
   ```

   Or by hand: copy `systemd/flm.service` to `~/.config/systemd/user/`, then
   `systemctl --user daemon-reload && systemctl --user enable --now flm`.

   `-a 1` loads Whisper next to the other model.

### Models

- `translategemma:4b`: a translation model. Better than Hy-MT2 with slang and abbreviations,
  but about 3.5x slower (6.3 s against 1.8 s for two paragraphs). The default for `clip`,
  `shot` and files.
- `hy-mt2-flash:1.8b` (Hunyuan Hy-MT2): a model only for translation. It is fast on the NPU
  (about 50 tokens/s output) and gives good results; the example config uses it for
  `listen`, where speed matters. uktr sends it the user-only prompt it was trained on when
  the model name contains `hy-mt`.
- `qwen3.5:9b`: a chat model that also reads images. eli12, fixtext and ask use it.
- FLM loads one language model at a time (Whisper stays loaded next to it), so the first
  request after a change of model (for example translate, then chat) takes a few seconds more.
- With ollama, any chat model works for translation too (for example `gemma4:12b`); it gets
  a system prompt that tells it to output only the translation. Set `ELI12_MODEL`,
  `FIX_MODEL` and `ASK_MODEL` to a chat model that you have.

## Configuration

`~/.config/uktr/config` holds `KEY=value` lines (`#` starts a comment). Environment variables
with the same name override the file. See `config.example`.

| Key | Default | Meaning |
|---|---|---|
| `UKTR_URL` | `http://localhost:52625/v1` | OpenAI-compatible API base URL (all tools) |
| `UKTR_MODEL` | `translategemma:4b` | translation model |
| `UKTR_LISTEN_MODEL` | `UKTR_MODEL` | translation model for `listen` |
| `UKTR_LANGUAGE` | the desktop language | your language: a code or name (`nl`, `Dutch`) |
| `UKTR_TO` | `UKTR_LANGUAGE` | target language for uktr |
| `UKTR_OCR` | `ukr+eng` | tesseract languages for `shot` |
| `UKTR_LANG` | `uk` | spoken language for `listen` (empty = auto-detect) |
| `UKTR_ASR_URL` | `UKTR_URL` | API base URL for Whisper |
| `UKTR_ASR_MODEL` | `whisper-v3:turbo` | Whisper model |
| `UKTR_LISTEN_MAX` | `12` | longest audio chunk, in seconds |
| `UKTR_SILENCE` | `300` | int16 RMS level that counts as quiet |
| `ELI12_MODEL` | `qwen3.5:9b` | chat model for `eli12` |
| `ELI12_SENTENCES` | `5` | longest explanation for `eli12`, in sentences |
| `ELI12_LANG` | `UKTR_LANGUAGE` | language for `eli12` (a code, a name, or `both`) |
| `FIX_MODEL` | `qwen3.5:9b` | chat model for `fixtext` |
| `ASK_MODEL` | `qwen3.5:9b` | chat model for `ask` (must read images for `--screen`) |
| `ASK_HISTORY` | `1` | `0`: do not save `ask` chats |
| `DICTATE_LANG` | `UKTR_LANGUAGE` | language you speak to `dictate` (empty = Whisper guesses) |
| `DICTATE_SILENCE` | `150` | microphone level that counts as quiet |
| `DICTATE_PAUSE` | `2` | seconds of quiet after speech that end a dictation |
| `DICTATE_MAX` | `120` | longest dictation, in seconds |
| `ASK_SCREEN_PROMPT` | (English, answer in your language) | first question for `ask --screen`; `{language}` = your language |

Example, one-off: translate to German, and subtitles for Russian audio:

```sh
UKTR_TO=German uktr < article.txt
UKTR_LANG=ru uktr listen
```

## Development

`llm.py` holds what the tools share: config, your language, the chat and Whisper requests,
file extraction, clipboard, notifications and the popup. The tests need no server:

```sh
python3 -m unittest discover -s tests
```

CI runs the tests on Python 3.9 to 3.13, lints with ruff and shellcheck, and checks the package
names of every distribution. See [CONTRIBUTING.md](CONTRIBUTING.md); report security problems
as described in [SECURITY.md](SECURITY.md).

## License

MIT, see [LICENSE](LICENSE).
