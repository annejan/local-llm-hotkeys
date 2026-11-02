#!/bin/bash
# Run install.sh in a fresh container of a distribution and check that every package it asks
# for exists there. CI runs this for each distribution. Locally, for example:
#
#   docker run --rm -v "$PWD":/repo:ro debian:trixie bash -c \
#       'apt-get update -qq && apt-get install -y -qq python3 >/dev/null &&
#        cp -r /repo /tmp/r && /tmp/r/tests/distro-packages.sh'
set -u
cd "$(dirname "$0")/.." || exit 1
out=$(./install.sh 2>&1 | sed 's/\x1b\[[0-9;]*m//g')
line=$(sed -n 's/^  To install the missing packages: //p' <<<"$out")
if [ -z "$line" ]; then
    echo "install.sh printed no install line:"; echo "$out"; exit 1
fi
echo "install.sh says: $line"
packages=$(sed -E 's/^sudo (zypper install|apt install|dnf install|pacman -S --needed) //' <<<"$line")
exists() {
    case "$line" in
        *apt*) apt-cache show "$1" >/dev/null 2>&1 ;;
        *dnf*) [ -n "$(dnf -q repoquery --latest-limit 1 "$1" 2>/dev/null)" ] ;;
        *pacman*) pacman -Si "$1" >/dev/null 2>&1 ;;
        *zypper*) zypper -q --non-interactive info "$1" 2>/dev/null | grep -q '^Name' ;;
        *) return 1 ;;
    esac
}
fail=0
for p in $packages; do
    if exists "$p"; then echo "  ok      $p"; else echo "  MISSING $p"; fail=1; fi
done
exit $fail
