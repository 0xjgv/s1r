#!/bin/sh
# Build the release assets into dist-release/: the wheel, install.sh, and checksums.txt.
# The same assets serve GitHub Releases and `make install`.
set -eu

root=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
cd "$root"
out=dist-release
version=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml)
wheel=s1r-$version-py3-none-any.whl

rm -rf "$out" build *.egg-info
mkdir -p "$out"
uv build --quiet --wheel --out-dir "$out"
rm -rf build *.egg-info
[ -f "$out/$wheel" ] || { echo "release: uv build did not write $out/$wheel" >&2; exit 1; }
rm -f "$out/.gitignore"
cp install.sh "$out/install.sh"
if command -v sha256sum >/dev/null 2>&1; then
    (cd "$out" && sha256sum "$wheel" install.sh > checksums.txt)
else
    (cd "$out" && shasum -a 256 "$wheel" install.sh > checksums.txt)
fi
echo "$out/$wheel"
