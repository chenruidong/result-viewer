import http.server
import json
import os
import signal
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from result_viewer import electro_gui
from result_viewer.catalog import Catalog
from result_viewer.notebook import Notebooks
from result_viewer.server import App

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


def dash_app(id_, port, status="up", command=None, started="2026-10-09T20:07:21Z", host="127.0.0.1"):
    """One entry of the apps dashboard's /api/apps ``apps`` list (the fields read here)."""
    return {"id": id_, "status": status, "local": {"url": f"http://{host}:{port}/", "port": port},
            "process": {"command": command or f"/envs/imitation-phys/bin/python -m electro_gui --no-browser --port {port}",
                        "started": started}}


class FakeDashboard:
    """A loopback /api/apps that answers with ``self.apps``."""

    def __init__(self, apps):
        self.apps = apps
        self.calls = 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.calls += 1
                body = json.dumps({"apps": outer.apps}).encode() if self.path == "/api/apps" else b"{}"
                self.send_response(200 if self.path == "/api/apps" else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class DashboardTest(unittest.TestCase):
    def test_pick_running(self):
        pick = electro_gui.pick_running
        here = dash_app("electrogui", 8778)
        self.assertEqual(pick([here]), "http://127.0.0.1:8778")
        self.assertEqual(pick([dash_app("rv", 8766, command="python -m result_viewer serve"), here]), "http://127.0.0.1:8778")
        self.assertEqual(pick([dash_app("x", 8779, command="/bin/electro-gui --port 8779")]), "http://127.0.0.1:8779")
        self.assertEqual(pick([dash_app("electro_gui", 8765, command="python other.py")]), "http://127.0.0.1:8765")
        self.assertIsNone(pick([dash_app("electro_gui", 8765, status="idle")]))
        self.assertIsNone(pick([dash_app("electrogui", 8777, host="100.1.2.3")]))
        self.assertIsNone(pick([dash_app("x", 8780, command="python -m electro_gui_tools")]))
        self.assertIsNone(pick(None))
        self.assertIsNone(pick([]))
        older = dash_app("a", 8765, started="2026-10-01T00:00:00Z")
        newer = dash_app("b", 8778, started="2026-10-09T00:00:00Z")
        self.assertEqual(pick([older, newer]), "http://127.0.0.1:8778")  # most recently started
        self.assertEqual(pick([older, newer], prefer="http://127.0.0.1:8765"), "http://127.0.0.1:8765")  # configured port

    def test_from_dashboard(self):
        dash = FakeDashboard([dash_app("electrogui", 8778)])
        try:
            self.assertEqual(electro_gui.from_dashboard(dash.url), "http://127.0.0.1:8778")
        finally:
            dash.close()
        self.assertIsNone(electro_gui.from_dashboard(f"http://127.0.0.1:{free_port()}", timeout=1))  # nothing listening

    def test_viewer_uses_the_running_electro_gui_on_this_mac_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            dash = FakeDashboard([dash_app("electrogui", 8778)])
            try:
                def app(**kw):
                    return App(Catalog([]), Notebooks(tmp), tmp, Path(tmp) / "electro_gui", None, **kw)

                found = app(apps_dashboard_url=dash.url)
                self.assertEqual(found.electro_gui_url("local"), "http://127.0.0.1:8778")
                self.assertEqual(found.launcher().url, "http://127.0.0.1:8778")
                self.assertEqual(found.electro_gui_url("tailnet"), "")  # remote_url: never a loopback URL
                self.assertEqual(dash.calls, 1)  # asked once, then reused
                dash.apps = []
                self.assertEqual(found.electro_gui_url("local"), "http://127.0.0.1:8778")  # within 10 s
                found._found = (None, None, None)
                self.assertEqual(found.electro_gui_url("local"), "http://127.0.0.1:8765")  # none up: settings.json
                self.assertEqual(app(apps_dashboard_url=dash.url, electro_gui_url="http://127.0.0.1:9").electro_gui_url("local"),
                                 "http://127.0.0.1:9")
                self.assertEqual(app().electro_gui_url("local"), "http://127.0.0.1:8765")  # lookup off
                self.assertEqual(dash.calls, 2)
            finally:
                dash.close()


if __name__ == "__main__":
    unittest.main()
