import copy
import unittest

from backend.validation import (
    ValidationError,
    eligible_analysis_answers,
    normalize_submission,
    submit_action,
)
from tests.fixtures import answered, definition, payload, with_answer


_DEFAULT_PAYLOAD = object()


class QuestionnaireValidationTests(unittest.TestCase):
    def setUp(self):
        self.definition = definition()
        self.payload = payload()

    def normalize(self, body=_DEFAULT_PAYLOAD):
        return normalize_submission(self.payload if body is _DEFAULT_PAYLOAD else body, self.definition)

    def assert_invalid(self, body):
        with self.assertRaises(ValidationError) as caught:
            self.normalize(body)
        self.assertTrue(caught.exception.errors)
        for error in caught.exception.errors:
            self.assertTrue({"field", "code", "message"} <= error.keys())

    def test_missing_unknown_none_and_declined_remain_distinct(self):
        body = with_answer(self.payload, "q06", "health_concerns", ["unknown"])
        body["answers"]["q01"] = {"age_years": {"status": "declined", "value": None}}
        normalized = self.normalize(body)["answers"]
        self.assertEqual(normalized["q06"]["health_concerns"], answered(["unknown"]))
        self.assertEqual(normalized["q01"]["age_years"]["status"], "declined")
        self.assertEqual(normalized["q02"]["last_menstrual_period"], {"status": "unanswered", "value": None})
        body["answers"]["q06"]["health_concerns"] = answered(["none"])
        self.assertEqual(self.normalize(body)["answers"]["q06"]["health_concerns"], answered(["none"]))

    def test_first_pregnancy_only_skips_prior_history(self):
        body = with_answer(self.payload, "q05", "pregnancies_total", 1)
        body["answers"]["q05"].update({
            "living_children_band": answered("one"),
            "currently_breastfeeding": answered("yes"),
            "deliveries_count": answered(2),
            "prior_complications": answered(["other"]),
            "prior_complications_other_text": answered("Previous entry"),
        })
        result = self.normalize(body)["answers"]["q05"]
        self.assertEqual(result["living_children_band"], answered("one"))
        self.assertEqual(result["currently_breastfeeding"], answered("yes"))
        for field_id in ["deliveries_count", "prior_complications", "prior_complications_other_text"]:
            self.assertEqual(result[field_id]["status"], "skipped")
            self.assertIsNone(result[field_id]["value"])
            self.assertTrue(result[field_id]["skip_rule_id"])

    def test_unknown_prior_pregnancy_count_does_not_force_skip(self):
        body = with_answer(self.payload, "q05", "deliveries_count", 2)
        self.assertEqual(self.normalize(body)["answers"]["q05"]["deliveries_count"], answered(2))

    def test_no_partner_clears_old_attitude_but_unknown_keeps_it(self):
        body = with_answer(self.payload, "q29", "partner_attitude", "continue")
        self.assertEqual(self.normalize(body)["answers"]["q29"]["partner_attitude"], answered("continue"))
        body = with_answer(body, "q04", "partner_status", "no_partner")
        result = self.normalize(body)["answers"]["q29"]["partner_attitude"]
        self.assertEqual(result, {"status": "skipped", "value": None, "skip_rule_id": "has_partner"})

    def test_client_cannot_invent_skip_for_active_question(self):
        body = with_answer(self.payload, "q04", "partner_status", "stable_partner")
        body["answers"]["q29"] = {"partner_attitude": {"status": "skipped", "value": None, "skip_rule_id": "has_partner"}}
        self.assert_invalid(body)

    def test_invalid_hidden_value_is_not_silently_accepted(self):
        body = with_answer(self.payload, "q04", "partner_status", "no_partner")
        body = with_answer(body, "q29", "partner_attitude", "invented")
        self.assert_invalid(body)

    def test_non_answered_value_must_be_null(self):
        for status in ["unanswered", "declined", "not_applicable"]:
            with self.subTest(status=status):
                body = copy.deepcopy(self.payload)
                body["answers"]["q11"] = {"work_school_support": {"status": status, "value": "support"}}
                self.assert_invalid(body)

    def test_not_applicable_requires_field_permission(self):
        body = copy.deepcopy(self.payload)
        body["answers"]["q11"] = {"work_school_support": {"status": "not_applicable", "value": None}}
        self.assertEqual(self.normalize(body)["answers"]["q11"]["work_school_support"]["status"], "not_applicable")
        body["answers"]["q01"] = {"age_years": {"status": "not_applicable", "value": None}}
        self.assert_invalid(body)

    def test_explicit_unknown_and_none_cannot_mix_with_specific_choices(self):
        for values in [["physical", "none"], ["mental", "unknown"], ["none", "unknown"], ["physical", "physical"], []]:
            with self.subTest(values=values):
                self.assert_invalid(with_answer(self.payload, "q06", "health_concerns", values))

    def test_own_opinion_can_coexist_with_not_listening_to_others(self):
        body = with_answer(self.payload, "q30", "valued_opinions", ["self", "none_others"])
        self.assertEqual(self.normalize(body)["answers"]["q30"]["valued_opinions"]["value"], ["self", "none_others"])
        self.assert_invalid(with_answer(self.payload, "q30", "valued_opinions", ["partner", "none_others"]))

    def test_support_needs_limit_and_exclusivity(self):
        good = ["medical", "emotional", "housing"]
        self.assertEqual(self.normalize(with_answer(self.payload, "q37", "support_priorities", good))["answers"]["q37"]["support_priorities"]["value"], good)
        self.assert_invalid(with_answer(self.payload, "q37", "support_priorities", good + ["privacy"]))
        self.assert_invalid(with_answer(self.payload, "q37", "support_priorities", ["none", "medical"]))

    def test_integer_rejects_boolean_fraction_and_out_of_range(self):
        for value in [True, 1.5, "5", -1, 11]:
            with self.subTest(value=value):
                self.assert_invalid(with_answer(self.payload, "q35", "decision_confidence", value))
        for value in [0, 10]:
            self.assertEqual(self.normalize(with_answer(self.payload, "q35", "decision_confidence", value))["answers"]["q35"]["decision_confidence"]["value"], value)

    def test_dates_are_strict_and_calendar_valid(self):
        for value in ["2026-02-30", "2026-1-02", "2026-01", "yesterday"]:
            with self.subTest(value=value):
                self.assert_invalid(with_answer(self.payload, "q02", "last_menstrual_period", value))
        body = with_answer(self.payload, "q02", "last_menstrual_period", "2024-02-29")
        self.assertEqual(self.normalize(body)["answers"]["q02"]["last_menstrual_period"]["value"], "2024-02-29")
        self.assert_invalid(with_answer(self.payload, "q05", "last_pregnancy_end_month", "2026-13"))
        self.assert_invalid(with_answer(self.payload, "q05", "last_pregnancy_end_month", "2026-01-01"))

    def test_unknown_fields_codes_and_untrusted_metadata_are_rejected(self):
        cases = []
        wrong_question = copy.deepcopy(self.payload)
        wrong_question["answers"]["q18"] = {}
        cases.append(wrong_question)
        cases.append(with_answer(self.payload, "q01", "made_up", 2))
        cases.append(with_answer(self.payload, "q04", "partner_status", "invented"))
        wrong_metadata = copy.deepcopy(self.payload)
        wrong_metadata["submission_id"] = "../../escape"
        cases.append(wrong_metadata)
        wrong_answer = with_answer(self.payload, "q01", "age_years", 20)
        wrong_answer["answers"]["q01"]["age_years"]["extra"] = "surprise"
        cases.append(wrong_answer)
        for body in cases:
            with self.subTest(body=body):
                self.assert_invalid(body)

    def test_questionnaire_versions_must_match(self):
        for key in ["schema_version", "questionnaire_version", "questionnaire_id"]:
            body = copy.deepcopy(self.payload)
            body[key] = "other"
            with self.subTest(key=key):
                self.assert_invalid(body)

    def test_malformed_json_shapes_are_validation_errors(self):
        for body in [None, [], "answers", 3]:
            with self.subTest(body=body):
                self.assert_invalid(body)
        for malformed in [None, [], "answer", 3]:
            body = copy.deepcopy(self.payload)
            body["answers"]["q01"] = malformed
            with self.subTest(question=malformed):
                self.assert_invalid(body)

    def test_hidden_fields_reference_only_valid_business_answers(self):
        body = with_answer(self.payload, "q38", "excluded_report_fields", ["q01.age_years", "q06.health_concerns"])
        self.assertEqual(self.normalize(body)["answers"]["q38"]["excluded_report_fields"]["value"], ["q01.age_years", "q06.health_concerns"])
        for references in [["q01.missing"], ["consent.report_use"], ["q38.report_action"], ["q01.age_years", "q01.age_years"]]:
            with self.subTest(references=references):
                self.assert_invalid(with_answer(self.payload, "q38", "excluded_report_fields", references))

    def test_exclusion_references_respect_explicit_definition_scope(self):
        field = next(field for question in self.definition["questions"] if question["id"] == "q38" for field in question["fields"] if field["id"] == "excluded_report_fields")
        field["allowed_refs"] = ["q01.age_years"]
        self.normalize(with_answer(self.payload, "q38", "excluded_report_fields", ["q01.age_years"]))
        self.assert_invalid(with_answer(self.payload, "q38", "excluded_report_fields", ["q06.health_concerns"]))

    def test_value_priorities_preserve_explicit_rank_and_relation(self):
        priorities = [
            {"factor": "Health", "relation": "uncertain", "conditions": "Need explanation"},
            {"factor": "Personal plans", "relation": "favors_terminate", "conditions": ""},
        ]
        body = with_answer(self.payload, "q17", "value_priorities", priorities)
        self.assertEqual(self.normalize(body)["answers"]["q17"]["value_priorities"]["value"], priorities)
        for bad in [priorities * 2, [{"factor": "Health", "relation": "invented", "conditions": ""}], [{"factor": "", "relation": "uncertain", "conditions": ""}], [{"factor": "Health", "relation": "uncertain", "conditions": "", "weight": 99}]]:
            with self.subTest(bad=bad):
                self.assert_invalid(with_answer(self.payload, "q17", "value_priorities", bad))

    def test_value_relations_respect_questionnaire_item_options(self):
        field = next(field for question in self.definition["questions"] if question["id"] == "q17" for field in question["fields"] if field["id"] == "value_priorities")
        field["item_fields"] = [{"id": "relation", "type": "single_select", "options": [{"code": "uncertain", "label": "Uncertain"}]}]
        item = {"factor": "Health", "relation": "uncertain", "conditions": ""}
        self.normalize(with_answer(self.payload, "q17", "value_priorities", [item]))
        item["relation"] = "favors_continue"
        self.assert_invalid(with_answer(self.payload, "q17", "value_priorities", [item]))

    def test_draft_can_be_incomplete_but_submission_requires_consent(self):
        body = copy.deepcopy(self.payload)
        body["answers"] = {}
        draft = self.normalize(body)
        with self.assertRaises(ValidationError):
            submit_action(draft, self.definition)

    def test_control_answers_choose_explicit_flow(self):
        self.assertEqual(submit_action(self.normalize(), self.definition), "submit")
        for report_action in ["edit", "stop"]:
            body = with_answer(self.payload, "q38", "report_action", report_action)
            self.assertEqual(submit_action(self.normalize(body), self.definition), report_action)
        body = with_answer(self.payload, "consent", "report_use", "disagree")
        self.assertEqual(submit_action(self.normalize(body), self.definition), "stop")
        body = with_answer(self.payload, "consent", "private_completion", "uncomfortable")
        self.assertEqual(submit_action(self.normalize(body), self.definition), "pause")

    def test_analysis_excludes_fields_and_dependent_notes_without_mutating_answers(self):
        body = with_answer(self.payload, "q05", "pregnancies_total", 2)
        body = with_answer(body, "q05", "prior_complications", ["other"])
        body = with_answer(body, "q05", "prior_complications_other_text", "Sensitive history")
        body = with_answer(body, "q38", "excluded_report_fields", ["q05.prior_complications"])
        normalized = self.normalize(body)
        original = copy.deepcopy(normalized)
        filtered = eligible_analysis_answers(normalized, self.definition)
        self.assertNotIn("prior_complications", filtered["q05"])
        self.assertNotIn("prior_complications_other_text", filtered["q05"])
        self.assertEqual(filtered["q05"]["pregnancies_total"], answered(2))
        self.assertEqual(normalized, original)

    def test_analysis_omits_flow_controls_but_preserves_general_report_notes(self):
        body = with_answer(self.payload, "q38", "report_notes", "Please explain the uncertainty")
        filtered = eligible_analysis_answers(self.normalize(body), self.definition)
        self.assertNotIn("consent", filtered)
        self.assertEqual(filtered["q38"], {"report_notes": answered("Please explain the uncertainty")})

    def test_unresolved_free_text_exclusion_blocks_future_analysis(self):
        body = with_answer(self.payload, "q38", "exclusion_notes", "Do not mention my family situation")
        with self.assertRaises(ValidationError) as caught:
            eligible_analysis_answers(self.normalize(body), self.definition)
        self.assertTrue(any(error["code"] == "unresolved_exclusion" for error in caught.exception.errors))

    def test_analysis_requires_current_authorization_and_confirmation(self):
        bodies = [
            with_answer(self.payload, "consent", "report_use", "disagree"),
            with_answer(self.payload, "consent", "private_completion", "uncomfortable"),
            with_answer(self.payload, "q38", "report_action", "edit"),
            with_answer(self.payload, "q38", "report_action", "stop"),
        ]
        missing_confirmation = copy.deepcopy(self.payload)
        del missing_confirmation["answers"]["q38"]
        bodies.append(missing_confirmation)
        for body in bodies:
            with self.subTest(answers=body["answers"]), self.assertRaises(ValidationError):
                eligible_analysis_answers(self.normalize(body), self.definition)


if __name__ == "__main__":
    unittest.main()
