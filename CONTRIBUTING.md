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
and Arch containers. If you add a dependency, add its package name for each distribution to
`pkg()` in `install.sh`, and to the install lines in the README.

## Guidelines

- Python standard library only; PySide6 only in the `ask` window.
- Code shared by the tools goes in `llm.py`.
- Default to the NPU (FastFlowLM) and to the user's language.
- Errors from a hotkey must show as a notification (`llm.errors`), never only on stderr.
- Keep the README in plain, short sentences.

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
