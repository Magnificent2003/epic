"""Small independent questionnaire for exercising rule behavior."""

from copy import deepcopy


def answered(value):
    return {"status": "answered", "value": value}


def options(*codes):
    return [{"code": code, "label": code} for code in codes]


def field(field_id, field_type, **constraints):
    return {"id": field_id, "label": field_id, "type": field_type, **constraints}


def question(question_id, *fields):
    return {
        "id": question_id,
        "source_number": None if question_id == "consent" else int(question_id[1:]),
        "section_id": "test",
        "title": question_id,
        "fields": list(fields),
    }


def definition():
    return {
        "schema_version": "1.0",
        "questionnaire_id": "epic_pregnancy_decision",
        "version": "1.0.0",
        "title": "Test questionnaire",
        "sections": [{"id": "test", "title": "Test", "question_ids": []}],
        "questions": [
            question(
                "consent",
                field("report_use", "single_select", required=True, options=options("agree", "disagree")),
                field("private_completion", "single_select", required=True, options=options("comfortable", "assisted", "uncomfortable")),
            ),
            question("q01", field("age_years", "integer", minimum=0)),
            question("q02", field("last_menstrual_period", "date")),
            question("q04", field("partner_status", "single_select", options=options("stable_partner", "no_partner"))),
            question(
                "q05",
                field("pregnancies_total", "integer", minimum=1),
                field("living_children_band", "single_select", options=options("zero", "one", "three_or_more")),
                field("currently_breastfeeding", "single_select", options=options("yes", "no", "unknown")),
                field("deliveries_count", "integer", minimum=0, visible_when={"rule_id": "prior_history", "field": "q05.pregnancies_total", "operator": "not_equals", "value": 1}),
                field("last_pregnancy_end_month", "year_month", visible_when={"rule_id": "prior_history", "field": "q05.pregnancies_total", "operator": "not_equals", "value": 1}),
                field("prior_complications", "multi_select", options=options("other", "none", "unknown"), exclusive_options=["none", "unknown"], visible_when={"rule_id": "prior_history", "field": "q05.pregnancies_total", "operator": "not_equals", "value": 1}),
                field("prior_complications_other_text", "text", max_length=50, visible_when={"rule_id": "prior_history_other", "field": "q05.prior_complications", "operator": "contains", "value": "other"}),
            ),
            question("q06", field("health_concerns", "multi_select", options=options("physical", "mental", "none", "unknown"), exclusive_options=["none", "unknown"])),
            question("q11", field("work_school_support", "single_select", options=options("support", "unknown"), allow_not_applicable=True)),
            question("q17", field("value_priorities", "ranked_values", max_selections=3, max_length=100)),
            question("q29", field("partner_attitude", "single_select", options=options("continue", "terminate", "unknown"), visible_when={"rule_id": "has_partner", "field": "q04.partner_status", "operator": "not_equals", "value": "no_partner"})),
            question("q30", field("valued_opinions", "multi_select", options=options("self", "partner", "family", "none_others"), conflicts=[["partner", "none_others"], ["family", "none_others"]])),
            question("q35", field("decision_confidence", "integer", minimum=0, maximum=10)),
            question("q37", field("support_priorities", "multi_select", options=options("medical", "emotional", "housing", "privacy", "none"), max_selections=3, exclusive_options=["none"])),
            question(
                "q38",
                field("excluded_report_fields", "field_refs"),
                field("exclusion_notes", "text", max_length=100),
                field("report_notes", "text", max_length=100),
                field("report_action", "single_select", required=True, options=options("confirm", "edit", "stop")),
            ),
        ],
    }


def payload():
    return {
        "schema_version": "1.0",
        "questionnaire_id": "epic_pregnancy_decision",
        "questionnaire_version": "1.0.0",
        "answers": {
            "consent": {"report_use": answered("agree"), "private_completion": answered("comfortable")},
            "q38": {"report_action": answered("confirm")},
        },
    }


def with_answer(body, question_id, field_id, value):
    result = deepcopy(body)
    result["answers"].setdefault(question_id, {})[field_id] = answered(value)
    return result
