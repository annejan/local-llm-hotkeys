# Security

local-llm-hotkeys runs on your own computer and talks only to the model server you configure
(by default FastFlowLM on `localhost`). It sends no data anywhere else.

## Reporting a problem

Please report a security problem privately, not in a public issue: use
[Report a vulnerability](https://github.com/annejan/local-llm-hotkeys/security/advisories/new)
on GitHub. Say what can go wrong and how to reproduce it. You get an answer within a week.

## Things to know

- `dictate` creates a virtual keyboard through `/dev/uinput` to paste text. The udev rule in
  `udev/` gives the logged-in user that access, the same rule that KDE Connect and Steam
  install. Any program of that user can then create input devices.
- Saved `ask` chats, including screenshots, are plain JSON files in `~/.local/share/uktr/ask/`.
  Set `ASK_HISTORY=0` to save nothing.
- Text and screenshots go to the server in `UKTR_URL`. If you point that to a remote server,
  your clipboard and screenshots go there.
