import contextlib
import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from system_one_router import cli


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "minisign"


class UpdateTests(unittest.TestCase):
    def run_update(self, argv):
        with patch("system_one_router.cli.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 3)) as run:
            status = cli.main(argv)
        return status, run.call_args

    def test_update_runs_the_bundled_installer_with_the_verifier(self):
        status, call = self.run_update(["update"])
        self.assertEqual(status, 3)
        command, environment = call.args[0], call.kwargs["env"]
        self.assertEqual(command, ["sh", str(Path(cli.__file__).with_name("install.sh"))])
        self.assertTrue(Path(command[1]).is_file())
        self.assertEqual(environment["SYSTEM_ONE_ROUTER_VERIFIER"], sys.executable)
        self.assertNotIn("SYSTEM_ONE_ROUTER_VERSION", environment)

    def test_update_pins_a_version_with_or_without_v(self):
        for target in ("0.2.0", "v0.2.0"):
            with self.subTest(target=target):
                _, call = self.run_update(["update", target])
                self.assertEqual(call.kwargs["env"]["SYSTEM_ONE_ROUTER_VERSION"], "v0.2.0")

    def test_verify_signature_reports_status(self):
        key = (FIXTURES / "key.pub").read_text().splitlines()[1]
        signature = str(FIXTURES / "checksums.txt.minisig")
        self.assertEqual(cli.main(["update", "--verify-signature", key, str(FIXTURES / "checksums.txt"), signature]), 0)
        with contextlib.redirect_stderr(io.StringIO()) as error:
            status = cli.main(["update", "--verify-signature", key, str(FIXTURES / "key.pub"), signature])
        self.assertEqual(status, 1)
        self.assertIn("signature verification failed", error.getvalue())

    def test_unknown_command_or_extra_arguments_print_usage(self):
        for argv in ([], ["bogus"], ["status", "extra"], ["update", "a", "b"], ["update", "--verify-signature"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(cli.main(argv), 2)
            self.assertIn("s1r update", error.getvalue())


if __name__ == "__main__":
    unittest.main()
