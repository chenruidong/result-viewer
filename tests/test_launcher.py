import json
import os
import signal
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path

from result_viewer import electro_gui

FAKE_MAIN = '''
import http.server, json, sys
args = sys.argv[1:]
json.dump(args, open("argv.json", "w"))
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")
    def log_message(self, *a):
        pass
http.server.HTTPServer(("127.0.0.1", int(args[args.index("--port") + 1])), H).serve_forever()
'''


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "analysis_imitation"
        (self.repo / "electro_gui").mkdir(parents=True)
        (self.repo / "electro_gui" / "__init__.py").write_text("")
        (self.repo / "electro_gui" / "__main__.py").write_text(FAKE_MAIN)
        self.url = f"http://127.0.0.1:{free_port()}"
        self.launcher = electro_gui.Launcher(self.url, Path(self.tmp.name) / "configs", self.repo, sys.executable,
                                             Path(self.tmp.name) / "electro_gui.log")
        self.pid = None

    def tearDown(self):
        if self.pid:
            try:
                os.killpg(self.pid, signal.SIGTERM)
            except OSError:
                pass
        self.tmp.cleanup()

    def test_starts_once_with_the_viewer_config_dir(self):
        first = self.launcher.start()
        self.assertTrue(first["started"], first)
        self.pid = first["pid"]
        again = self.launcher.start()  # still coming up, or already answering: never a second process
        self.assertFalse(again["started"])
        deadline = time.time() + 15
        while not electro_gui.probe(self.url, timeout=0.5) and time.time() < deadline:
            time.sleep(0.1)
        self.assertTrue(electro_gui.probe(self.url))
        self.assertEqual(self.launcher.start(), {"started": False, "running": True})
        args = json.loads((self.repo / "argv.json").read_text())
        self.assertEqual(args[:3], ["--no-browser", "--port", self.url.rsplit(":", 1)[1]])
        self.assertEqual(args[3:], ["--config-dir", "output/presentation", "--config-dir", str(Path(self.tmp.name) / "configs")])
        self.assertIn("started by result-viewer", (Path(self.tmp.name) / "electro_gui.log").read_text())
        self.assertTrue(self.launcher.state()["starting"])  # the process is alive

    def test_problems(self):
        self.assertIsNone(self.launcher.problem())
        self.assertIn("not on this Mac", electro_gui.Launcher("http://100.1.2.3:8777", "/x", self.repo, sys.executable).problem())
        self.assertIn("no electro_gui URL", electro_gui.Launcher("", "/x", self.repo, sys.executable).problem())
        self.assertIn("no electro_gui package", electro_gui.Launcher(self.url, "/x", self.repo / "nope", sys.executable).problem())
        self.assertEqual(electro_gui.Launcher(self.url, "/x", Path(self.tmp.name) / "nope", sys.executable).start()["started"], False)


if __name__ == "__main__":
    unittest.main()
