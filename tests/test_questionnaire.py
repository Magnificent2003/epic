"""Regression checks against the actual EPIC questionnaire, not a test fixture."""

import copy
import json
import unittest
from pathlib import Path

from backend.validation import ValidationError, eligible_analysis_answers, normalize_submission
from tests.fixtures import answered, payload, with_answer


ROOT = Path(__file__).resolve().parents[1]


class PublishedQuestionnaireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definition = json.loads((ROOT / "questionnaire" / "definition.json").read_text(encoding="utf-8"))
        cls.fields = {f"{q['id']}.{field['id']}": field for q in cls.definition["questions"] for field in q["fields"]}

    def normalize(self, body):
        return normalize_submission(body, self.definition)

    def test_definition_and_questionnaire_share_fields_and_options(self):
        questionnaire = (ROOT / "意外妊娠决策支持问卷.md").read_text(encoding="utf-8")
        questions = self.definition["questions"]
        numbers = [q["source_number"] for q in questions if q["source_number"] is not None]
        self.assertEqual(numbers, [n for n in range(1, 39) if n not in [18, 28]])
        self.assertEqual(len({q["id"] for q in questions}), len(questions))
        self.assertEqual(len(self.fields), sum(len(q["fields"]) for q in questions))
        section_questions = [qid for section in self.definition["sections"] for qid in section["question_ids"]]
        self.assertEqual(section_questions, [q["id"] for q in questions])
        for ref, field in self.fields.items():
            with self.subTest(field=ref):
                self.assertIn(field["label"], questionnaire)
                if field.get("visible_when"):
                    self.assertIn(field["visible_when"]["field"], self.fields)
                option_codes = [option["code"] for option in field.get("options", [])]
                self.assertEqual(len(option_codes), len(set(option_codes)))
                for option in field.get("options", []):
                    self.assertIn(option["label"], questionnaire)
                for code in field.get("exclusive_options", []):
                    self.assertIn(code, option_codes)
                for conflict in field.get("conflicts", []):
                    self.assertTrue(set(conflict) <= set(option_codes))

    def test_only_flow_controls_are_required_and_final_confirmation_is_last(self):
        required = {ref for ref, field in self.fields.items() if field.get("required")}
        self.assertEqual(required, {"consent.report_use", "consent.private_completion", "q38.report_action"})
        self.assertEqual(self.definition["questions"][-1]["fields"][-1]["id"], "report_action")

    def test_first_pregnancy_skips_all_nested_history_but_preserves_current_answers(self):
        body = with_answer(payload(), "q05", "pregnancies_total", 1)
        body["answers"]["q05"].update({
            "living_children_band": answered("one"),
            "currently_breastfeeding": answered("yes"),
            "miscarriages_count": answered(1),
            "terminations_count": answered(1),
            "deliveries_count": answered(1),
            "last_pregnancy_end_month": answered("2025-01"),
            "prior_complications": answered(["other"]),
            "prior_complications_other_text": answered("Old history answer"),
        })
        current = self.normalize(body)["answers"]["q05"]
        history = ["miscarriages_count", "terminations_count", "deliveries_count", "last_pregnancy_end_month", "prior_complications", "prior_complications_other_text"]
        for field_id in history:
            with self.subTest(field=field_id):
                self.assertEqual(current[field_id]["status"], "skipped")
                self.assertIsNone(current[field_id]["value"])
        self.assertEqual(current["living_children_band"], answered("one"))
        self.assertEqual(current["currently_breastfeeding"], answered("yes"))
        del body["answers"]["q05"]["pregnancies_total"]
        history_unknown = self.normalize(body)["answers"]["q05"]
        self.assertEqual(history_unknown["deliveries_count"], answered(1))
        self.assertEqual(history_unknown["prior_complications_other_text"], answered("Old history answer"))

    def test_no_partner_skips_entire_q29_but_nonfixed_partner_does_not(self):
        body = with_answer(payload(), "q04", "partner_status", "no_partner")
        body["answers"]["q29"] = {
            "partner_attitude": answered("continue"),
            "partner_influence": answered("none"),
            "partner_support_notes": answered("Has promised help"),
        }
        result = self.normalize(body)["answers"]
        self.assertTrue(all(answer["status"] == "skipped" for answer in result["q29"].values()))
        self.assertTrue(all(answer["status"] != "skipped" for answer in result["q31"].values()))
        body["answers"]["q04"]["partner_status"] = answered("nonfixed_partner")
        result = self.normalize(body)["answers"]
        self.assertEqual(result["q29"], body["answers"]["q29"])

    def test_work_school_support_only_skips_defined_inapplicable_activities(self):
        for activity in ["family_care", "not_working_or_studying"]:
            body = with_answer(payload(), "q11", "main_activity", activity)
            body = with_answer(body, "q11", "work_school_support", "clear")
            with self.subTest(activity=activity):
                self.assertEqual(self.normalize(body)["answers"]["q11"]["work_school_support"]["status"], "skipped")
        body = with_answer(payload(), "q11", "main_activity", "other")
        body["answers"]["q11"]["work_school_support"] = {"status": "not_applicable", "value": None}
        self.assertEqual(self.normalize(body)["answers"]["q11"]["work_school_support"]["status"], "not_applicable")
        body = with_answer(payload(), "q11", "work_school_support", "clear")
        self.assertEqual(self.normalize(body)["answers"]["q11"]["work_school_support"], answered("clear"))

    def test_self_and_no_other_opinions_are_compatible(self):
        body = with_answer(payload(), "q30", "valued_opinions", ["self", "no_others"])
        self.assertEqual(self.normalize(body)["answers"]["q30"]["valued_opinions"]["value"], ["self", "no_others"])
        body = with_answer(payload(), "q30", "valued_opinions", ["partner", "no_others"])
        with self.assertRaises(ValidationError):
            self.normalize(body)

    def test_three_support_needs_are_allowed_but_four_or_none_plus_specific_are_not(self):
        choices = ["confirm_pregnancy", "emotional_support", "care_support"]
        body = with_answer(payload(), "q37", "support_priorities", choices)
        self.assertEqual(self.normalize(body)["answers"]["q37"]["support_priorities"]["value"], choices)
        for invalid in [choices + ["financial_policy"], ["none", "emotional_support"]]:
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                self.normalize(with_answer(payload(), "q37", "support_priorities", invalid))

    def test_conditional_wishes_retain_conditions_then_clear_them_after_change(self):
        cases = [
            ("q15", "continuation_willingness", "continuation_conditions", "willing"),
            ("q15", "parenting_willingness", "parenting_conditions", "unwilling"),
            ("q16", "termination_acceptability", "termination_conditions", "acceptable"),
        ]
        for qid, source_id, conditions_id, replacement in cases:
            with self.subTest(field=conditions_id):
                body = with_answer(payload(), qid, source_id, "conditional")
                body = with_answer(body, qid, conditions_id, "Need reliable support")
                self.assertEqual(self.normalize(body)["answers"][qid][conditions_id], answered("Need reliable support"))
                body["answers"][qid][source_id] = answered(replacement)
                cleared = self.normalize(body)["answers"][qid][conditions_id]
                self.assertEqual(cleared["status"], "skipped")
                self.assertIsNone(cleared["value"])

    def test_persons_explicit_value_order_survives_without_scoring(self):
        priorities = [
            {"factor": "健康情况", "relation": "uncertain", "conditions": "希望先核实检查结果"},
            {"factor": "照顾现有孩子", "relation": "favors_terminate", "conditions": ""},
        ]
        body = with_answer(payload(), "q17", "value_priorities", priorities)
        self.assertEqual(self.normalize(body)["answers"]["q17"]["value_priorities"]["value"], priorities)
        empty = self.normalize(payload())["answers"]["q17"]["value_priorities"]
        self.assertEqual(empty, {"status": "unanswered", "value": None})

    def test_declared_exclusion_scope_and_filtering_match_business_fields(self):
        exclusion = self.fields["q38.excluded_report_fields"]
        expected = {ref for ref in self.fields if ref.split(".")[0] not in {"consent", "q38"}}
        expected.add("q38.report_notes")
        self.assertEqual(set(exclusion["allowed_refs"]), expected)
        body = with_answer(payload(), "q15", "continuation_willingness", "conditional")
        body = with_answer(body, "q15", "continuation_conditions", "Sensitive condition")
        body = with_answer(body, "q38", "excluded_report_fields", ["q15.continuation_willingness"])
        normalized = self.normalize(body)
        original = copy.deepcopy(normalized)
        filtered = eligible_analysis_answers(normalized, self.definition)
        self.assertNotIn("continuation_willingness", filtered["q15"])
        self.assertNotIn("continuation_conditions", filtered["q15"])
        self.assertEqual(normalized, original)
        body = with_answer(body, "q38", "exclusion_notes", "还请隐藏某段关系经历")
        with self.assertRaises(ValidationError):
            eligible_analysis_answers(self.normalize(body), self.definition)

    def test_general_report_notes_can_be_excluded_but_flow_controls_cannot(self):
        body = with_answer(payload(), "q38", "report_notes", "Sensitive general supplement")
        normalized = self.normalize(body)
        self.assertEqual(
            eligible_analysis_answers(normalized, self.definition)["q38"]["report_notes"],
            answered("Sensitive general supplement"),
        )
        excluded = with_answer(body, "q38", "excluded_report_fields", ["q38.report_notes"])
        normalized_excluded = self.normalize(excluded)
        original = copy.deepcopy(normalized_excluded)
        filtered = eligible_analysis_answers(normalized_excluded, self.definition)
        self.assertNotIn("report_notes", filtered.get("q38", {}))
        self.assertEqual(normalized_excluded, original)
        controls = [ref for ref in self.fields if ref.startswith("consent.") or (ref.startswith("q38.") and ref != "q38.report_notes")]
        for ref in controls:
            with self.subTest(ref=ref), self.assertRaises(ValidationError):
                self.normalize(with_answer(body, "q38", "excluded_report_fields", [ref]))


if __name__ == "__main__":
    unittest.main()
