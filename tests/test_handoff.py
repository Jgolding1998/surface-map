"""Exercise the browser renderer with actual backend plans, without a browser SDK."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from surface_map.rebuild import plan_rebuild


NODE = os.environ.get("SURFACE_MAP_NODE") or shutil.which("node")
ROOT = Path(__file__).resolve().parents[1]
RENDER = """
const fs = require('node:fs');
const handoff = require(process.argv[1]);
const plan = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify({brief:handoff.markdown(plan),
  simple:handoff.explain(plan, 'simple'), developer:handoff.explain(plan, 'developer'),
  genz:handoff.explain(plan, 'genz')}));
"""


@unittest.skipUnless(NODE, "Node is optional for local Python-only analysis")
class RealPlanHandoffTests(unittest.TestCase):
    def render(self, plan):
        result = subprocess.run([NODE, "-e", RENDER, str(ROOT / "surface_map/static/handoff.js")],
                                input=json.dumps(plan), capture_output=True, text=True,
                                encoding="utf-8", timeout=10, check=True)
        return json.loads(result.stdout)

    def test_mixed_real_plan_keeps_exact_paths_evidence_and_basic_limitations(self):
        canary = "PRIVATE_HANDOFF_CANARY_6251"
        capture = {"log": {"entries": [{
            "request": {"method": "GET", "url": f"https://example.invalid/api/secret-route/51?token={canary}"},
            "response": {"status": 200, "content": {"mimeType": "application/json", "text": json.dumps({"name": canary})}},
        }]}}
        plan = plan_rebuild([
            {"kind": "har", "text": json.dumps(capture)},
            {"kind": "html", "text": '<a href="/dashboard">Home</a><form method="post" action="/api/orders"></form>'},
            {"kind": "javascript", "text": 'fetch("/api/items"); function ( {'},
        ], javascript_engine="basic")
        self.assertNotIn("status", plan["coverage"]["sources"][0])
        self.assertEqual(plan["coverage"]["javascript_analysis"][0]["engine"], "basic")
        rendered = self.render(plan)
        for row in plan["operations"]:
            self.assertIn(row["path"], rendered["brief"])
            self.assertIn(row["method"], rendered["brief"])
            self.assertIn(row["confidence"], rendered["brief"])
            for evidence in row["evidence"]:
                self.assertIn(evidence["source"], rendered["brief"])
        self.assertIn("/api/{segment_2}/{integer_3}", rendered["brief"])
        self.assertIn("## Navigation references", rendered["brief"])
        for key in ("simple", "developer", "genz", "brief"):
            self.assertIn("syntax and scope are not validated", rendered[key])
            self.assertNotIn(canary, rendered[key])
            self.assertNotIn("example.invalid", rendered[key])
        facts = lambda text: text.split("\n\n", 1)[1]
        self.assertEqual(facts(rendered["simple"]), facts(rendered["developer"]))
        self.assertEqual(facts(rendered["simple"]), facts(rendered["genz"]))

    def test_actual_plan_and_brief_limits_remain_distinct(self):
        names = ("users", "orders", "items", "cart", "checkout", "reports", "events", "projects")
        html = "".join(f'<form method="post" action="/api/{first}/{second}"></form>'
                       for first in names for second in names)
        plan = plan_rebuild([{"kind": "html", "text": html}], javascript_engine="basic")
        self.assertEqual(len(plan["operations"]), 50)
        self.assertEqual(plan["coverage"]["retained_operation_groups"], 64)
        self.assertEqual(plan["coverage"]["omitted_operations"], 14)
        rendered = self.render(plan)
        self.assertIn("14 operation groups", rendered["simple"])
        self.assertIn("Showing 20 of 50 retained rows", rendered["brief"])
        self.assertIn("30 retained rows are omitted", rendered["brief"])
        metadata = rendered["brief"].split("````json\n", 1)[1].split("\n````", 1)[0]
        self.assertEqual(json.loads(metadata)["coverage"], plan["coverage"])


if __name__ == "__main__":
    unittest.main()
