import unittest

from surface_map.analyze import safe_path
from surface_map.source import source_inventory


class PathBoundaryTests(unittest.TestCase):
    def test_browser_backslash_urls_are_not_reported_as_literal_routes(self):
        for value in ("/api\\users", "/\\elsewhere.invalid/api", "https://example.invalid\\api/users"):
            self.assertIsNone(safe_path(value))

    def test_percent_encoded_separators_do_not_invent_route_segments(self):
        self.assertEqual(safe_path("/api%2Fusers"), ("/{segment_1}", []))
        self.assertEqual(safe_path("/api/customer%2f123"), ("/api/{segment_2}", []))
        self.assertEqual(safe_path("/api/%63ustomers"), ("/api/customers", []))
        self.assertIsNone(safe_path("/api/%2e%2e/users"))

    def test_normal_expression_chains_do_not_hide_python_imports(self):
        for expression in (" + ".join(["'a'"] * 80), "value" + ".field" * 80):
            report = source_inventory([{"kind": "python", "content": "import requests\nresult = " + expression + "\n"}])
            self.assertEqual(report["sources"][0]["status"], "parsed")
            self.assertIn("requests", {item["name"] for item in report["dependencies"]})
