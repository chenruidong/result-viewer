import contextlib
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from result_viewer import cli, server
from result_viewer.accesslog import ENV_DIR, AccessLog, attach
from result_viewer.catalog import Catalog
from result_viewer.notebook import Notebooks
from result_viewer.server import ROUTES, App, Tailnet, make_handler, make_server
from tests.helpers import SID, make_session

OWNER = "me@example.com"
NODE = "node.tail0000.ts.net"


class FixedTailnet(Tailnet):
    def _read(self):  # never call the real tailscale in tests
        return None


class LogTest(unittest.TestCase):
    """Every log here goes to a temporary TAILNET_ACCESS_LOG_DIR, never ~/Library/Logs/tailnet-access."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.logs = self.base / "logs"
        self.logs.mkdir()
        env = mock.patch.dict(os.environ, {ENV_DIR: str(self.logs)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)
        make_session(self.base / "results")

    def app(self):
        data = self.base / "data"
        data.mkdir(exist_ok=True)
        return App(Catalog([self.base / "results"]), Notebooks(data), data, data / "electro_gui",
                   FixedTailnet(OWNER, [NODE]), electro_gui_url="http://127.0.0.1:9",
                   electro_gui_repo=self.base / "no-electrogui")  # never start the real electro_gui here


class RoutesTest(unittest.TestCase):
    def test_every_served_path_has_its_class(self):
        log = AccessLog("result-viewer", routes=ROUTES, enabled=False)
        expect = {"/": "page", "/index.html": "page", "/static/eg-start.html": "page.eg-start",
                  "/static/app.js": "static", "/favicon.ico": "icon", "/apple-touch-icon.png": "icon",
                  "/apple-touch-icon-precomposed.png": "icon", f"/fig/0/{SID}/analysis/x.png": "fig",
                  "/api/health": "health", "/api/config": "api.config", "/api/sessions": "api.sessions",
                  f"/api/session?sid={SID}": "api.session", "/api/note": "api.note",
                  f"/api/notebook?sid={SID}": "api.notebook", "/api/electro_gui": "api.electro_gui",
                  "/api/electro_gui/start": "api.electro_gui.start", "/api/nope": "api", "/nope": "other"}
        self.assertEqual({path: log.route_class(path) for path in expect}, expect)


class AttachTest(LogTest):
    def serve(self, port, bind=False):
        """Run ``cmd_serve`` with a server that pretends to be bound to ``port`` (or, with ``bind``, a real
        one on a free port) and stops at once; returns its handler class."""
        made = []

        def fake_make_server(app, host, port):
            if bind:
                srv = make_server(app, host, 0)
                srv.serve_forever = lambda: None
            else:
                srv = mock.Mock(server_address=(host, port), RequestHandlerClass=make_handler(app))
            made.append(srv)
            return srv

        argv = ["serve", "--no-browser", "--port", str(port), "--root", str(self.base / "results"),
                "--data-dir", str(self.base / "data"), "--apps-dashboard-url", ""]
        with mock.patch.object(server, "make_server", fake_make_server), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(argv), 0)
        made[0].server_close()
        return made[0].RequestHandlerClass

    def test_attached_on_the_registered_ports_only(self):
        # The dashboard registry's port for result-viewer: 8766 on the iMac (also the default), 8767 on the Mac mini.
        self.assertEqual((cli.DEFAULT_PORT, cli.ACCESS_LOG_PORTS), (8766, (8766, 8767)))
        for port in cli.ACCESS_LOG_PORTS:
            with self.subTest(port=port):
                log = getattr(self.serve(port), "_access_log", None)
                self.assertIsInstance(log, AccessLog)
                self.assertEqual((log.app_id, log.root, log.create), ("result-viewer", self.logs, False))
                self.assertEqual(log.route_class("/api/notebook"), "api.notebook")
        self.assertFalse(hasattr(self.serve(8768), "_access_log"))  # a dev instance's port
        self.assertFalse(hasattr(self.serve(0, bind=True), "_access_log"))  # bound to some free port
        self.assertEqual(list(self.logs.iterdir()), [])  # attaching writes nothing

    def test_mac_mini_port_still_answers_and_logs(self):
        srv = server.Server(("127.0.0.1", 0), self.serve(8767))  # the handler cmd_serve gave the log, on a free port
        srv.daemon_threads = False  # so server_close joins the request's thread, which writes its line after replying
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        try:
            conn.request("GET", "/")
            resp = conn.getresponse()
            page = (resp.status, b"/static/app.js" in resp.read())
        finally:
            conn.close()
            srv.shutdown()
            srv.server_close()  # joins the request's thread (non-daemon above)
        self.assertEqual(page, (200, True))
        lines = [json.loads(line) for day in (self.logs / "result-viewer").glob("*.jsonl")
                 for line in day.read_text().splitlines()]
        self.assertEqual([(e["method"], e["route"], e["status"], e["via"]) for e in lines],
                         [("GET", "page", 200, "local")])

    def test_tests_servers_log_nothing(self):
        srv = make_server(self.app(), "127.0.0.1", 0)
        srv.server_close()
        self.assertFalse(hasattr(srv.RequestHandlerClass, "_access_log"))


class LoggedServerTest(LogTest):
    def test_requests_still_work_and_are_logged(self):
        srv = make_server(self.app(), "127.0.0.1", 0)
        log = AccessLog("result-viewer", routes=ROUTES)
        attach(srv.RequestHandlerClass, log)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

        def request(method, path, host=None, headers=None, body=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            hdrs = dict({"Host": host or f"127.0.0.1:{port}"}, **(headers or {}))
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read()
            conn.close()
            return resp.status, data

        status, page = request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"/static/app.js", page)
        status, data = request("GET", f"/api/notebook?sid={SID}", host=f"{NODE}:8766",
                               headers={"Tailscale-User-Login": OWNER, "X-Forwarded-For": "100.101.102.103"})
        self.assertEqual((status, json.loads(data)["notebook"]["session_id"]), (200, SID))
        self.assertEqual(request("GET", "/api/nope")[0], 404)
        self.assertEqual(request("POST", "/api/note", body=b"{}", headers={"Content-Type": "text/plain"})[0], 415)
        log.flush()
        days = list((self.logs / "result-viewer").glob("*.jsonl"))
        self.assertEqual(len(days), 1)
        text = days[0].read_text()
        events = [json.loads(line) for line in text.splitlines()]
        self.assertEqual([(e["method"], e["route"], e["status"], e["via"]) for e in events],
                         [("GET", "page", 200, "local"), ("GET", "api.notebook", 200, "serve"),
                          ("GET", "api", 404, "local"), ("POST", "api.note", 415, "local")])
        self.assertEqual((events[1]["login"], events[1]["ip"]), (OWNER, "100.101.102.103"))
        self.assertNotIn(SID, text)  # never the path or query
        self.assertTrue((self.logs / "result-viewer" / "status.json").is_file())


if __name__ == "__main__":
    unittest.main()
