# uktr

Rough translation of Ukrainian (or any other language) to English with a local LLM.
It is for reading, not for publishing. The output is rough, and some mistakes are funny.

uktr talks to any OpenAI-compatible server. The default setup runs everything on the
AMD Ryzen AI NPU with [FastFlowLM](https://github.com/FastFlowLM/FastFlowLM), so the GPU
stays free (for example for video playback).

One script, Python standard library only.

## Modes

| Command | Input | Output |
|---|---|---|
| `uktr` | stdin | stdout |
| `uktr clip` | clipboard | popup window, result also copied to the clipboard |
| `uktr shot` | screen region (spectacle + tesseract OCR) | popup window |
| `uktr FILE [-o OUT]` | `.pdf` `.epub` `.docx` `.odt` `.html` `.txt` `.md` | `FILE.en.md` |
| `uktr listen [--orig]` | system audio (live) | subtitles in the terminal |

Long text goes to the model in chunks of about 1500 characters, split at paragraph breaks.

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

## Requirements

- Python 3.9+
- A server with an OpenAI-compatible API: FastFlowLM (NPU) or ollama (GPU/CPU)
- `clip`: `wl-clipboard`, `kdialog`, `notify-send`
- `shot`: `spectacle`, `tesseract` with Ukrainian data (`tesseract-ocr-traineddata-ukr` on openSUSE)
- `FILE`: `pdftotext` (poppler) for PDF, `pandoc` for epub/docx/odt/html
- `listen`: `parec` (pipewire-pulse or PulseAudio), and a server with Whisper

## Install

```sh
ln -s "$PWD/uktr.py" ~/.local/bin/uktr
mkdir -p ~/.config/uktr && cp config.example ~/.config/uktr/config
```

KDE integration (optional):

```sh
cp kde/uktr-clip.desktop kde/uktr-shot.desktop ~/.local/share/applications/
mkdir -p ~/.local/share/kio/servicemenus
cp kde/uktr-servicemenu.desktop ~/.local/share/kio/servicemenus/uktr.desktop
chmod +x ~/.local/share/kio/servicemenus/uktr.desktop
```

- Meta+Shift+T: translate the clipboard
- Meta+Shift+Y: translate a screen region
- Dolphin right-click on a document: "Translate to English (uktr)"

The shortcuts come from `X-KDE-Shortcuts` in the `.desktop` files. If they do not work
after you log in again, set them in System Settings → Shortcuts.

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
- `translategemma:4b`: also a translation model. Try it if Hy-MT2 gets a text wrong.
- Any chat model works too (for example `gemma4:12b` in ollama). These get a system prompt
  that tells them to output only the translation.

## Configuration

`~/.config/uktr/config` holds `KEY=value` lines. Environment variables with the same name
override the file. See `config.example`.

| Key | Default | Meaning |
|---|---|---|
| `UKTR_URL` | `http://localhost:11434/v1` | OpenAI-compatible API base URL |
| `UKTR_MODEL` | `gemma4:12b` | translation model |
| `UKTR_TO` | `English` | target language |
| `UKTR_OCR` | `ukr+eng` | tesseract languages for `shot` |
| `UKTR_LANG` | `uk` | spoken language for `listen` (empty = auto-detect) |
| `UKTR_ASR_URL` | `UKTR_URL` | API base URL for Whisper |
| `UKTR_ASR_MODEL` | `whisper-v3:turbo` | Whisper model |
| `UKTR_LISTEN_MAX` | `12` | longest audio chunk, in seconds |
| `UKTR_SILENCE` | `300` | int16 RMS level that counts as quiet |

Example, one-off Russian to Dutch:

```sh
UKTR_TO=Dutch uktr < article.txt
UKTR_LANG=ru uktr listen
```
