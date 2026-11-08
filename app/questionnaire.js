/** Shared client-side questionnaire rules. The API remains the final validator. */

const STATUSES = new Set(['answered', 'unanswered', 'declined', 'not_applicable', 'skipped']);
const DEFAULT_RELATIONS = ['favors_continue', 'favors_terminate', 'supports_both', 'conflict', 'uncertain'];
const FINAL_CONFIRMATION = 'q38.report_action';
const clone = value => structuredClone(value);
const blank = () => ({ status: 'unanswered', value: null });
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const textLength = value => [...value].length;

export function buildIndex(definition) {
  const index = new Map();
  for (const question of definition.questions) {
    for (const field of question.fields) {
      const ref = `${question.id}.${field.id}`;
      if (index.has(ref)) throw new Error('问卷字段重复。');
      index.set(ref, {
        ...field,
        questionId: question.id,
        questionTitle: question.title,
        sourceNumber: question.source_number,
      });
    }
  }
  return index;
}

export function getAnswer(answers, ref) {
  const [questionId, fieldId] = ref.split('.');
  const fields = answers?.[questionId];
  return fields && Object.prototype.hasOwnProperty.call(fields, fieldId) ? fields[fieldId] : blank();
}

function putAnswer(answers, ref, answer) {
  const [questionId, fieldId] = ref.split('.');
  answers[questionId] ??= {};
  answers[questionId][fieldId] = answer;
}

export function emptyAnswers(definition) {
  return Object.fromEntries(definition.questions.map(question => [
    question.id,
    Object.fromEntries(question.fields.map(field => [field.id, blank()])),
  ]));
}

/** null is an unknown condition and must not be treated as a negative answer. */
export function evaluateVisibility(rule, answers) {
  if (!rule) return true;
  const source = getAnswer(answers, rule.field);
  if (['skipped', 'not_applicable'].includes(source?.status)) return false;
  if (source?.status !== 'answered') return null;
  const actual = source.value;
  switch (rule.operator) {
    case 'equals': return actual === rule.value;
    case 'not_equals': return actual !== rule.value;
    case 'in': return rule.value.includes(actual);
    case 'not_in': return !rule.value.includes(actual);
    case 'contains': return Array.isArray(actual) ? actual.includes(rule.value) : null;
    default: throw new Error('问卷显示条件无效。');
  }
}

export function normalizeAnswers(definition, supplied = {}) {
  const index = buildIndex(definition);
  const answers = emptyAnswers(definition);
  for (const ref of index.keys()) putAnswer(answers, ref, clone(getAnswer(supplied, ref)));
  const visiting = new Set();
  const resolved = new Set();
  function resolve(ref) {
    if (resolved.has(ref)) return;
    if (visiting.has(ref)) throw new Error('问卷显示条件存在循环。');
    const field = index.get(ref);
    if (!field) throw new Error('问卷显示条件引用了不存在的字段。');
    visiting.add(ref);
    const rule = field.visible_when;
    if (rule) resolve(rule.field);
    if (evaluateVisibility(rule, answers) === false) {
      putAnswer(answers, ref, { status: 'skipped', value: null, skip_rule_id: rule.rule_id });
    } else if (getAnswer(answers, ref)?.status === 'skipped') {
      // A newly applicable answer must be entered again, never resurrected.
      putAnswer(answers, ref, blank());
    }
    visiting.delete(ref);
    resolved.add(ref);
  }
  for (const ref of index.keys()) resolve(ref);
  return answers;
}

export function setAnswer(definition, answers, ref, answer) {
  if (!buildIndex(definition).has(ref)) throw new Error('未知问卷字段。');
  const next = clone(answers);
  putAnswer(next, ref, clone(answer));
  if (ref !== FINAL_CONFIRMATION) putAnswer(next, FINAL_CONFIRMATION, blank());
  return normalizeAnswers(definition, next);
}

/** Preserve invalid typed input for an inline error instead of truncating it. */
export function inputAnswer(field, raw) {
  if (raw === null || raw === undefined || (typeof raw === 'string' && raw.trim() === '')) return blank();
  if (field.type === 'integer' && typeof raw === 'string' && /^[+-]?\d+$/.test(raw.trim())) {
    const number = Number(raw);
    return { status: 'answered', value: Number.isSafeInteger(number) ? number : raw };
  }
  return { status: 'answered', value: raw };
}

/** Clicking a mutually exclusive choice replaces the conflicting old choice. */
export function toggleOption(field, selected = [], code) {
  const original = Array.isArray(selected) ? [...selected] : [];
  if (original.includes(code)) return { values: original.filter(item => item !== code) };
  if (!field.options?.some(option => option.code === code)) {
    return { values: original, error: '请选择提供的选项。' };
  }
  const exclusive = new Set(field.exclusive_options ?? []);
  let values = exclusive.has(code) ? [] : original.filter(item => !exclusive.has(item));
  for (const conflict of field.conflicts ?? []) {
    if (conflict.includes(code)) values = values.filter(item => !conflict.includes(item));
  }
  values.push(code);
  if (field.max_selections !== undefined && values.length > field.max_selections) {
    return { values: original, error: `最多选择${field.max_selections}项。` };
  }
  return { values };
}

function validDate(value, monthOnly) {
  if (typeof value !== 'string' || !(monthOnly ? /^\d{4}-\d{2}$/ : /^\d{4}-\d{2}-\d{2}$/).test(value)) return false;
  const [year, month, suppliedDay] = value.split('-').map(Number);
  const day = monthOnly ? 1 : suppliedDay;
  if (year < 1 || year > 9999 || month < 1 || month > 12 || day < 1) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day <= days[month - 1];
}

function allowedReferences(field, index) {
  const business = [...index.keys()].filter(ref => !ref.startsWith('consent.') && (!ref.startsWith('q38.') || ref === 'q38.report_notes'));
  const available = new Set(business);
  return new Set((field.allowed_refs ?? business).filter(ref => available.has(ref)));
}

function validateValue(field, value, ref, index, add) {
  switch (field.type) {
    case 'integer':
      if (!Number.isSafeInteger(value)) add(ref, 'type', '请填写整数。');
      else if ((field.minimum !== undefined && value < field.minimum) || (field.maximum !== undefined && value > field.maximum)) {
        const range = field.maximum === undefined ? `不小于${field.minimum}` : `${field.minimum ?? 0}—${field.maximum}`;
        add(ref, 'range', `请填写${range}的整数。`);
      }
      break;
    case 'date':
    case 'year_month':
      if (!validDate(value, field.type === 'year_month')) add(ref, 'date', field.type === 'date' ? '请填写有效日期。' : '请填写有效年月。');
      break;
    case 'text':
      if (typeof value !== 'string' || !value.trim()) add(ref, 'type', '请填写内容，或清除此项回答。');
      else if (textLength(value) > (field.max_length ?? 4000)) add(ref, 'max_length', `最多填写${field.max_length ?? 4000}字。`);
      break;
    case 'single_select':
      if (typeof value !== 'string' || !field.options.some(option => option.code === value)) add(ref, 'option', '请选择提供的选项。');
      break;
    case 'multi_select':
    case 'field_refs': {
      if (!Array.isArray(value) || value.some(item => typeof item !== 'string')) {
        add(ref, 'type', '请选择有效项目。');
        break;
      }
      if (new Set(value).size !== value.length) add(ref, 'duplicate', '不能重复选择同一项。');
      if (value.length > (field.max_selections ?? index.size)) add(ref, 'max_selections', `最多选择${field.max_selections ?? index.size}项。`);
      if (field.type === 'field_refs') {
        const allowed = allowedReferences(field, index);
        if (value.some(item => !allowed.has(item))) add(ref, 'field_reference', '只能排除问卷中允许选择的回答。');
        break;
      }
      if (!value.length) add(ref, 'empty_selection', '请选择一项，或将此题保留为未答。');
      if (value.some(item => !field.options.some(option => option.code === item))) add(ref, 'option', '包含无效选项。');
      if (value.length > 1 && value.some(item => field.exclusive_options?.includes(item))) add(ref, 'exclusive', '排他选项不能与其他选项同时选择。');
      if ((field.conflicts ?? []).some(conflict => conflict.every(code => value.includes(code)))) add(ref, 'conflict', '这些选项不能同时选择。');
      break;
    }
    case 'ranked_values': {
      if (!Array.isArray(value) || !value.length) {
        add(ref, 'type', '请填写至少一项考虑，或清除此项回答。');
        break;
      }
      if (value.length > (field.max_selections ?? 3)) add(ref, 'max_selections', `最多填写${field.max_selections ?? 3}项考虑。`);
      const relations = new Set(field.item_fields?.find(item => item.id === 'relation')?.options?.map(option => option.code) ?? DEFAULT_RELATIONS);
      const factors = new Set();
      for (const [position, item] of value.entries()) {
        if (!isObject(item) || Object.keys(item).sort().join(',') !== 'conditions,factor,relation') {
          add(ref, 'ranked_item', `请完整填写第${position + 1}项考虑。`);
          continue;
        }
        if (typeof item.factor !== 'string' || !item.factor.trim() || textLength(item.factor) > (field.factor_max_length ?? 500)) {
          add(ref, 'ranked_factor', `请填写第${position + 1}项重要考虑，最多${field.factor_max_length ?? 500}字。`);
        } else if (factors.has(item.factor.trim())) add(ref, 'duplicate', '请勿重复填写同一考虑。');
        else factors.add(item.factor.trim());
        if (typeof item.relation !== 'string' || !relations.has(item.relation)) add(ref, 'ranked_relation', `请选择第${position + 1}项与选择的关系。`);
        if (typeof item.conditions !== 'string' || textLength(item.conditions) > (field.conditions_max_length ?? 2000)) add(ref, 'ranked_conditions', `条件最多填写${field.conditions_max_length ?? 2000}字。`);
      }
      break;
    }
    default: add(ref, 'type', '暂不支持此题型，请重新加载问卷。');
  }
}

/** Validate current state. Optional unanswered fields never block navigation. */
export function validateAnswers(definition, answers, { requireConsent = false, requireFinal = false } = {}) {
  const index = buildIndex(definition);
  const errors = [];
  const add = (field, code, message) => errors.push({ field, code, message });
  const validQuestions = new Set(definition.questions.map(question => question.id));
  if (!isObject(answers)) return [{ field: 'answers', code: 'type', message: '回答格式无效。' }];
  for (const [qid, values] of Object.entries(answers)) {
    if (!validQuestions.has(qid)) add('answers', 'unknown_question', '包含未知题目。');
    else if (!isObject(values)) add(qid, 'type', '题目回答格式无效。');
    else for (const fid of Object.keys(values)) if (!index.has(`${qid}.${fid}`)) add(qid, 'unknown_field', '包含未知回答字段。');
  }
  for (const [ref, field] of index) {
    const answer = getAnswer(answers, ref);
    if (!isObject(answer) || !('status' in answer) || !('value' in answer)) {
      add(ref, 'answer_shape', '回答格式无效。');
      continue;
    }
    if (Object.keys(answer).some(key => !['status', 'value', 'skip_rule_id'].includes(key))) add(ref, 'unknown_field', '回答包含未知属性。');
    if (!STATUSES.has(answer.status)) {
      add(ref, 'status', '回答状态无效。');
      continue;
    }
    if (answer.status === 'answered') validateValue(field, answer.value, ref, index, add);
    else if (answer.value !== null) add(ref, 'null_required', '未作答状态不能保留回答内容。');
    if (answer.status === 'not_applicable' && !field.allow_not_applicable) add(ref, 'not_applicable', '此项不提供不适用选项。');
    const visible = evaluateVisibility(field.visible_when, answers);
    if (answer.status === 'skipped') {
      if (visible !== false || answer.skip_rule_id !== field.visible_when?.rule_id) add(ref, 'invalid_skip', '跳题状态已变化，请重新核对。');
    } else if ('skip_rule_id' in answer) add(ref, 'skip_rule', '此项不应包含跳题标识。');
  }
  if (requireConsent || requireFinal) {
    const use = getAnswer(answers, 'consent.report_use');
    const privacy = getAnswer(answers, 'consent.private_completion');
    if (use?.status !== 'answered' || use.value !== 'agree') add('consent.report_use', 'consent_required', '请先确认是否同意本次问卷用途。');
    if (privacy?.status !== 'answered' || !['comfortable', 'assisted'].includes(privacy.value)) add('consent.private_completion', 'privacy_required', '请在方便按本人意愿填写时继续。');
  }
  if (requireFinal) {
    const confirmation = getAnswer(answers, FINAL_CONFIRMATION);
    if (confirmation?.status !== 'answered' || confirmation.value !== 'confirm') add(FINAL_CONFIRMATION, 'confirmation_required', '请核对本次回答并确认提交。');
  }
  return errors;
}

export function makePayload(definition, answers) {
  return {
    schema_version: definition.schema_version,
    questionnaire_id: definition.questionnaire_id,
    questionnaire_version: definition.version,
    answers: normalizeAnswers(definition, answers),
  };
}

export function labelAnswer(definition, ref, answer) {
  const field = buildIndex(definition).get(ref);
  if (!field) return '未知题目';
  const statusLabels = { unanswered: '未回答', declined: '不愿回答', not_applicable: '不适用', skipped: '按条件跳过' };
  if (!answer || answer.status !== 'answered') return statusLabels[answer?.status] ?? '未回答';
  const optionLabel = code => field.options?.find(option => option.code === code)?.label ?? String(code);
  if (field.type === 'single_select') return optionLabel(answer.value);
  if (field.type === 'multi_select') return Array.isArray(answer.value) ? answer.value.map(optionLabel).join('、') : '回答格式有误';
  if (field.type === 'field_refs') {
    const index = buildIndex(definition);
    return Array.isArray(answer.value) ? (answer.value.map(item => {
      const target = index.get(item);
      return target ? `${target.sourceNumber ? `第${target.sourceNumber}题 · ` : ''}${target.label}` : '未知回答';
    }).join('；') || '无额外排除要求') : '回答格式有误';
  }
  if (field.type === 'ranked_values') {
    const relationLabels = new Map((field.item_fields?.find(item => item.id === 'relation')?.options ?? []).map(option => [option.code, option.label]));
    return Array.isArray(answer.value) ? answer.value.map((item, i) => `${i + 1}. ${item.factor || '尚未填写考虑'}（${relationLabels.get(item.relation) ?? '尚不清楚'}）${item.conditions ? `；条件：${item.conditions}` : ''}`).join('\n') : '回答格式有误';
  }
  return String(answer.value);
}
