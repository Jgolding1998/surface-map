"""Public-page orchestration shared by MCP and the optional local URL workflow.

Raw captures remain transient internal inputs. Only the minimized analyzer report
and its derived implementation plan are returned.
"""
from __future__ import annotations

import time

from .analyze import ArtifactError, analyze
from .rebuild import _plan_from_report
from .url_capture import MAX_URL_LENGTH, capture_public_url

ANALYSIS_SECONDS = 10.0
PLAN_SECONDS = 1.0
CLIENT_TIMEOUT_MS = 45000
PUBLIC_PLAN_SCOPE = (
    "A partial implementation plan from a public HTTPS page and selected same-origin scripts. "
    "The page and selected scripts were fetched for static inspection. Captured code was not "
    "executed; discovered forms and API operations were not replayed or validated. This does "
    "not reproduce an application or establish a complete contract. Navigation references "
    "do not become API operations. Import reviewed saved artifacts to generate mock files."
)
PUBLIC_PRIVACY_SCOPE = (
    "A public page and selected script resources were fetched for static analysis. "
    "Discovered form and API operations were not replayed or validated. Normalized paths "
    "may merge unrelated origins or identifiers; evidence is incomplete."
)
REFERENCE_OMISSION = (
    "String and module references were not inventoried for this public URL plan. "
    "An empty reference list does not establish absence. Use reviewed saved artifacts "
    "for a separate reference inventory."
)


def _validate_options(url, include_scripts, javascript_engine):
    if not isinstance(url, str) or not 1 <= len(url) <= MAX_URL_LENGTH:
        raise ArtifactError("Provide a public HTTPS URL within the supported length limit.")
    if type(include_scripts) is not bool:
        raise ArtifactError("Script capture selection must be a boolean.")
    if not isinstance(javascript_engine, str) or javascript_engine not in {"auto", "basic", "enhanced"}:
        raise ArtifactError("Unsupported JavaScript analysis engine.")


def analyze_public_capture(capture, *, javascript_engine="auto", include_page_signals=False,
                           deadline=None, analyzer=None):
    """Analyze a trusted transport result once, preserving capture omissions.

    ``capture`` and ``analyzer`` are private orchestration inputs, not HTTP/MCP
    arguments. The injectable analyzer preserves the MCP boundary's test seam.
    """
    if analyzer is None:
        analyzer = analyze
    end = time.monotonic() + ANALYSIS_SECONDS if deadline is None else deadline
    report = analyzer(capture["artifacts"], javascript_engine=javascript_engine,
                      document_url=capture["document_url"], deadline=end,
                      include_page_signals=include_page_signals, tolerate_script_errors=True)
    omissions = report["script_omissions"]
    warnings = list(capture["warnings"])
    if omissions:
        warnings.append("Some fetched scripts were omitted from analysis; supported HTML and script evidence was retained. Inspect each source status and script_omissions.")
    report["capture"] = {
        **capture["capture"],
        "scripts_analyzed": len(capture["artifacts"]) - 1 - len(omissions),
        "analysis_partial": bool(omissions),
    }
    report["capture_warnings"] = warnings
    report["privacy"]["scope"] = PUBLIC_PRIVACY_SCOPE
    return report


def inspect_public_url(url, *, include_scripts=True, javascript_engine="auto"):
    """Fetch once, analyze once, and derive a plan from that exact report."""
    _validate_options(url, include_scripts, javascript_engine)
    capture = capture_public_url(url, include_scripts=include_scripts)
    deadline = time.monotonic() + ANALYSIS_SECONDS
    report = analyze_public_capture(capture, javascript_engine=javascript_engine, deadline=deadline)
    # Do not scan omitted scripts with a weaker parser or renumber source IDs.
    # References are optional context; the report remains the sole operation source.
    references = {
        "format": "surface-map-references-v1", "sources": [],
        "sources_not_scanned": len(capture["artifacts"]),
        "candidates": [], "module_references": [],
        "limits": {"truncated": False, "inventory_performed": False},
        "scope": REFERENCE_OMISSION,
    }
    plan = _plan_from_report(report, references, deadline=time.monotonic() + PLAN_SECONDS, scope=PUBLIC_PLAN_SCOPE,
                             coverage_extra={"reference_inventory_omission": {
                                 "reason": "public_url_reference_inventory_not_performed",
                                 "source_count": len(capture["artifacts"]),
                                 "scope": REFERENCE_OMISSION,
                             }})
    return {"report": report, "plan": plan}
