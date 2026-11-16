"""Regression: first local launch must not block for an optional email."""

import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


class FirstLaunchTests(unittest.TestCase):
    def test_interactive_first_launch_reaches_health_without_stdin(self):
        root = Path(__file__).resolve().parents[1]
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        # Simulate a new user with no Streamlit credentials. Keep non-headless
        # mode (as on Windows) and never submit text or Enter on the stdin pipe.
        # Avoid opening a real browser from a background automated test.
        script = """
import sys
from unittest.mock import patch
from streamlit.web import cli
with patch('streamlit.runtime.credentials._check_credential_file_exists', return_value=False), \\
     patch('streamlit.cli_util.open_browser'):
    cli.main(args=['run', 'app.py', '--server.port', sys.argv[1],
                   '--server.headless', 'false'])
"""
        process = subprocess.Popen([sys.executable, "-c", script, str(port)], cwd=root,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8")
        ready = False
        output = ""
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    with urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1) as response:
                        ready = response.status == 200 and response.read() == b"ok"
                    if ready:
                        break
                except (URLError, TimeoutError, OSError):
                    pass
                time.sleep(0.1)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                output, _ = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate(timeout=5)
        self.assertTrue(ready, f"First launch did not reach readiness:\n{output}")
        self.assertNotIn("Email:", output)
        self.assertNotIn("helpful onboarding emails", output)


if __name__ == "__main__":
    unittest.main()
