"""
Regression tests for the path-confinement and local-origin fixes.

Each test here corresponds to something that was exploitable before: an
unauthenticated read of the whole filesystem, a traversal on the static
route, a home-wide tool sandbox, and an API that answered to any Host.
"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from llampaca.security import confine_path, is_sensitive_path, PathNotAllowed
from llampaca.tools.filesystem import (
    set_workspace_root,
    read_file,
    write_file,
    find_files,
    search_text,
    delete_path,
)

# The API pins Host to the loopback interface, so the test client must present
# a loopback base_url — TestClient's default is "http://testserver".
BASE_URL = "http://127.0.0.1:8000"


class TestConfinePath(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="llampaca_confine_"))

    def test_allows_path_inside_root(self):
        target = self.root / "sub" / "file.txt"
        target.parent.mkdir(parents=True)
        target.write_text("ok")
        self.assertEqual(confine_path(target, [self.root]), target.resolve())

    def test_rejects_traversal(self):
        with self.assertRaises(PathNotAllowed):
            confine_path(self.root / ".." / ".." / "etc" / "passwd", [self.root])

    def test_rejects_absolute_outside(self):
        with self.assertRaises(PathNotAllowed):
            confine_path("/etc/passwd", [self.root])

    def test_rejects_symlink_escaping_root(self):
        """resolve() follows symlinks, so a link planted inside the root
        cannot be used to reach outside it."""
        link = self.root / "escape"
        link.symlink_to("/etc/passwd")
        with self.assertRaises(PathNotAllowed):
            confine_path(link, [self.root])

    def test_sensitive_paths_denied_even_inside_root(self):
        """The deny list applies even when the root legitimately contains the
        path — the case where the user picks $HOME as their workspace."""
        home = Path.home()
        self.assertTrue(is_sensitive_path((home / ".ssh" / "id_rsa").resolve()))
        self.assertTrue(is_sensitive_path((home / ".aws" / "credentials").resolve()))
        self.assertTrue(
            is_sensitive_path((home / ".llampaca" / "mcp_config.json").resolve())
        )
        with self.assertRaises(PathNotAllowed):
            confine_path(home / ".ssh" / "id_rsa", [home])

    def test_ordinary_home_paths_still_allowed(self):
        home = Path.home()
        self.assertFalse(is_sensitive_path((home / "Documents").resolve()))


class TestFilesystemSandbox(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="llampaca_ws_"))
        set_workspace_root(self.ws)
        (self.ws / "note.txt").write_text("ciao mondo\n")

    def test_reads_inside_workspace(self):
        self.assertIn("ciao mondo", read_file("note.txt"))

    def test_rejects_read_outside_workspace(self):
        for bad in ("/etc/passwd", "../../etc/passwd", "~/.ssh/id_rsa"):
            with self.subTest(path=bad), self.assertRaises(PermissionError):
                read_file(bad)

    def test_rejects_write_outside_workspace(self):
        with self.assertRaises(PermissionError):
            write_file("~/.ssh/authorized_keys", "pwn")

    def test_search_tools_are_callable(self):
        """find_files, search_text and delete_path referenced an undefined
        WORKSPACE_ROOT global and raised NameError on every call."""
        (self.ws / "a.py").write_text("needle here\n")
        self.assertIn("a.py", find_files("*.py"))
        self.assertIn("needle", search_text("needle"))
        (self.ws / "throwaway.txt").write_text("x")
        self.assertIn("Deleted", delete_path("throwaway.txt"))

    def test_refuses_to_delete_workspace_root(self):
        self.assertIn("refusing", delete_path(str(self.ws)))


class TestApiPathConfinement(unittest.TestCase):
    def setUp(self):
        from llampaca.gui.routes import app

        self.client = TestClient(app, base_url=BASE_URL)

    def test_media_cannot_read_arbitrary_files(self):
        """GET /api/media/etc/passwd used to return the file (abs_path = "/" + path)."""
        self.assertEqual(self.client.get("/api/media/etc/passwd").status_code, 404)
        self.assertEqual(
            self.client.get("/api/media", params={"path": "/etc/passwd"}).status_code, 404
        )

    def test_media_cannot_read_credentials(self):
        r = self.client.get(
            "/api/media", params={"path": str(Path.home() / ".ssh" / "id_rsa")}
        )
        self.assertEqual(r.status_code, 404)

    def test_static_route_rejects_traversal(self):
        """The catch-all joined the raw path onto the GUI dir; uvicorn does not
        normalize, so '..' segments escaped. It must fall back to the SPA index
        rather than serve the target."""
        r = self.client.get("/../../../../../../../../etc/passwd")
        self.assertNotIn("root:", r.text)

    def test_static_route_still_serves_assets(self):
        self.assertEqual(self.client.get("/index.html").status_code, 200)
        self.assertEqual(self.client.get("/app.js").status_code, 200)

    def test_open_file_rejects_paths_outside_media_roots(self):
        r = self.client.post("/api/open-file", json={"path": "/etc/hosts"})
        self.assertEqual(r.status_code, 403)


class TestLocalOriginEnforcement(unittest.TestCase):
    def setUp(self):
        from llampaca.gui.routes import app

        self.client = TestClient(app, base_url=BASE_URL)

    def test_rejects_foreign_host_header(self):
        """DNS rebinding: the browser sends the attacker's domain in Host."""
        r = self.client.get("/api/settings", headers={"Host": "evil.attacker.com"})
        self.assertEqual(r.status_code, 403)

    def test_rejects_cross_site_origin(self):
        r = self.client.get("/api/settings", headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_rejects_null_origin(self):
        r = self.client.get("/api/settings", headers={"Origin": "null"})
        self.assertEqual(r.status_code, 403)

    def test_rejects_cross_site_simple_post(self):
        """post_message parses the body with request.json(), so a text/plain
        POST is a 'simple' request and escaped the CORS preflight."""
        r = self.client.post(
            "/api/conversations/whatever/messages",
            headers={"Content-Type": "text/plain", "Origin": "https://evil.example"},
            content='{"role":"user","content":"hi"}',
        )
        self.assertEqual(r.status_code, 403)

    def test_allows_loopback(self):
        self.assertEqual(self.client.get("/api/settings").status_code, 200)
        self.assertEqual(
            self.client.get(
                "/api/settings", headers={"Origin": "http://127.0.0.1:8000"}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/settings", headers={"Host": "localhost:8000"}).status_code,
            200,
        )


if __name__ == "__main__":
    unittest.main()
