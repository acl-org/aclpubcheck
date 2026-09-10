from pathlib import Path
import subprocess
import sysconfig
import tempfile
import unittest


class CLITest(unittest.TestCase):
    def test_help(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [str(Path(sysconfig.get_path("scripts")) / "aclpubcheck"), "--help"],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("usage:", result.stdout)
        self.assertIn("--paper_type", result.stdout)


if __name__ == "__main__":
    unittest.main()
