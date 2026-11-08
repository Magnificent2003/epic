import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

// No package manager or package.json is needed by this browser-native module.
const source = await readFile(new URL('../app/questionnaire.js', import.meta.url), 'utf8');
const rules = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const definition = JSON.parse(await readFile(new URL('../questionnaire/definition.json', import.meta.url), 'utf8'));
const index = rules.buildIndex(definition);
const answered = value => ({ status: 'answered', value });
const errorAt = (answers, field, options) => rules.validateAnswers(definition, answers, options).some(error => error.field === field);
const set = (answers, ref, value) => rules.setAnswer(definition, answers, ref, answered(value));
const fresh = () => rules.emptyAnswers(definition);

function consented() {
  let answers = set(fresh(), 'consent.report_use', 'agree');
  return set(answers, 'consent.private_completion', 'comfortable');
}

test('the actual definition produces every question and a readable index', () => {
  const answers = fresh();
  assert.equal(index.size, 122);
  assert.equal(Object.keys(answers).length, 37);
  assert.equal(index.get('q34.current_preference').sourceNumber, 34);
  assert.equal(index.get('q34.current_preference').questionTitle, '您目前的妊娠倾向');
  assert.equal(rules.validateAnswers(definition, answers).length, 0);
});

test('first pregnancy clears nested old history but preserves current children and breastfeeding', () => {
  let answers = set(fresh(), 'q05.pregnancies_total', 2);
  answers = set(answers, 'q05.prior_complications', ['other']);
  answers = set(answers, 'q05.prior_complications_other_text', '旧回答');
  answers = set(answers, 'q05.living_children_band', 'one');
  answers = set(answers, 'q05.currently_breastfeeding', 'yes');
  const before = structuredClone(answers);
  const next = set(answers, 'q05.pregnancies_total', 1);
  assert.deepEqual(answers, before);
  assert.equal(next.q05.prior_complications.status, 'skipped');
  assert.equal(next.q05.prior_complications_other_text.status, 'skipped');
  assert.equal(next.q05.prior_complications_other_text.value, null);
  assert.deepEqual(next.q05.living_children_band, answered('one'));
  assert.deepEqual(next.q05.currently_breastfeeding, answered('yes'));
  assert.equal(rules.validateAnswers(definition, next).length, 0);
});

test('making a skipped field applicable again restores unanswered, not stale content', () => {
  let answers = set(fresh(), 'q04.partner_status', 'stable_partner');
  answers = set(answers, 'q29.partner_support_notes', '旧回答');
  answers = set(answers, 'q04.partner_status', 'no_partner');
  assert.ok(Object.values(answers.q29).every(answer => answer.status === 'skipped'));
  answers = set(answers, 'q04.partner_status', 'nonfixed_partner');
  assert.ok(Object.values(answers.q29).every(answer => answer.status === 'unanswered'));
  assert.equal(answers.q29.partner_support_notes.value, null);
});

test('unanswered and declined upstream values do not imply negative answers', () => {
  let answers = set(fresh(), 'q29.partner_attitude', 'continue');
  answers = rules.setAnswer(definition, answers, 'q04.partner_status', { status: 'declined', value: null });
  assert.deepEqual(answers.q29.partner_attitude, answered('continue'));
  assert.equal(rules.evaluateVisibility(index.get('q29.partner_attitude').visible_when, answers), null);
});

test('applicability clears institution support only for defined main activities', () => {
  let answers = set(fresh(), 'q11.main_activity', 'other');
  answers = rules.setAnswer(definition, answers, 'q11.work_school_support', { status: 'not_applicable', value: null });
  assert.equal(rules.validateAnswers(definition, answers).length, 0);
  answers = set(answers, 'q11.main_activity', 'family_care');
  assert.equal(answers.q11.work_school_support.status, 'skipped');
});

test('every answer or report-setting edit invalidates the previous confirmation', () => {
  let answers = set(consented(), 'q38.report_action', 'confirm');
  assert.equal(rules.validateAnswers(definition, answers, { requireFinal: true }).length, 0);
  answers = set(answers, 'q38.excluded_report_fields', ['q01.age_years']);
  assert.deepEqual(answers.q38.report_action, { status: 'unanswered', value: null });
  assert.ok(errorAt(answers, 'q38.report_action', { requireFinal: true }));
  answers = set(answers, 'q38.report_action', 'confirm');
  answers = set(answers, 'q01.age_years', 27);
  assert.equal(answers.q38.report_action.status, 'unanswered');
});

test('exclusive choice replaces specifics and a specific replaces the exclusive choice', () => {
  const field = index.get('q06.health_concerns');
  const specific = field.options.find(option => !field.exclusive_options.includes(option.code)).code;
  assert.deepEqual(rules.toggleOption(field, [specific], 'none'), { values: ['none'] });
  assert.deepEqual(rules.toggleOption(field, ['none'], specific), { values: [specific] });
  assert.deepEqual(rules.toggleOption(field, [specific], specific), { values: [] });
});

test('q30 preserves self with no-others while replacing actual conflicting people', () => {
  const field = index.get('q30.valued_opinions');
  const selected = rules.toggleOption(field, ['self', 'partner'], 'no_others').values;
  assert.deepEqual(selected, ['self', 'no_others']);
  const next = rules.toggleOption(field, selected, 'family').values;
  assert.deepEqual(next, ['self', 'family']);
  assert.equal(errorAt(set(fresh(), 'q30.valued_opinions', selected), 'q30.valued_opinions'), false);
});

test('a fourth support need is refused without erasing the first three', () => {
  const field = index.get('q37.support_priorities');
  const selected = ['confirm_pregnancy', 'care_support', 'emotional_support'];
  const result = rules.toggleOption(field, selected, 'work_study');
  assert.ok(result.error);
  assert.deepEqual(result.values, selected);
  assert.deepEqual(selected, ['confirm_pregnancy', 'care_support', 'emotional_support']);
});

test('text input handling preserves zero and invalid numbers without silently coercing', () => {
  const field = index.get('q35.decision_confidence');
  assert.deepEqual(rules.inputAnswer(field, ''), { status: 'unanswered', value: null });
  assert.deepEqual(rules.inputAnswer(field, '0'), answered(0));
  assert.deepEqual(rules.inputAnswer(field, '1.5'), answered('1.5'));
  assert.deepEqual(rules.inputAnswer(field, '2e1'), answered('2e1'));
  for (const value of [true, 1.5, '8', 11, -1, Number.MAX_SAFE_INTEGER + 1]) {
    assert.ok(errorAt(set(fresh(), 'q35.decision_confidence', value), 'q35.decision_confidence'));
  }
  assert.equal(errorAt(set(fresh(), 'q35.decision_confidence', 0), 'q35.decision_confidence'), false);
});

test('dates follow calendar rules and do not invent the day for a year-month', () => {
  for (const value of ['2026-02-29', '2026-13-01', '2026-2-01', '0000-01-01']) {
    assert.ok(errorAt(set(fresh(), 'q02.last_menstrual_period', value), 'q02.last_menstrual_period'));
  }
  assert.equal(errorAt(set(fresh(), 'q02.last_menstrual_period', '2024-02-29'), 'q02.last_menstrual_period'), false);
  assert.equal(errorAt(set(fresh(), 'q05.last_pregnancy_end_month', '2025-01'), 'q05.last_pregnancy_end_month'), false);
  assert.ok(errorAt(set(fresh(), 'q05.last_pregnancy_end_month', '2025-01-01'), 'q05.last_pregnancy_end_month'));
});

test('optional blanks are valid but consent, privacy, and final confirmation are separate gates', () => {
  assert.equal(rules.validateAnswers(definition, fresh()).length, 0);
  assert.ok(errorAt(fresh(), 'consent.report_use', { requireConsent: true }));
  let answers = consented();
  assert.equal(rules.validateAnswers(definition, answers, { requireConsent: true }).length, 0);
  assert.ok(errorAt(answers, 'q38.report_action', { requireFinal: true }));
  answers = set(answers, 'q38.report_action', 'confirm');
  assert.equal(rules.validateAnswers(definition, answers, { requireFinal: true }).length, 0);
  answers = set(answers, 'consent.private_completion', 'uncomfortable');
  assert.ok(errorAt(answers, 'consent.private_completion', { requireConsent: true }));
});

test('unfinished ranked value rows remain visible invalid data instead of disappearing', () => {
  const rows = [{ factor: '', relation: 'uncertain', conditions: '还需要考虑的条件' }];
  const answers = set(fresh(), 'q17.value_priorities', rows);
  assert.deepEqual(rules.makePayload(definition, answers).answers.q17.value_priorities.value, rows);
  assert.ok(errorAt(answers, 'q17.value_priorities'));
});

test('explicit value priority order is retained, and duplicate factors are rejected', () => {
  const rows = [
    { factor: '本人健康', relation: 'uncertain', conditions: '先了解检查结果' },
    { factor: '工作计划', relation: 'conflict', conditions: '' },
  ];
  const answers = set(fresh(), 'q17.value_priorities', rows);
  assert.equal(errorAt(answers, 'q17.value_priorities'), false);
  assert.deepEqual(rules.makePayload(definition, answers).answers.q17.value_priorities.value, rows);
  assert.ok(errorAt(set(answers, 'q17.value_priorities', [rows[0], rows[0]]), 'q17.value_priorities'));
});

test('reports allow excluding business answers and general notes but never controls', () => {
  const answers = set(fresh(), 'q38.excluded_report_fields', ['q01.age_years', 'q38.report_notes']);
  assert.equal(errorAt(answers, 'q38.excluded_report_fields'), false);
  for (const ref of ['consent.report_use', 'q38.report_action', 'q18.missing']) {
    assert.ok(errorAt(set(fresh(), 'q38.excluded_report_fields', [ref]), 'q38.excluded_report_fields'));
  }
});

test('malformed imported drafts are rejected before normalization can drop unknown data', () => {
  for (const bad of [null, 'bad', { status: 'answered' }, { status: 'declined', value: 'secret' }, { status: 'made_up', value: null }]) {
    const answers = fresh();
    answers.q01.age_years = bad;
    assert.ok(errorAt(answers, 'q01.age_years'));
  }
  const extra = fresh();
  extra.q18 = { guessed: answered(1) };
  assert.ok(rules.validateAnswers(definition, extra).some(error => error.code === 'unknown_question'));
});

test('payloads keep unanswered, declined, unknown and none distinct, with no server metadata', () => {
  let answers = set(fresh(), 'q06.health_concerns', ['unknown']);
  answers = rules.setAnswer(definition, answers, 'q01.age_years', { status: 'declined', value: null });
  answers = set(answers, 'q08.financial_burdens', ['none']);
  const payload = rules.makePayload(definition, answers);
  assert.deepEqual(Object.keys(payload).sort(), ['answers', 'questionnaire_id', 'questionnaire_version', 'schema_version']);
  assert.deepEqual(payload.answers.q06.health_concerns, answered(['unknown']));
  assert.deepEqual(payload.answers.q08.financial_burdens, answered(['none']));
  assert.equal(payload.answers.q01.age_years.status, 'declined');
  assert.equal(payload.answers.q02.last_menstrual_period.status, 'unanswered');
});

test('review text shows human labels, explicit absence and ranked relationship meaning', () => {
  assert.equal(rules.labelAnswer(definition, 'q35.decision_confidence', answered(0)), '0');
  assert.equal(rules.labelAnswer(definition, 'q01.age_years', { status: 'declined', value: null }), '不愿回答');
  assert.equal(rules.labelAnswer(definition, 'q38.excluded_report_fields', answered([])), '无额外排除要求');
  const text = rules.labelAnswer(definition, 'q17.value_priorities', answered([{ factor: '个人计划', relation: 'favors_continue', conditions: '' }]));
  assert.match(text, /个人计划/);
  assert.match(text, /更支持继续/);
});
