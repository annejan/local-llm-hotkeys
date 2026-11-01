# local-llm-hotkeys

KDE hotkeys and Dolphin menu actions that send text to a local LLM. Four tools:

- **uktr**: rough translation of Ukrainian (or any other language) to English.
- **eli12**: explains a text like you are 12.
- **fixtext**: fixes spelling and grammar of the clipboard.
- **ask**: a chat window for quick questions and follow-ups.

Both talk to any OpenAI-compatible server. The default setup runs everything on the
AMD Ryzen AI NPU with [FastFlowLM](https://github.com/FastFlowLM/FastFlowLM), so the GPU
stays free (for example for video playback). Nothing leaves your computer.

Python standard library only, except the `ask` chat window, which uses PySide6.

## uktr: translate

Rough translation with a local LLM. It is for reading, not for publishing. The output is
rough, and some mistakes are funny.

### Modes

| Command | Input | Output |
|---|---|---|
| `uktr` | stdin | stdout |
| `uktr clip` | clipboard | popup window, result also copied to the clipboard |
| `uktr shot` | screen region (spectacle + tesseract OCR) | popup window |
| `uktr FILE [-o OUT]` | `.pdf` `.epub` `.docx` `.odt` `.html` `.txt` `.md` | `FILE.en.md` |
| `uktr listen [--orig]` | system audio (live) | subtitles in the terminal |

Long text goes to the model in chunks of about 1500 characters, split at paragraph breaks.

#### `uktr listen`

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

`eli12` is a second tool in this repository. It reads a text (a long boring email, a
README, an article, a PDF) and says in at most 5 short sentences what it is about and what
matters most, in plain words.

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

The default language is Dutch (`ELI12_LANG` in the config). `-l en` gives English, and `-l both`
gives Dutch followed by English.

It uses the same server and config file as uktr, but a chat model (`ELI12_MODEL`, default
`qwen3.5:9b`), because translation models cannot explain. A long README (about 2000 words) takes
about 25 s on the NPU. Text after the first 60,000 characters is ignored.

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

Meta+Shift+Q opens a chat window. Type a question and press Enter; the answer streams in
as the model writes it, with markdown (code blocks, lists). Ask follow-up questions: the
window keeps the conversation until you press New chat or close it. The model answers in
the language of your message and knows today's date, the distribution and the desktop
(for example "openSUSE Tumbleweed, KDE Plasma 6.8.1 on Wayland"), so commands fit your system.

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
| `ask` | chat in the terminal (`/new` starts a new chat, empty line quits) | terminal |
| `ask --list` | saved chats, newest first | stdout |
| `ask --resume [N]` | continue saved chat N (default 1, the newest) | terminal |
| `ask QUESTION...` | command line | stdout |
| `ask < question` | stdin | stdout |

### Saved chats

Every chat from the window or the terminal is saved after each answer, as a JSON file in
`~/.local/share/uktr/ask/`. The window shows them on the left, newest first: click one to read
it and ask more. Right-click or Del removes a chat (after a confirmation). One-shot questions
(`ask QUESTION`, `ask < question`) are not saved. Set `ASK_HISTORY=0` to save nothing.

The window needs PySide6 (`python313-pyside6` on openSUSE). Without it, `ask --popup` opens
the terminal chat in Konsole. Model: `ASK_MODEL`, default `qwen3.5:9b`. The first words
come after about 1 s. When a conversation gets longer than about 24,000 characters, the
oldest messages are dropped. A 9B model makes mistakes, so check facts that matter.

## Requirements

- Python 3.9+
- A server with an OpenAI-compatible API: FastFlowLM (NPU) or ollama (GPU/CPU)
- `clip`: `wl-clipboard`, `kdialog`, `notify-send`
- `ask --popup`: PySide6
- `shot`: `spectacle`, `tesseract` with Ukrainian data (`tesseract-ocr-traineddata-ukr` on openSUSE)
- `FILE`: `pdftotext` (poppler) for PDF, `pandoc` for epub/docx/odt/html
- `listen`: `parec` (pipewire-pulse or PulseAudio), and a server with Whisper

## Install

```sh
git clone https://github.com/annejan/local-llm-hotkeys.git
cd local-llm-hotkeys
ln -s "$PWD/uktr.py" ~/.local/bin/uktr
ln -s "$PWD/eli12.py" ~/.local/bin/eli12
ln -s "$PWD/fixtext.py" ~/.local/bin/fixtext
ln -s "$PWD/ask.py" ~/.local/bin/ask
mkdir -p ~/.config/uktr && cp config.example ~/.config/uktr/config
```

KDE integration (optional):

```sh
cp kde/*.desktop ~/.local/share/applications/
kbuildsycoca6
mkdir -p ~/.local/share/kio/servicemenus
cp kde/servicemenu/uktr.desktop ~/.local/share/kio/servicemenus/
chmod +x ~/.local/share/kio/servicemenus/uktr.desktop
```

- Meta+Shift+T: translate the clipboard
- Meta+Shift+Y: translate a screen region
- Meta+Shift+E or Ctrl+Copilot: explain the clipboard with eli12
- Meta+Shift+F: fix spelling of the clipboard with fixtext
- Meta+Shift+Q or the Copilot key: chat with ask
- Alt+Copilot (or AltGr+Copilot): translate the clipboard to Dutch
- Dolphin right-click on a document: "Translate to English (uktr)" and "Explain like I'm 12 (eli12)"

The shortcuts come from `X-KDE-Shortcuts` in the `.desktop` files. After `kbuildsycoca6`,
KDE (Plasma 6) registers them at once; it does not write default shortcuts to
`~/.config/kglobalshortcutsrc`. To change a key, use System Settings → Shortcuts.

The Copilot key (on newer laptops, such as the ThinkPad T14 Gen 6) sends Meta+Shift+F23.
KDE calls that "Meta+Shift+Touchpad Off", so that is the name in the `.desktop` files.
Ctrl and Alt are free to add, so there are four combinations: Copilot (ask), Ctrl+Copilot
(eli12), Alt+Copilot (translate to Dutch) and Ctrl+Alt+Copilot (still free). AltGr works as
Alt here.

## Backend: FastFlowLM on the NPU

Tested on a Ryzen AI 7 PRO 350 (XDNA2) with openSUSE Tumbleweed, kernel 7.2+ (`amdxdna`).

1. Get the portable Linux tarball (`fastflowlm_<version>_linux.tar.gz`) from the FastFlowLM
   releases. It includes XRT and the xdna plugin, so you do not have to build XRT.
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
   flm pull hy-mt2-flash:1.8b    # translation, ~1.4 GB
   flm pull translategemma:4b    # better translation, slower, ~4.5 GB
   flm pull whisper-v3:turbo     # speech-to-text for `listen`, ~0.6 GB
   ```

4. Install and start the server (OpenAI API on port 52625):

   ```sh
   cp systemd/flm.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now flm
   ```

   The unit expects `flm` at `~/.local/bin/flm`. `-a 1` loads Whisper next to the
   translation model.

### Models

- `hy-mt2-flash:1.8b` (Hunyuan Hy-MT2): a model only for translation. It is fast on the
  NPU (about 50 tokens/s output) and gives good results. uktr sends it the user-only prompt
  it was trained on when the model name contains `hy-mt`.
- `translategemma:4b`: also a translation model. Better than Hy-MT2 with slang and
  abbreviations, but about 3.5x slower (6.3 s against 1.8 s for two paragraphs).
  The example config uses it for `clip`, `shot` and files, and Hy-MT2 for `listen`.
  FLM loads one translation model at a time, so the first request after a change of
  model takes 2–5 s more.
- Any chat model works too (for example `gemma4:12b` in ollama). These get a system prompt
  that tells them to output only the translation.

## Configuration

`~/.config/uktr/config` holds `KEY=value` lines. Environment variables with the same name
override the file. See `config.example`.

| Key | Default | Meaning |
|---|---|---|
| `UKTR_URL` | `http://localhost:11434/v1` | OpenAI-compatible API base URL |
| `UKTR_MODEL` | `gemma4:12b` | translation model |
| `UKTR_LISTEN_MODEL` | `UKTR_MODEL` | translation model for `listen` |
| `UKTR_TO` | `English` | target language |
| `UKTR_OCR` | `ukr+eng` | tesseract languages for `shot` |
| `UKTR_LANG` | `uk` | spoken language for `listen` (empty = auto-detect) |
| `UKTR_ASR_URL` | `UKTR_URL` | API base URL for Whisper |
| `UKTR_ASR_MODEL` | `whisper-v3:turbo` | Whisper model |
| `UKTR_LISTEN_MAX` | `12` | longest audio chunk, in seconds |
| `UKTR_SILENCE` | `300` | int16 RMS level that counts as quiet |
| `ELI12_MODEL` | `qwen3.5:9b` | chat model for `eli12` |
| `ELI12_SENTENCES` | `5` | longest explanation for `eli12`, in sentences |
| `ELI12_LANG` | `nl` | language for `eli12`: `nl`, `en` or `both` |
| `FIX_MODEL` | `qwen3.5:9b` | chat model for `fixtext` |
| `ASK_MODEL` | `qwen3.5:9b` | chat model for `ask` |
| `ASK_HISTORY` | `1` | `0`: do not save `ask` chats |

Example, one-off Russian to Dutch:

```sh
UKTR_TO=Dutch uktr < article.txt
UKTR_LANG=ru uktr listen
```

## License

MIT, see [LICENSE](LICENSE).
