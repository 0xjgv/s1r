#!/bin/sh
# Put minisign on a CI runner without depending on the runner image's packages.
#
#   scripts/install-minisign.sh DIR   install DIR/minisign; the caller adds DIR to PATH
#
# Linux gets the static x86_64 or aarch64 binary from minisign's pinned upstream
# release. SHA256 pins the archive that jedisct1's release key
# RWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3 signed. Upstream
# ships no Intel macOS binary, so macOS uses Homebrew, which every GitHub
# macOS image has.
set -eu

VERSION=0.12
SHA256=9a599b48ba6eb7b1e80f12f36b94ceca7c00b7a5173c95c3efc88d9822957e73

dest=${1:?usage: install-minisign.sh DIR}

case "$(uname -s)" in
  Darwin)
    brew install minisign
    exit 0
    ;;
  Linux) ;;
  *) echo "unsupported OS: $(uname -s)" >&2; exit 1 ;;
esac

case "$(uname -m)" in
  x86_64 | amd64) arch=x86_64 ;;
  aarch64 | arm64) arch=aarch64 ;;
  *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL --retry 3 -o "$tmp/minisign.tar.gz" \
  "https://github.com/jedisct1/minisign/releases/download/$VERSION/minisign-$VERSION-linux.tar.gz"
echo "$SHA256  $tmp/minisign.tar.gz" | sha256sum -c --quiet -
tar -xzf "$tmp/minisign.tar.gz" -C "$tmp"
mkdir -p "$dest"
install -m 0755 "$tmp/minisign-linux/$arch/minisign" "$dest/minisign"
"$dest/minisign" -v
