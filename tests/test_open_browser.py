import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from surface_map.__main__ import main
from surface_map.rebuild import plan_rebuild


class OpenBrowserTests(unittest.TestCase):
    def run_server(self, arguments, *, opened=True, browser_error=None):
        server = Mock()
        server.serve_forever.side_effect = KeyboardInterrupt
        output = io.StringIO()
        with patch("surface_map.__main__.make_server", return_value=server) as bind, \
                patch("surface_map.__main__.webbrowser.open", return_value=opened,
                      side_effect=browser_error) as browser, contextlib.redirect_stdout(output):
            self.assertEqual(main(arguments), 0)
        server.serve_forever.assert_called_once_with(poll_interval=0.2)
        server.server_close.assert_called_once_with()
        return bind, browser, output.getvalue()

    def test_opt_in_opens_only_bound_loopback_workspace(self):
        server = Mock()
        events = []
        server.serve_forever.side_effect = KeyboardInterrupt
        with patch("surface_map.__main__.make_server", side_effect=lambda port: events.append(("bind", port)) or server), \
                patch("surface_map.__main__.webbrowser.open", side_effect=lambda url, new: events.append(("browser", url, new)) or True), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--open", "--port", "9107"]), 0)
        self.assertEqual(events, [("bind", 9107), ("browser", "http://127.0.0.1:9107", 2)])
        server.server_close.assert_called_once_with()

    def test_browser_is_not_opened_without_opt_in(self):
        bind, browser, output = self.run_server([])
        bind.assert_called_once_with(8768)
        browser.assert_not_called()
        self.assertIn("http://127.0.0.1:8768", output)

    def test_browser_refusal_keeps_server_running_and_prints_manual_url(self):
        _, browser, output = self.run_server(["--open", "--port", "9108"], opened=False)
        browser.assert_called_once_with("http://127.0.0.1:9108", new=2)
        self.assertIn("Open http://127.0.0.1:9108 yourself.", output)

    def test_browser_exception_is_private_and_keeps_server_running(self):
        _, _, output = self.run_server(["--open"], browser_error=RuntimeError("PRIVATE_BROWSER_CANARY"))
        self.assertIn("Browser could not open automatically.", output)
        self.assertNotIn("PRIVATE_BROWSER_CANARY", output)
        self.assertNotIn("Traceback", output)

    def test_help_does_not_start_server_or_open_browser(self):
        with patch("surface_map.__main__.make_server") as bind, \
                patch("surface_map.__main__.webbrowser.open") as browser, \
                contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as caught:
            main(["--open", "--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("--open", output.getvalue())
        bind.assert_not_called()
        browser.assert_not_called()

    def test_failed_bind_does_not_open_browser_or_expose_exception(self):
        with patch("surface_map.__main__.make_server", side_effect=OSError("PRIVATE_BIND_CANARY")), \
                patch("surface_map.__main__.webbrowser.open") as browser, \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            code = main(["--open"])
        self.assertEqual(code, 2)
        browser.assert_not_called()
        self.assertNotIn("PRIVATE_BIND_CANARY", errors.getvalue())

    def test_invalid_port_does_not_bind_or_open_browser(self):
        with patch("surface_map.__main__.make_server") as bind, \
                patch("surface_map.__main__.webbrowser.open") as browser, \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["--open", "--port", "0"])
        self.assertEqual(caught.exception.code, 2)
        bind.assert_not_called()
        browser.assert_not_called()

    def test_subcommand_does_not_open_browser_even_when_flag_is_supplied(self):
        with patch("surface_map.__main__.make_server") as bind, \
                patch("surface_map.__main__.webbrowser.open") as browser, \
                patch("surface_map.__main__.selected_sources", return_value=[]), \
                patch("surface_map.__main__.inspect_sources", return_value={"sources": []}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--open", "inspect", "--files", "requirements.txt"]), 0)
        self.assertEqual(output.getvalue().strip(), '{"sources":[]}')
        bind.assert_not_called()
        browser.assert_not_called()


class QuickstartExampleTests(unittest.TestCase):
    def test_shipped_example_matches_the_documented_basic_trial(self):
        root = Path(__file__).resolve().parents[1] / "examples" / "quickstart"
        artifacts = [
            {"kind": kind, "text": (root / name).read_text(encoding="utf-8")}
            for name, kind in (("page.html", "html"), ("client.js", "javascript"), ("orders.har", "har"))
        ]
        with patch("socket.create_connection", side_effect=AssertionError("Offline example only")):
            plan = plan_rebuild(artifacts, javascript_engine="basic")
        self.assertEqual({(row["method"], row["path"], row["confidence"]) for row in plan["operations"]}, {
            ("GET", "/api/items", "inferred"),
            ("GET", "/api/orders", "observed"),
            ("POST", "/api/orders", "declared"),
        })
        self.assertEqual([(row["path"], row["confidence"]) for row in plan["pages"]], [("/dashboard", "declared")])
        self.assertEqual(plan["coverage"]["warnings"], [])
        self.assertEqual(plan["unresolved"], [])
        self.assertNotIn("example.invalid", json.dumps(plan))
        self.assertTrue(all(row["stub"] == "not_implemented" for row in plan["operations"]))


if __name__ == "__main__":
    unittest.main()
