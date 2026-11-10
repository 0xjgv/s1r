import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from system_one_router import cli


def installer(status=0):
    def run(command, env=None):
        run.script = Path(command[1]).read_text()
        run.command, run.env = command, env
        return subprocess.CompletedProcess(command, status)
    return run


class UpdateTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("SYSTEM_ONE_ROUTER_DOWNLOAD_BASE", None)

    def fetch(self, argv):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"echo installer\n"
        run = installer(3)
        with patch("system_one_router.cli.urllib.request.urlopen", return_value=response) as get, \
                patch("system_one_router.cli.subprocess.run", side_effect=run):
            status = cli.main(argv)
        return status, get.call_args[0][0], run

    def test_update_runs_latest_installer_and_returns_its_status(self):
        status, url, run = self.fetch(["update"])
        self.assertEqual(status, 3)
        self.assertEqual(url, cli.RELEASES + "/latest/download/install.sh")
        self.assertEqual((run.command[0], run.script, run.env), ("sh", "echo installer\n", None))

    def test_update_pins_a_version_with_or_without_v(self):
        for target in ("0.2.0", "v0.2.0"):
            with self.subTest(target=target):
                _, url, run = self.fetch(["update", target])
                self.assertEqual(url, cli.RELEASES + "/download/v0.2.0/install.sh")
                self.assertEqual(run.env["SYSTEM_ONE_ROUTER_VERSION"], "v0.2.0")

    def test_update_from_a_local_release_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "install.sh").write_text("echo local\n")
            os.environ["SYSTEM_ONE_ROUTER_DOWNLOAD_BASE"] = directory
            run = installer()
            with patch("system_one_router.cli.subprocess.run", side_effect=run):
                self.assertEqual(cli.main(["update"]), 0)
        self.assertEqual(run.script, "echo local\n")

    def test_update_reports_fetch_failure(self):
        error = io.StringIO()
        with patch("system_one_router.cli.urllib.request.urlopen", side_effect=OSError("offline")), \
                patch("system_one_router.cli.subprocess.run") as run, contextlib.redirect_stderr(error):
            self.assertEqual(cli.main(["update"]), 1)
        run.assert_not_called()
        self.assertIn("cannot fetch install.sh", error.getvalue())

    def test_unknown_command_or_extra_arguments_print_usage(self):
        for argv in ([], ["bogus"], ["status", "extra"], ["update", "a", "b"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(cli.main(argv), 2)
            self.assertIn("s1r update", error.getvalue())


if __name__ == "__main__":
    unittest.main()
