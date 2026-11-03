# Contributing

Bug reports, ideas and pull requests are welcome. Tell what you use: distribution, Plasma
version, and the NPU or GPU with the model server.

## Before a pull request

```sh
python3 -m unittest discover -s tests   # unit tests, no model server needed
ruff check .                            # Python lint (rules in ruff.toml)
shellcheck install.sh tests/*.sh
desktop-file-validate kde/*.desktop
```

CI runs the same checks, plus `tests/distro-packages.sh` in openSUSE, Debian, Ubuntu, Fedora
and Arch containers.

After a model or prompt change, run `tests/injection.py` by hand (it needs the model server):
it shows how often a hidden instruction in a text steers eli12 and uktr. See the numbers in
[#5](https://github.com/annejan/local-llm-hotkeys/issues/5) for what to expect. If you add a dependency, add its package name for each distribution to
`pkg()` in `install.sh`, and to the install lines in the README.

## Guidelines

- Python standard library only; PySide6 only in the `aikey` window (`ask.py`).
- Code shared by the tools goes in `llm.py`.
- Default to the NPU (FastFlowLM) and to the user's language.
- Errors from a hotkey must show as a notification (`llm.errors`), never only on stderr.
- Keep the README in plain, short sentences.
- Do not change `Exec=` of a hotkey `.desktop` file that users already have: a running Plasma
  keeps the old command until the next login ([KDE bug 487941](https://bugs.kde.org/show_bug.cgi?id=487941)).
  Give the file a new name instead, and let `install.sh` remove the old one (as for
  `ask.desktop`, which became `aikey.desktop` in 0.4).

## Making a release

```sh
git tag -s v0.2.0 -m "v0.2.0: ..."     # a signed tag
git push origin v0.2.0
```

The release workflow builds the tarball and `SHA256SUMS`, attests them, and attaches them to
the release (a draft if there is none yet). Then sign the checksums with the maintainer key:

```sh
gh release download v0.2.0 -p SHA256SUMS
gpg --armor --detach-sign SHA256SUMS
gh release upload v0.2.0 SHA256SUMS.asc
```

Release tags cannot be moved or deleted (a ruleset protects `v*`).
