"""First-run CLI feedback without broadening error disclosures."""
import contextlib
import io
import socket
import unittest
from unittest.mock import Mock, patch

from surface_map.__main__ import main


class CLIOnboardingTests(unittest.TestCase):
    def test_real_occupied_port_gives_recovery_command_without_opening_browser(self):
        output, errors = io.StringIO(), io.StringIO()
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            with patch("surface_map.__main__.webbrowser.open") as browser, \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = main(["--open", "--port", str(occupied.getsockname()[1])])
        self.assertEqual(code, 2)
        browser.assert_not_called()
        self.assertEqual(output.getvalue(), "")
        self.assertIn("Choose an unused port", errors.getvalue())
        self.assertIn("python -m surface_map --port", errors.getvalue())
        self.assertNotIn("Invalid command line", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_invalid_port_keeps_generic_private_argument_errors(self):
        canary = "PRIVATE_PORT_ARGUMENT_CANARY"
        for value in ("0", "65536", canary):
            with self.subTest(kind="private" if value == canary else "out_of_range"):
                errors = io.StringIO()
                with patch("surface_map.__main__.make_server") as bind, \
                        patch("surface_map.__main__.webbrowser.open") as browser, \
                        contextlib.redirect_stderr(errors), self.assertRaises(SystemExit) as stopped:
                    main(["--open", "--port", value])
                self.assertEqual(stopped.exception.code, 2)
                bind.assert_not_called()
                browser.assert_not_called()
                self.assertIn("Invalid command line", errors.getvalue())
                self.assertNotIn(canary, errors.getvalue())

    def test_startup_explains_network_choice_and_survives_private_browser_failure(self):
        canary = "PRIVATE_BROWSER_EXCEPTION_CANARY"
        server = Mock()
        server.serve_forever.side_effect = KeyboardInterrupt
        output, errors = io.StringIO(), io.StringIO()
        with patch("surface_map.__main__.make_server", return_value=server), \
                patch("surface_map.__main__.webbrowser.open", side_effect=OSError(canary)) as browser, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(["--open", "--port", "9109"])
        self.assertEqual(code, 0)
        browser.assert_called_once_with("http://127.0.0.1:9109", new=2)
        server.serve_forever.assert_called_once_with(poll_interval=0.2)
        server.server_close.assert_called_once_with()
        self.assertEqual(errors.getvalue(), "")
        self.assertIn("Saved-file analysis stays offline", output.getvalue())
        self.assertIn("Public HTTPS mode fetches", output.getvalue())
        self.assertIn("when requested", output.getvalue())
        self.assertIn("Open http://127.0.0.1:9109 yourself", output.getvalue())
        self.assertNotIn("Offline artifact analysis only", output.getvalue())
        self.assertNotIn(canary, output.getvalue())


if __name__ == "__main__":
    unittest.main()
