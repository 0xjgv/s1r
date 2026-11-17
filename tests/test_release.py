import base64
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from system_one_router import signature as ed

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "minisign"
KEY = (FIXTURES / "key.pub").read_text().splitlines()[1]
MESSAGE = (FIXTURES / "checksums.txt").read_bytes()
SIGNATURE = (FIXTURES / "checksums.txt.minisig").read_text()

# RFC 8032, section 7.1, tests 1 and 2: (public key, message, signature).
RFC_VECTORS = [
    ("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]

# Test-only Ed25519 signing (RFC 8032, section 6) so the tests can sign releases.
SECRET = hashlib.sha256(b"s1r test key").digest()
KEY_ID = b"s1rtest1"


def compress(point):
    inverse = pow(point[2], ed.p - 2, ed.p)
    x, y = point[0] * inverse % ed.p, point[1] * inverse % ed.p
    return (y | (x & 1) << 255).to_bytes(32, "little")


def ed25519_sign(message):
    digest = hashlib.sha512(SECRET).digest()
    a = int.from_bytes(digest[:32], "little") & ((1 << 254) - 8) | 1 << 254
    public = compress(ed.point_mul(a, ed.G))
    r = ed.sha512_modq(digest[32:] + message)
    encoded_r = compress(ed.point_mul(r, ed.G))
    s = (r + ed.sha512_modq(encoded_r + public + message) * a) % ed.q
    return encoded_r + s.to_bytes(32, "little"), public


PUBLIC_KEY = base64.b64encode(b"Ed" + KEY_ID + ed25519_sign(b"")[1]).decode()


def minisign(message, comment="s1r test"):
    """Sign like `minisign -S`: prehashed signature plus a signed trusted comment."""
    signature, _ = ed25519_sign(hashlib.blake2b(message).digest())
    signed_comment, _ = ed25519_sign(signature + comment.encode())
    blob = base64.b64encode(b"ED" + KEY_ID + signature).decode()
    return f"untrusted comment: test\n{blob}\ntrusted comment: {comment}\n{base64.b64encode(signed_comment).decode()}\n"


class SignatureTests(unittest.TestCase):
    def test_ed25519_matches_rfc_8032_vectors(self):
        for public, message, signature in RFC_VECTORS:
            public, message, signature = map(bytes.fromhex, (public, message, signature))
            self.assertTrue(ed.ed25519_verify(public, message, signature))
            self.assertFalse(ed.ed25519_verify(public, message + b"x", signature))
            flipped = bytes([signature[0] ^ 1]) + signature[1:]
            self.assertFalse(ed.ed25519_verify(public, message, flipped))

    def test_accepts_prehashed_and_legacy_minisign_signatures(self):
        self.assertEqual("s1r v9.9.9", ed.verify(KEY, MESSAGE, SIGNATURE))
        self.assertEqual("legacy", ed.verify(KEY, MESSAGE, (FIXTURES / "legacy.minisig").read_text()))

    def test_rejects_tampering_and_other_keys(self):
        lines = SIGNATURE.splitlines()
        cases = {
            "message": (KEY, MESSAGE + b"x", SIGNATURE),
            "trusted comment": (KEY, MESSAGE, "\n".join([*lines[:2], "trusted comment: s1r v0.0.1", lines[3]])),
            "other key": (PUBLIC_KEY, MESSAGE, SIGNATURE),
            "truncated": (KEY, MESSAGE, "\n".join(lines[:3])),
            "not base64": (KEY, MESSAGE, "\n".join([lines[0], "!!", *lines[2:]])),
        }
        for name, arguments in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                ed.verify(*arguments)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="s1r installer ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.release = self.root / "release"
        self.release.mkdir()
        self.bin = self.root / "bin"
        commands = self.root / "commands"
        commands.mkdir()
        # A stand-in uv whose `tool install` writes an s1r that prints the wheel's version.
        (commands / "uv").write_text(
            '#!/bin/sh\ncase "$1 $2" in\n"tool dir") echo "$TEST_BIN" ;;\n'
            '"tool install") for wheel; do :; done; version=${wheel##*/s1r-}; version=${version%%-*}\n'
            '    mkdir -p "$TEST_BIN"; printf \'#!/bin/sh\\necho %s\\n\' "$version" > "$TEST_BIN/s1r"\n'
            '    chmod 755 "$TEST_BIN/s1r" ;;\nesac\n'
        )
        (commands / "uv").chmod(0o755)
        self.verifier = commands / "python-verifier"
        self.verifier.write_text(f'#!/bin/sh\nPYTHONPATH="{ROOT}" exec "{sys.executable}" "$@"\n')
        self.verifier.chmod(0o755)
        self.installer = self.root / "install.sh"
        self.installer.write_text(
            re.sub(r"(?m)^PUBLIC_KEY=.*$", f"PUBLIC_KEY={PUBLIC_KEY}", (ROOT / "install.sh").read_text())
        )
        self.environment = {
            **os.environ,
            "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_STATE_HOME": str(self.root / "state"),
            "SYSTEM_ONE_ROUTER_HOME": str(self.root / "share"),
            "SYSTEM_ONE_ROUTER_DOWNLOAD_BASE": str(self.release),
            "TEST_BIN": str(self.bin),
        }
        for name in ("SYSTEM_ONE_ROUTER_VERSION", "SYSTEM_ONE_ROUTER_VERIFIER"):
            self.environment.pop(name, None)

    def publish(self, version="0.1.0"):
        for old in self.release.glob("s1r-*.whl"):
            old.unlink()
        wheel = self.release / f"s1r-{version}-py3-none-any.whl"
        wheel.write_bytes(f"wheel {version}".encode())
        checksums = f"{hashlib.sha256(wheel.read_bytes()).hexdigest()}  {wheel.name}\n".encode()
        (self.release / "checksums.txt").write_bytes(checksums)
        (self.release / "checksums.txt.minisig").write_text(minisign(checksums))

    def install(self, **overrides):
        return subprocess.run(["sh", str(self.installer)], env={**self.environment, **overrides},
                              text=True, capture_output=True)

    def test_signature_is_required_by_the_verifier_and_minisign(self):
        modes = {"s1r update": {"SYSTEM_ONE_ROUTER_VERIFIER": str(self.verifier)}}
        if shutil.which("minisign", path=self.environment["PATH"]):
            modes["minisign"] = {}
        for mode, overrides in modes.items():
            with self.subTest(mode=mode):
                self.publish()
                result = self.install(**overrides)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("not verified", result.stderr)
                (self.bin / "s1r").unlink()
                checksums = self.release / "checksums.txt"
                checksums.write_text(checksums.read_text() + "\n")
                tampered = self.install(**overrides)
                self.assertNotEqual(tampered.returncode, 0)
                self.assertIn("invalid signature", tampered.stderr)
                (self.release / "checksums.txt.minisig").unlink()
                unsigned = self.install(**overrides)
                self.assertNotEqual(unsigned.returncode, 0)
                self.assertIn("checksums.txt.minisig", unsigned.stderr)
                self.assertFalse((self.bin / "s1r").exists())

    def test_installs_without_minisign_after_a_warning(self):
        self.publish()
        path = os.pathsep.join(entry for entry in self.environment["PATH"].split(os.pathsep)
                               if not (Path(entry) / "minisign").exists())
        result = self.install(PATH=path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("signature not verified", result.stderr)

    def test_updates_and_skips_the_download_when_current(self):
        self.publish("0.1.0")
        self.assertEqual(self.install().returncode, 0)
        self.publish("0.2.0")
        update = self.install()
        self.assertEqual(update.returncode, 0, update.stderr)
        self.assertIn("0.1.0 -> 0.2.0", update.stdout)
        (self.release / "s1r-0.2.0-py3-none-any.whl").unlink()
        current = self.install()
        self.assertEqual(current.returncode, 0, current.stderr)
        self.assertIn("already up to date: 0.2.0", current.stdout)

    def test_local_build_installs_unsigned_assets_and_reinstalls_the_same_version(self):
        self.publish("0.1.0")
        self.assertEqual(self.install().returncode, 0)
        (self.release / "checksums.txt.minisig").unlink()
        local = {"SYSTEM_ONE_ROUTER_LOCAL_BUILD": "1"}
        for overrides in ({}, {"SYSTEM_ONE_ROUTER_VERIFIER": str(self.verifier)}):
            with self.subTest(verifier=bool(overrides)):
                result = self.install(**local, **overrides)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("signature not checked", result.stderr)
                self.assertNotIn("already up to date", result.stdout)
        remote = self.install(**local, SYSTEM_ONE_ROUTER_DOWNLOAD_BASE="https://example.invalid/releases")
        self.assertNotEqual(remote.returncode, 0)
        self.assertIn("needs SYSTEM_ONE_ROUTER_DOWNLOAD_BASE to be a local directory", remote.stderr)


if __name__ == "__main__":
    unittest.main()
