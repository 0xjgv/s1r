#!/bin/sh
# Install or update s1r from a checksummed GitHub release.
# SYSTEM_ONE_ROUTER_VERSION        release version, e.g. v0.1.2 (default: latest)
# SYSTEM_ONE_ROUTER_DOWNLOAD_BASE  release base URL, or an absolute directory holding
#                                  checksums.txt and the wheel (local testing)
# SYSTEM_ONE_ROUTER_HOME           where release wheels are kept (default: $HOME/.local/share/s1r)
# When an anonymous download fails and the GitHub CLI is logged in, it downloads through `gh`.
set -eu

REPO=0xjgv/s1r
NAME=s1r
OLD=system-one-router  # the tool and folder name before v0.1.4

die() {
    echo "$NAME install: $*" >&2
    exit 1
}

command -v uv >/dev/null 2>&1 || die "uv is required: https://docs.astral.sh/uv/"

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        die "sha256sum or shasum is required"
    fi
}

curl_get() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$1" -o "$2" 2>/dev/null
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$2" "$1"
    else
        return 1
    fi
}

version=${SYSTEM_ONE_ROUTER_VERSION:-}
version=${version#v}
if [ -n "$version" ]; then
    printf '%s\n' "$version" | awk '/^[0-9]+\.[0-9]+\.[0-9]+$/ {valid=1} END {exit !valid}' ||
        die "SYSTEM_ONE_ROUTER_VERSION must be a version such as v0.1.0"
fi

base=${SYSTEM_ONE_ROUTER_DOWNLOAD_BASE:-https://github.com/$REPO/releases}
base=${base%/}
case $base in
/*) release=$base ;;
*) if [ -n "$version" ]; then release=$base/download/v$version; else release=$base/latest/download; fi ;;
esac

# fetch <file> <dest>: local directory, anonymous HTTPS, then the GitHub CLI.
fetch() {
    case $release in
    /*) cp "$release/$1" "$2" 2>/dev/null && return 0 ;;
    *)
        curl_get "$release/$1" "$2" && return 0
        if command -v gh >/dev/null 2>&1; then
            gh release download ${version:+"v$version"} -R "$REPO" -p "$1" -O "$2" --clobber >/dev/null 2>&1 && return 0
        fi
        ;;
    esac
    die "download failed: $release/$1"
}

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' 0
trap 'exit 1' HUP INT TERM

fetch checksums.txt "$tmpdir/checksums.txt"
wheel=$(awk -v version="$version" '
    {sub(/^\*/, "", $2)}
    $2 ~ /^s1r-[0-9]+[.][0-9]+[.][0-9]+-py3-none-any[.]whl$/ &&
        (version == "" || $2 == "s1r-" version "-py3-none-any.whl") {
        found=$2; count++
    }
    END {if (count == 1) print found}
' "$tmpdir/checksums.txt")
[ -n "$wheel" ] || die "expected one $NAME wheel in $release/checksums.txt"
expected=$(awk -v name="$wheel" '{sub(/^\*/, "", $2)} $2 == name {print $1}' "$tmpdir/checksums.txt")
fetch "$wheel" "$tmpdir/$wheel"
[ "$(sha256 "$tmpdir/$wheel")" = "$expected" ] || die "checksum mismatch for $wheel"
wheel_version=${wheel#s1r-}
wheel_version=${wheel_version%-py3-none-any.whl}

# Move config, logs, and kept wheels from the $OLD folders once; never overwrite.
for root in "${XDG_CONFIG_HOME:-$HOME/.config}" "${XDG_STATE_HOME:-$HOME/.local/state}" "$HOME/.local/share"; do
    if [ -d "$root/$OLD" ] && [ ! -e "$root/$NAME" ]; then
        mv "$root/$OLD" "$root/$NAME" && echo "Moved $root/$OLD to $root/$NAME"
    fi
done

store=${SYSTEM_ONE_ROUTER_HOME:-$HOME/.local/share/$NAME}/releases
mkdir -p "$store"
cp "$tmpdir/$wheel" "$store/$wheel"

bin_dir=$(uv tool dir --bin)
previous=
if [ -x "$bin_dir/$NAME" ]; then
    previous=$("$bin_dir/$NAME" version 2>/dev/null || true)
fi
# The $OLD tool also provides $NAME; remove it first so uv does not keep a stale receipt.
if uv tool list 2>/dev/null | grep -q "^$OLD "; then
    uv tool uninstall "$OLD" >/dev/null 2>&1 || die "uv tool uninstall $OLD failed"
fi
uv tool install --quiet --force --python '>=3.9' "$store/$wheel" || die "uv tool install failed for $wheel"
current=$("$bin_dir/$NAME" version 2>/dev/null) || die "installed $NAME does not run"
[ "$current" = "$wheel_version" ] || die "installed $NAME reported an unexpected version: $current"

if [ -n "$previous" ] && [ "$previous" != "$current" ]; then
    echo "Updated $bin_dir/$NAME: $previous -> $current"
else
    echo "Installed $bin_dir/$NAME: $current"
fi

# Seed a private config file once; never overwrite an existing one.
config=${XDG_CONFIG_HOME:-$HOME/.config}/$NAME/.env
if [ ! -e "$config" ]; then
    mkdir -p "$(dirname "$config")"
    (umask 077 && printf '%s\n' '# s1r credentials; environment variables take precedence.' \
        'TYPESAFE_API_KEY=' 'OPENROUTER_API_KEY=' 'RESPAN_API_KEY=' > "$config")
    echo "Created $config; add your keys, then run: $NAME status"
fi
case :$PATH: in
*":$bin_dir:"*) ;;
*) echo "Add $bin_dir to PATH: export PATH=\"$bin_dir:\$PATH\"" ;;
esac
