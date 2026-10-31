"""Compare the existing analyzer's minimized asset-reference inventory.

This module accepts analyzer reports, not raw captures or arbitrary URLs. It
does not collect additional fields or expand the analyzer's privacy boundary.
"""
from __future__ import annotations

import re


FIXED_NOTE = (
    "Only retained normalized script, stylesheet, link and embedded-document "
    "references are compared. Origins are discarded, so domain-only swaps and "
    "aliased path renames can be invisible. Inline scripts and dynamic loaders "
    "are not inspected for asset changes. A reference absent from this selection "
    "is not proven deleted. Unchanged counts do not establish complete asset "
    "coverage, unchanged contents or security."
)
_KINDS = frozenset({"script", "stylesheet", "link", "embedded-document"})
_SOURCE = re.compile(r"source_[1-6]\Z")
_LOCATION = re.compile(r"line [1-9][0-9]*\Z")


def _index(report):
    assets = report.get("assets", [])
    if type(assets) is not list or len(assets) > 200:
        raise ValueError("Invalid normalized asset report.")
    result = {}
    for row in assets:
        if (type(row) is not dict or row.get("kind") not in _KINDS
                or type(row.get("path")) is not str
                or type(row.get("evidence")) is not list
                or len(row["evidence"]) > 16):
            raise ValueError("Invalid normalized asset report.")
        evidence = []
        for item in row["evidence"]:
            if (type(item) is not dict or type(item.get("source")) is not str
                    or type(item.get("location")) is not str
                    or not _SOURCE.fullmatch(item["source"])
                    or not _LOCATION.fullmatch(item["location"])):
                raise ValueError("Invalid normalized asset report.")
            evidence.append({"source": item["source"], "location": item["location"]})
        # Keep only the analyzer's existing normalized identity and generic
        # evidence. Future report metadata must not be copied implicitly.
        result[(row["kind"], row["path"])] = {
            "kind": row["kind"], "path": row["path"], "evidence": evidence}
    return result


def compare_assets(before_report, after_report):
    """Return added/absent retained references, never a domain/security verdict."""
    before, after = _index(before_report), _index(after_report)
    return {
        "added": [after[key] for key in sorted(after.keys() - before.keys())],
        "removed": [before[key] for key in sorted(before.keys() - after.keys())],
        "unchanged_count": len(before.keys() & after.keys()),
        "coverage": "normalized_asset_references_only",
        "scope": FIXED_NOTE,
    }
