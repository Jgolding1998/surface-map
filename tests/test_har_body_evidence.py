"""Bounded body outlines and observed form names, inspired by Craft's web_har.

The positive base64 and form cases are negative controls for the previous
shape-only implementation: each previously disappeared from the report.
"""

import base64
import json
import unittest
from unittest.mock import patch

import surface_map.analyze as analysis
from surface_map.analyze import ArtifactError, analyze, json_body
from surface_map.export import contract, openapi


CANARY = "BODY_PRIVATE_CANARY_703129"


def capture(post=None, content=None, *, entries=None):
    if entries is None:
        request = {"method": "POST", "url": "/api/orders?token=" + CANARY,
                   "headers": [{"name": "Authorization", "value": CANARY}],
                   "cookies": [{"name": CANARY, "value": CANARY}]}
        response = {"status": 200}
        if post is not None:
            request["postData"] = post
        if content is not None:
            response["content"] = content
        entries = [{"request": request, "response": response}]
    return [{"kind": "har", "text": json.dumps({"log": {"entries": entries}})}]


def inspect(post=None, content=None, **kwargs):
    return analyze(capture(post, content, **kwargs), javascript_engine="basic")


def body(report, direction="request"):
    return next(row for row in report["har_body_analysis"][0]["bodies"] if row["direction"] == direction)


def encoded(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class HarBodyEvidenceTests(unittest.TestCase):
    def test_base64_json_recovers_existing_shape_without_values(self):
        plain = {"mimeType": "application/problem+json; charset=utf-8",
                 "text": json.dumps({"id": 27, "password": CANARY, CANARY: [True, None]})}
        b64 = {**plain, "encoding": "base64", "text": encoded(plain["text"])}
        report = inspect(b64, b64)
        route = report["routes"][0]
        self.assertEqual(route["request_shapes"], [json_body(plain)])
        self.assertEqual(route["response_shapes"], [json_body(plain)])
        self.assertEqual(json_body(b64), json_body(plain))
        self.assertEqual(body(report)["state"], "inspected")
        self.assertEqual(body(report)["reason"], "json_shape")
        self.assertEqual(report["har_body_analysis"][0]["counts"]["json_shapes"], 2)
        for value in (report, contract(report), openapi(report)):
            rendered = json.dumps(value)
            self.assertNotIn(CANARY, rendered)
            self.assertNotIn(b64["text"], rendered)
            self.assertNotIn("Authorization", rendered)
        self.assertEqual(route["evidence"], [{"source": "source_1", "location": "entry 1", "kind": "har"}])

    def test_base64_never_sniffs_non_json_mime(self):
        for mime in ("text/plain", "application/vnd.private+json", "image/png", "", None):
            with self.subTest(mime=mime), patch.object(analysis.base64, "b64decode", side_effect=AssertionError("must not decode")):
                report = inspect(content={"mimeType": mime, "encoding": "base64", "text": encoded('{"id":1}')})
                self.assertEqual(report["routes"][0]["response_shapes"], [])
                self.assertEqual(body(report, "response")["reason"], "unsupported_mime")

    def test_base64_alphabet_padding_utf8_and_json_are_strict(self):
        cases = [
            ("not base64!", "invalid_base64"),
            (encoded('{"id":1}') + "\n", "invalid_base64"),
            ("____", "invalid_base64"),
            ("e30", "invalid_base64"),
            ("e30===", "invalid_base64"),
            ("e31=", "invalid_base64"),  # nonzero unused padding bits
            (base64.b64encode(b"\xff").decode("ascii"), "invalid_utf8"),
            (encoded("<html>" + CANARY), "invalid_json"),
            (encoded('{"id":1,"id":2}'), "invalid_json"),
            (encoded('{"id":NaN}'), "invalid_json"),
        ]
        for text, reason in cases:
            with self.subTest(reason=reason, text=text):
                report = inspect(content={"mimeType": "application/json", "encoding": "base64", "text": text})
                self.assertEqual(body(report, "response")["reason"], reason)
                self.assertEqual(report["routes"][0]["response_shapes"], [])
                self.assertNotIn(CANARY, json.dumps(report))

    def test_unknown_or_malformed_encoding_is_omitted(self):
        for encoding in ("gzip", "BASE64", False, 0, [], {}):
            with self.subTest(encoding=encoding):
                report = inspect({"mimeType": "application/json", "encoding": encoding, "text": '{"id":1}'})
                self.assertEqual(body(report)["reason"], "unsupported_encoding")
                self.assertEqual(report["routes"][0]["request_shapes"], [])

    def test_decoded_and_plain_byte_limits_are_exact(self):
        text = '{"name":"' + "x" * (analysis.MAX_HAR_BODY_BYTES - 11) + '"}'
        self.assertEqual(len(text.encode("utf-8")), analysis.MAX_HAR_BODY_BYTES)
        for use_base64 in (False, True):
            for extra in ("", " "):
                with self.subTest(use_base64=use_base64, extra=extra):
                    content = {"mimeType": "application/json", "text": encoded(text + extra) if use_base64 else text + extra}
                    if use_base64:
                        content["encoding"] = "base64"
                    report = inspect(content=content)
                    self.assertEqual(body(report, "response")["reason"], "body_too_large" if extra else "json_shape")
        report = inspect(content={"mimeType": "application/json", "text": '"' + "é" * 32768 + '"'})
        self.assertEqual(body(report, "response")["reason"], "body_too_large")

    def test_oversize_base64_is_rejected_before_decoding(self):
        with patch.object(analysis.base64, "b64decode", side_effect=AssertionError("oversize decode")):
            report = inspect(content={"mimeType": "application/json", "encoding": "base64", "text": "A" * 90000})
        self.assertEqual(body(report, "response")["reason"], "body_too_large")

    def test_json_depth_and_node_limits_remain_bounded(self):
        for text in ("[" * 13 + "0" + "]" * 13, json.dumps([[0] * 1000] * 5)):
            with self.subTest(length=len(text)):
                report = inspect(content={"mimeType": "application/json", "encoding": "base64", "text": encoded(text)})
                self.assertEqual(body(report, "response")["reason"], "json_structure_limit")
                self.assertEqual(report["routes"][0]["response_shapes"], [])

    def test_unavailable_text_and_absent_body_are_distinct_from_empty_json(self):
        report = inspect(content={"mimeType": "application/json"})
        self.assertEqual(body(report)["reason"], "not_present")
        self.assertEqual(body(report, "response")["reason"], "unavailable_text")
        for value, reason in (("", "invalid_json"), ("{}", "json_shape"), ("null", "json_shape"), (42, "invalid_text"), ("\ud800", "invalid_utf8")):
            with self.subTest(value=value):
                report = inspect(content={"mimeType": "application/json", "text": value})
                self.assertEqual(body(report, "response")["reason"], reason)
        report = inspect(content=[])
        self.assertEqual(body(report, "response")["reason"], "invalid_body")

    def test_urlencoded_names_duplicates_and_unknown_aliases_are_observed(self):
        post = {"mimeType": "application/x-www-form-urlencoded; charset=UTF-8",
                "text": "email=" + CANARY + "&email=second&%74oken=" + CANARY + "&" + CANARY + "=private&first+name=private"}
        report = inspect(post)
        route = report["routes"][0]
        self.assertEqual(route["request_shapes"], [])
        self.assertEqual(route["request_fields"], [{"source": "source_1", "location": "entry 1", "kind": "har", "format": "urlencoded",
            "fields": [{"name": "field_1", "occurrences": 1}, {"name": "email", "occurrences": 2},
                       {"name": "field_3", "occurrences": 1}, {"name": "token", "occurrences": 1}]}])
        self.assertEqual(route["confidence"], "observed")
        self.assertEqual(body(report)["reason"], "form_fields")
        self.assertNotIn(CANARY, json.dumps(report))
        self.assertNotIn("first name", json.dumps(report))

    def test_param_only_urlencoded_names_do_not_infer_value_types(self):
        post = {"mimeType": "application/x-www-form-urlencoded", "params": [
            {"name": "email", "value": CANARY}, {"name": "email", "value": "other"},
            {"name": "id", "value": "25"}, {"name": CANARY, "value": {"private": CANARY}}]}
        report = inspect(post)
        self.assertEqual(report["routes"][0]["request_fields"][0]["fields"], [
            {"name": "field_1", "occurrences": 1}, {"name": "email", "occurrences": 2}, {"name": "id", "occurrences": 1}])
        self.assertEqual(report["routes"][0]["request_shapes"], [])
        self.assertNotIn(CANARY, json.dumps(report))

    def test_matching_params_and_text_use_one_observation(self):
        post = {"mimeType": "application/x-www-form-urlencoded", "text": "email=first&email=second&id=1", "params": [
            {"name": "id", "value": CANARY}, {"name": "email", "value": CANARY}, {"name": "email", "value": CANARY}]}
        report = inspect(post)
        self.assertEqual(report["routes"][0]["request_fields"][0]["fields"], [
            {"name": "email", "occurrences": 2}, {"name": "id", "occurrences": 1}])
        self.assertEqual(report["har_body_analysis"][0]["counts"]["form_fields"], 1)

    def test_conflicting_or_malformed_form_representations_fail_closed(self):
        cases = [
            ({"text": "email=x", "params": [{"name": "token"}]}, "conflicting_form_representations"),
            ({"text": "email=x&email=y", "params": [{"name": "email"}]}, "conflicting_form_representations"),
            ({"text": "email=x", "params": []}, "conflicting_form_representations"),
            ({"text": "email=x", "params": {}}, "invalid_form_params"),
            ({"params": [{"value": CANARY}]}, "invalid_form_params"),
            ({"params": [{"name": None}]}, "invalid_form_params"),
            ({"text": "email=%GG"}, "invalid_form_text"),
            ({"text": "%FF=x"}, "invalid_form_text"),
            ({"text": "email=x", "encoding": "gzip"}, "unsupported_encoding"),
        ]
        for value, reason in cases:
            with self.subTest(reason=reason, value=value):
                report = inspect({"mimeType": "application/x-www-form-urlencoded", **value})
                self.assertEqual(body(report)["reason"], reason)
                self.assertNotIn("request_fields", report["routes"][0])
                self.assertNotIn(CANARY, json.dumps(report))

    def test_form_text_base64_and_empty_forms_are_supported(self):
        for post in ({"text": encoded("email=" + CANARY + "&email="), "encoding": "base64"},
                     {"text": ""}, {"params": []}):
            report = inspect({"mimeType": "application/x-www-form-urlencoded", **post})
            self.assertEqual(body(report)["state"], "inspected")
            self.assertIn("request_fields", report["routes"][0])
            self.assertNotIn(CANARY, json.dumps(report))

    def test_form_limits_reject_whole_observation_instead_of_inventing_partial_fields(self):
        cases = [
            ({"params": [{"name": "email"}] * 65}, "too_many_form_fields"),
            ({"params": [{"name": "email"}] * 1000}, "too_many_form_fields"),
            ({"text": "&".join("email=x" for _ in range(65))}, "too_many_form_fields"),
            ({"params": [{"name": "x" * 65537}]}, "body_too_large"),
            ({"params": [{"name": "\ud800"}]}, "invalid_form_params"),
            ({"text": "email=" + "x" * 65537}, "body_too_large"),
        ]
        for value, reason in cases:
            with self.subTest(reason=reason):
                report = inspect({"mimeType": "application/x-www-form-urlencoded", **value})
                self.assertEqual(body(report)["reason"], reason)
                self.assertNotIn("request_fields", report["routes"][0])

    def test_duplicate_count_boundary_and_case_alias_collisions_keep_distinct_names(self):
        report = inspect({"mimeType": "application/x-www-form-urlencoded", "params": [{"name": "email"}] * 64})
        self.assertEqual(report["routes"][0]["request_fields"][0]["fields"], [{"name": "email", "occurrences": 64}])
        report = inspect({"mimeType": "application/x-www-form-urlencoded", "text": "Email=x&email=y&email=z"})
        self.assertEqual(report["routes"][0]["request_fields"][0]["fields"], [
            {"name": "email", "occurrences": 1}, {"name": "email_2", "occurrences": 2}])

    def test_multipart_reads_only_param_names_and_records_raw_text_omission(self):
        post = {"mimeType": "multipart/form-data; boundary=" + CANARY, "encoding": "base64", "text": CANARY,
                "params": [{"name": "file", "fileName": CANARY + ".txt", "value": CANARY},
                           {"name": "file", "fileName": CANARY + ".bin", "value": CANARY},
                           {"name": "description", "value": CANARY}]}
        with patch.object(analysis.base64, "b64decode", side_effect=AssertionError("must not decode multipart")):
            report = inspect(post)
        self.assertEqual(report["routes"][0]["request_fields"][0]["fields"], [
            {"name": "description", "occurrences": 1}, {"name": "file", "occurrences": 2}])
        self.assertEqual(body(report)["text_omission"], "multipart_text_not_inspected")
        self.assertEqual(report["har_body_analysis"][0]["counts"]["text_omissions"], {"multipart_text_not_inspected": 1})
        self.assertNotIn(CANARY, json.dumps(report))

    def test_multipart_without_params_never_guesses_names_from_raw_upload(self):
        post = {"mimeType": "multipart/form-data", "text": '--boundary\r\nContent-Disposition: form-data; name="email"\r\n\r\n' + CANARY}
        report = inspect(post)
        self.assertEqual(body(report)["reason"], "multipart_params_unavailable")
        self.assertNotIn("request_fields", report["routes"][0])
        self.assertNotIn(CANARY, json.dumps(report))

    def test_body_record_retention_is_explicit_and_counters_cover_every_entry(self):
        entries = [{"request": {"method": "GET", "url": "/api/orders"}, "response": {"status": 200}} for _ in range(80)]
        report = inspect(entries=entries)
        coverage = report["har_body_analysis"][0]
        self.assertEqual(len(coverage["bodies"]), analysis.MAX_HAR_BODY_RECORDS)
        self.assertEqual(coverage["omitted_body_records"], 160 - analysis.MAX_HAR_BODY_RECORDS)
        self.assertTrue(coverage["truncated"])
        self.assertEqual(coverage["counts"]["bodies"], 160)
        self.assertEqual(coverage["counts"]["reasons"], {"not_present": 160})
        self.assertEqual(coverage["limits"]["body_records"], analysis.MAX_HAR_BODY_RECORDS)
        self.assertEqual(coverage["limits"]["body_bytes"], 65536)

    def test_request_field_retention_uses_existing_omission_reporting(self):
        entries = [{"request": {"method": "POST", "url": "/api/orders", "postData": {
            "mimeType": "application/x-www-form-urlencoded", "text": "email=x"}}, "response": {"status": 200}} for _ in range(70)]
        report = inspect(entries=entries)
        self.assertEqual(len(report["routes"][0]["request_fields"]), 64)
        omission = next(row for row in report["evidence_limits"]["omissions"] if row["category"] == "route_request_fields")
        self.assertEqual(omission["omitted_occurrences"], 6)

    def test_shared_deadline_is_observed_during_body_decoding(self):
        clock = [0.0]
        original = analysis.base64.b64decode

        def slow_decode(*args, **kwargs):
            result = original(*args, **kwargs)
            clock[0] = 2.0
            return result

        with patch.object(analysis.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(analysis.base64, "b64decode", side_effect=slow_decode):
            with self.assertRaises(ArtifactError) as caught:
                analyze(capture(content={"mimeType": "application/json", "encoding": "base64", "text": encoded('{"id":1}')}), deadline=1.0)
        self.assertIn("deadline", str(caught.exception))
        self.assertNotIn(CANARY, str(caught.exception))

    def test_area_href_uses_declared_link_privacy_and_provenance(self):
        report = analyze([{"kind": "html", "text": '<map><area href="/api/orders?token=' + CANARY + '"></map>'}], javascript_engine="basic")
        route = report["routes"][0]
        self.assertEqual((route["method"], route["path"], route["confidence"]), ("GET", "/api/orders", "declared"))
        self.assertEqual(route["query_fields"], ["token"])
        self.assertEqual(route["evidence"], [{"source": "source_1", "location": "line 1", "kind": "link"}])
        self.assertNotIn(CANARY, json.dumps(report))


if __name__ == "__main__":
    unittest.main()
