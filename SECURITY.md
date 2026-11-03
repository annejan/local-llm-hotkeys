# Security

local-llm-hotkeys runs on your own computer and talks only to the model server you configure
(by default FastFlowLM on `localhost`). It sends no data anywhere else.

## Reporting a problem

Please report a security problem privately, not in a public issue: use
[Report a vulnerability](https://github.com/annejan/local-llm-hotkeys/security/advisories/new)
on GitHub. Say what can go wrong and how to reproduce it. You get an answer within a week.

No GitHub account? Encrypt your report to the maintainer's OpenPGP key and send it to the
address in that key:

- Key: <https://annejan.com/key.asc>
- Fingerprint: `DF7F EF8D FBA7 21E3 20B1  8F56 15B6 3ADC A003 4B9E`

Check that the fingerprint of the key you downloaded is the same before you use it.

## Releases

Release files are attested by GitHub and `SHA256SUMS` is signed with the key above; see
"Check a release download" in the README.

## Things to know

- `dictate` creates a virtual keyboard through `/dev/uinput` to paste text. The udev rule in
  `udev/` gives the logged-in user that access. Any program of that user can then create input
  devices and type or click in any window, which Wayland otherwise prevents. KDE Connect
  (openSUSE, Fedora, Arch) and the Steam udev rules install the same rule, so on those systems
  this access is usually there already; on Debian and Ubuntu only this rule adds it. The rule
  is optional: without it, dictate leaves the text on the clipboard.
  The RemoteDesktop portal is not used instead: a script cannot identify itself to it, so after
  one "Allow" the saved permission is open to every program of the user, and asking at every
  dictation would take the focus from the window that the text is for.
- Saved `aikey` chats, including screenshots, are plain JSON files in
  `~/.local/share/local-llm-hotkeys/chats/`, readable only by you.
  Set `AIKEY_HISTORY=0` to save nothing.
- Text, documents, screenshots and audio can come from someone else, and the model can follow
  instructions that are hidden in them. Its output is never run. A link in an `aikey` answer opens
  only after you confirm the full address, and control characters are removed before text is
  printed or pasted. An explanation or translation can still be wrong on purpose.
- `uktr FILE` and `eli12 FILE` read the file that a link points to. A downloaded archive
  can hold `report.txt -> ~/.ssh/id_ed25519`; "Translate (uktr)" then sends that key to the
  (local) model and writes the result next to the link. Look before you translate a folder
  from someone else.
- Text and screenshots go to the server in `HOTKEYS_URL`. If you point that to a remote server,
  your clipboard and screenshots go there.
