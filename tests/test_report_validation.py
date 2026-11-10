"""Machine-checkable report rules, separate from the model's semantic review."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from backend.report_validation import (
    ReportValidationError,
    parse_model_json,
    parse_model_response,
    validate_report,
    validate_review,
)


ROOT = Path(__file__).resolve().parents[1]
REFS = {
    "q17.continuation_concerns", "q17.termination_concerns",
    "q17.value_priorities", "q34.current_preference",
    "q36.emotional_support_preference", "q37.support_priorities",
}


def report():
    return {
        "00": "您目前尚无明确倾向。",
        "01": [{
            "factor": "您提到的顾虑",
            "continue_pregnancy": "您提到了继续妊娠时的顾虑。",
            "terminate_pregnancy": "从现有回答看，这方面尚不清楚。",
            "evidence_refs": ["q17.continuation_concerns"],
        }],
        "02": [{
            "analysis": "您希望尽快获得情绪支持，同时在优先帮助题中选择了其他帮助；这些回答不必相互抵消。",
            "evidence_refs": ["q36.emotional_support_preference", "q37.support_priorities"],
        }],
        "03": {
            "recommendation": "本题未提供价值排序，目前不足以判断哪一方向更符合您的意愿。",
            "evidence_refs": ["q17.value_priorities"],
        },
    }


class ReportContractTests(unittest.TestCase):
    def assert_invalid(self, call, code=None):
        with self.assertRaises(ReportValidationError) as caught:
            call()
        errors = caught.exception.errors
        self.assertTrue(errors)
        self.assertTrue(all(set(item) == {"path", "code", "message"} for item in errors))
        if code:
            self.assertIn(code, {item["code"] for item in errors})
        return errors

    def test_strict_parser_preserves_one_complete_json_object(self):
        source = report()
        self.assertEqual(parse_model_json(" \n" + json.dumps(source, ensure_ascii=False) + "\n"), source)

    def test_parser_rejects_fences_trailing_text_duplicate_keys_and_nonfinite_values(self):
        invalid = [
            '```json\n{}\n```', '说明：{}', '{} 后记', '{}{}', '{broken',
            '{"x":1,"x":2}', '{"x":{"a":1,"a":2}}',
            '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}',
            '{"x":"\\ud800"}', '{"x":"\ud800"}', '', None,
        ]
        for source in invalid:
            with self.subTest(source=repr(source)):
                self.assert_invalid(lambda: parse_model_json(source), "invalid_json")

    def test_parser_requires_object_and_bounds_response_size(self):
        for source in ['[]', 'null', 'true', '"text"', '1']:
            with self.subTest(source=source):
                self.assert_invalid(lambda: parse_model_json(source), "type")
        self.assert_invalid(lambda: parse_model_json('{"x":"' + 'a' * (1024 * 1024) + '"}'), "invalid_json")

    def test_response_parser_accepts_only_plain_json_or_a_complete_outer_fence(self):
        source = report()
        body = json.dumps(source, ensure_ascii=False)
        for response in [body, f"```json\n{body}\n```", f"```\n{body}\n```", f" \n```JSON\r\n{body}\r\n```\n "]:
            with self.subTest(prefix=response[:12]):
                self.assertEqual(parse_model_response(response), source)

    def test_response_parser_rejects_explanations_extra_fences_and_truncation(self):
        for response in [
            '说明：\n```json\n{}\n```', '```json\n{}\n```\n后记',
            '```json\n{}\n```\n```json\n{}\n```', '```json\n{}',
            '```python\n{}\n```', '```json\n{} 后记\n```',
        ]:
            with self.subTest(response=response):
                self.assert_invalid(lambda: parse_model_response(response), "invalid_json")

    def test_response_parser_keeps_strict_checks_inside_outer_fence(self):
        for body in ['{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}', '{"x":"\\ud800"}', '{}{}']:
            with self.subTest(body=body):
                self.assert_invalid(lambda: parse_model_response(f"```json\n{body}\n```"), "invalid_json")
        self.assert_invalid(lambda: parse_model_response('```json\n[]\n```'), "type")
        self.assert_invalid(lambda: parse_model_response('```json\n{"x":"' + 'a' * (1024 * 1024) + '"}\n```'), "invalid_json")

    def test_valid_report_preserves_content_and_refs(self):
        source = report()
        before = deepcopy(source)
        self.assertIsNone(validate_report(source, REFS))
        self.assertEqual(source, before)

    def test_empty_comparison_analysis_and_evidence_free_insufficiency_are_structurally_valid(self):
        source = report()
        source["01"] = []
        source["02"] = []
        source["03"]["evidence_refs"] = []
        validate_report(source, REFS)

    def test_report_requires_exact_columns_and_object_shapes(self):
        for update in [{"extra": "text"}, {"00": {"text": "倾向"}}, {"01": {}}, {"02": "分析"}, {"03": "建议"}]:
            source = report()
            source.update(update)
            with self.subTest(update=update):
                self.assert_invalid(lambda: validate_report(source, REFS))
        source = report()
        del source["00"]
        self.assert_invalid(lambda: validate_report(source, REFS), "required")
        source = report()
        source["01"][0]["invented"] = "Do not permit extra content"
        self.assert_invalid(lambda: validate_report(source, REFS), "unknown_fields")
        self.assert_invalid(lambda: validate_report([], REFS), "type")

    def test_required_report_text_cannot_be_blank_or_nontext(self):
        for value in ["", " \n\t", None, 12, True, []]:
            source = report()
            source["03"]["recommendation"] = value
            with self.subTest(value=value):
                self.assert_invalid(lambda: validate_report(source, REFS), "text")

    def test_references_must_be_available_unique_and_canonical(self):
        cases = [
            (["q01.age_years"], "unavailable_reference"),
            (["q1.age_years"], "unavailable_reference"),
            (["q17.continuation_concerns", "q17.continuation_concerns"], "duplicate_reference"),
            ([None], "unavailable_reference"),
            ([["q17.continuation_concerns"]], "unavailable_reference"),
            ([], "reference_count"),
            ("q17.continuation_concerns", "type"),
        ]
        for refs, code in cases:
            source = report()
            source["01"][0]["evidence_refs"] = refs
            with self.subTest(refs=refs):
                self.assert_invalid(lambda: validate_report(source, REFS), code)

    def test_cross_question_analysis_requires_two_different_questions(self):
        for refs in [["q17.continuation_concerns"], ["q17.continuation_concerns", "q17.termination_concerns"]]:
            source = report()
            source["02"][0]["evidence_refs"] = refs
            with self.subTest(refs=refs):
                self.assert_invalid(lambda: validate_report(source, REFS), "cross_question")

    def test_pass_reuses_draft_without_revision_or_issues(self):
        draft = report()
        review = {"decision": "pass", "issues": [], "revised_report": None}
        before = deepcopy(draft)
        self.assertIsNone(validate_review(review, draft, REFS))
        self.assertEqual(draft, before)
        for update in [{"issues": [{"path": "/00", "reason": "请核对。"}]}, {"revised_report": report()}]:
            invalid = deepcopy(review)
            invalid.update(update)
            self.assert_invalid(lambda: validate_review(invalid, draft, REFS), "decision_contract")

    def test_revise_requires_issues_and_a_complete_valid_report(self):
        revised = report()
        revised["00"] = "您目前仍在权衡两种选择。"
        review = {"decision": "revise", "issues": [{"path": "/00", "reason": "保留回答中的不确定性。"}], "revised_report": revised}
        validate_review(review, report(), REFS)
        for update in [{"issues": []}, {"revised_report": None}, {"revised_report": {"00": "部分修订"}}]:
            invalid = deepcopy(review)
            invalid.update(update)
            self.assert_invalid(lambda: validate_review(invalid, report(), REFS))
        review["revised_report"]["03"]["evidence_refs"] = ["q01.age_years"]
        errors = self.assert_invalid(lambda: validate_review(review, report(), REFS), "unavailable_reference")
        self.assertTrue(any(item["path"].startswith("/revised_report/") for item in errors))

    def test_hold_requires_a_reason_and_forbids_a_publishable_revision(self):
        review = {"decision": "hold", "issues": [{"path": "", "reason": "关键陈述需要进一步复核。"}], "revised_report": None}
        validate_review(review, report(), REFS)
        for update in [{"issues": []}, {"revised_report": report()}]:
            invalid = deepcopy(review)
            invalid.update(update)
            self.assert_invalid(lambda: validate_review(invalid, report(), REFS), "decision_contract")

    def test_issue_pointer_must_resolve_to_existing_draft_content(self):
        for path in ["", "/00", "/01", "/01/0", "/01/0/factor", "/02/0/evidence_refs/1", "/03/recommendation"]:
            review = {"decision": "hold", "issues": [{"path": path, "reason": "请复核此处。"}], "revised_report": None}
            with self.subTest(path=path):
                validate_review(review, report(), REFS)
        for path in ["00", "/04", "/01/1", "/01/-", "/01/00", "/00/0", "/01/~2", "/01/0/missing", "/01/" + "9" * 100, 0, None]:
            review = {"decision": "hold", "issues": [{"path": path, "reason": "请复核此处。"}], "revised_report": None}
            with self.subTest(path=path):
                self.assert_invalid(lambda: validate_review(review, report(), REFS), "pointer")

    def test_review_requires_fixed_fields_and_valid_decision_even_for_unhashable_values(self):
        for decision in ["approve", "", [], {}, None, True]:
            review = {"decision": decision, "issues": [], "revised_report": None}
            with self.subTest(decision=decision):
                self.assert_invalid(lambda: validate_review(review, report(), REFS), "decision")
        self.assert_invalid(lambda: validate_review({}, report(), REFS), "required")
        self.assert_invalid(lambda: validate_review([], report(), REFS), "type")
        review = {"decision": "hold", "issues": [{"path": "", "reason": " ", "extra": True}], "revised_report": None}
        self.assert_invalid(lambda: validate_review(review, report(), REFS), "unknown_fields")

    def test_validation_errors_never_echo_rejected_content_or_identifiers(self):
        secret = "不得记录到错误信息的原话"
        source = report()
        source[secret] = secret
        source["03"]["evidence_refs"] = [secret]
        errors = self.assert_invalid(lambda: validate_report(source, REFS))
        self.assertNotIn(secret, json.dumps(errors, ensure_ascii=False))
        errors = self.assert_invalid(lambda: parse_model_json("非JSON " + secret))
        self.assertNotIn(secret, json.dumps(errors, ensure_ascii=False))

    def test_schema_files_describe_the_same_fixed_report_and_review_contracts(self):
        report_schema = json.loads((ROOT / "prompt/report_schema.json").read_text(encoding="utf-8"))
        review_schema = json.loads((ROOT / "prompt/review_schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(report_schema["required"]), {"00", "01", "02", "03"})
        self.assertFalse(report_schema["additionalProperties"])
        embedded = review_schema["$defs"]["report"]
        self.assertEqual(embedded, {key: value for key, value in report_schema.items() if key not in {"$schema", "title"}})
        self.assertEqual(set(review_schema["required"]), {"decision", "issues", "revised_report"})
        self.assertEqual(review_schema["properties"]["decision"]["enum"], ["pass", "revise", "hold"])


if __name__ == "__main__":
    unittest.main()
