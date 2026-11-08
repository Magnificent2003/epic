import {
  buildIndex, emptyAnswers, normalizeAnswers, setAnswer, toggleOption,
  validateAnswers, makePayload, labelAnswer, getAnswer, inputAnswer,
} from './questionnaire.js';

const root = document.querySelector('#app');
const announceNode = document.querySelector('#announcement');
const icons = {
  arrow: '<path d="M4 12h15m-5-5 5 5-5 5"/>',
  back: '<path d="M20 12H5m5-5-5 5 5 5"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  shield: '<path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  leaf: '<path d="M20 4C6 1 2 8 5 15c2 5 12 6 15-11Z"/><path d="M5 21c0-7 5-11 10-13"/>',
  check: '<path d="m5 12 4.5 4.5L19 7"/>',
  download: '<path d="M12 3v12m-4-4 4 4 4-4M5 16v4h14v-4"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
};
const stageTitles = ['开始之前', '基本信息', '现实资源', '本人意愿', '经历与规划', '就医与信息', '关系与支持', '当前状态', '核对与提交'];
const state = { definition: null, index: null, answers: null, stage: 0, visited: new Set([0]), mode: 'form', errors: [], busy: false, submissionId: null, record: null, pauseFromConsent: false };
let toastTimer;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'on') for (const [event, handler] of Object.entries(value)) node.addEventListener(event, handler);
    else if (key === 'checked' || key === 'disabled' || key === 'selected' || key === 'hidden') node[key] = value;
    else node.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat()) if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}
function icon(name) {
  const span = el('span', { 'aria-hidden': 'true' });
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${icons[name] || icons.leaf}</svg>`;
  return span;
}
function button(text, fn, className = 'button', options = {}) {
  return el('button', { type: 'button', class: className, disabled: state.busy, on: { click: fn }, ...options }, text);
}
function announce(message) { announceNode.textContent = message; }
function toast(message) {
  document.querySelector('.toast')?.remove();
  const node = el('div', { class: 'toast', role: 'status' }, message);
  document.body.append(node);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.remove(), 4000);
  announce(message);
}
function currentAnswer(ref) { return getAnswer(state.answers, ref); }
function value(ref) { const answer = currentAnswer(ref); return answer.status === 'answered' ? answer.value : null; }
function allowedToFill() { return value('consent.report_use') === 'agree' && ['comfortable', 'assisted'].includes(value('consent.private_completion')); }
function clearConfirmation() { state.answers.q38.report_action = { status: 'unanswered', value: null }; }
function shortSection(section) { return section.title.replace(/^[一二三四五六七]\s*/, '').split(/[　]/)[0]; }
function refId(ref) { return `field-${ref.replaceAll('.', '-')}`; }
function cleanHelp(text = '') {
  return text.split(/(?<=[。；])/).filter(sentence => !/本版|开发阶段|后端|JSON|PDF|字段|系统不|不生成去留评分|不用于计算去留评分|本次实际读取/.test(sentence)).join('').trim();
}
function captureUI() {
  const active = document.activeElement;
  return { id: active?.id, start: active?.selectionStart, end: active?.selectionEnd, scroll: window.scrollY, details: [...document.querySelectorAll('details[open]')].map(n => n.id), exclusionScroll: document.querySelector('.exclusions')?.scrollTop };
}
function restoreUI(saved) {
  for (const id of saved.details) { const node = document.getElementById(id); if (node) node.open = true; }
  const exclusions = document.querySelector('.exclusions');
  if (exclusions && saved.exclusionScroll != null) exclusions.scrollTop = saved.exclusionScroll;
  const active = saved.id && document.getElementById(saved.id);
  if (active) {
    active.focus({ preventScroll: true });
    if (typeof saved.start === 'number' && active.setSelectionRange) { try { active.setSelectionRange(saved.start, saved.end); } catch {} }
  }
  window.scrollTo({ top: saved.scroll, behavior: 'instant' });
}
function updateAnswer(ref, answer) {
  const saved = captureUI();
  state.answers = setAnswer(state.definition, state.answers, ref, answer);
  state.errors = state.errors.filter(error => error.field !== ref && error.field !== 'q38.report_action');
  render();
  restoreUI(saved);
}
// Native date controls keep the active day/month/year segment internally.
// Replacing their DOM node on input would move keyboard focus to another segment.
function updateNativeDate(ref, answer) {
  state.answers = setAnswer(state.definition, state.answers, ref, answer);
  state.errors = state.errors.filter(error => error.field !== ref && error.field !== 'q38.report_action');
  const group = document.getElementById(refId(ref));
  group?.classList.remove('has-error');
  group?.removeAttribute('aria-describedby');
  group?.querySelector('[aria-invalid]')?.removeAttribute('aria-invalid');
  document.getElementById(`${refId(ref)}-error`)?.remove();
  const field = state.index.get(ref);
  if (group && !field.required) {
    const tools = renderFieldTools(ref, field, currentAnswer(ref));
    const previous = group.querySelector('.field-tools');
    if (previous) previous.replaceWith(tools);
    else group.append(tools);
  }
  const banner = document.querySelector('.error-banner');
  if (banner) {
    const updated = errorBanner();
    if (updated) banner.replaceWith(updated);
    else banner.remove();
  }
}

function revealError(error) {
  const [qid] = error.field.split('.');
  if (qid === 'q38') state.stage = 8;
  else { const stage = state.definition.sections.findIndex(section => section.question_ids.includes(qid)); if (stage >= 0) state.stage = stage; }
  state.mode = 'form';
  render();
  requestAnimationFrame(() => {
    const target = document.getElementById(refId(error.field));
    if (target) { target.scrollIntoView({ block: 'center', behavior: 'smooth' }); target.querySelector('input, textarea, select, button')?.focus({ preventScroll: true }); }
  });
}
function visibleErrorsForStage() {
  const refs = stageQuestionIds().flatMap(qid => state.definition.questions.find(q => q.id === qid).fields.map(field => `${qid}.${field.id}`));
  return validateAnswers(state.definition, state.answers).filter(error => refs.includes(error.field));
}
function navigate(stage) {
  if (state.busy || stage === state.stage) return;
  if (stage > 0 && !allowedToFill()) { toast('请先完成自愿填写与私密环境确认。'); return; }
  const errors = visibleErrorsForStage();
  if (errors.length) { state.errors = errors; render(); revealError(errors[0]); return; }
  state.stage = stage; state.visited.add(stage); state.errors = [];
  if (stage !== 8) clearConfirmation();
  render();
  document.querySelector('#main')?.focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
function stageQuestionIds() {
  if (state.stage === 8) return ['q38'];
  return state.definition.sections[state.stage].question_ids.filter(qid => qid !== 'q38');
}
function begin() {
  if (value('consent.report_use') === 'disagree') { stop(); return; }
  if (value('consent.private_completion') === 'uncomfortable') { state.pauseFromConsent = true; pause(); return; }
  const errors = validateAnswers(state.definition, state.answers, { requireConsent: true }).filter(error => error.field.startsWith('consent.'));
  if (errors.length || !allowedToFill()) {
    state.errors = errors.length ? errors : [{ field: 'consent.report_use', code: 'required', message: '请先完成两项填写确认。' }]; render(); return;
  }
  navigate(1);
}
function pause() { if (state.busy) return; state.mode = 'paused'; clearConfirmation(); render(); window.scrollTo(0, 0); }
function resume() {
  state.mode = 'form';
  if (state.pauseFromConsent) { state.stage = 0; state.answers.consent.private_completion = { status: 'unanswered', value: null }; state.pauseFromConsent = false; }
  render();
}
function stop() {
  if (state.busy) return;
  state.answers = emptyAnswers(state.definition); state.submissionId = null; state.record = null; state.errors = []; state.stage = 0; state.visited = new Set([0]); state.mode = 'stopped';
  render(); window.scrollTo(0, 0);
}
function sidebar() {
  const list = el('ol', { class: 'step-list' });
  stageTitles.forEach((title, stage) => {
    const active = state.stage === stage && state.mode === 'form';
    list.append(el('li', {}, button([
      el('span', { class: 'step-dot', 'aria-hidden': 'true' }, state.visited.has(stage) && stage < state.stage ? '✓' : String(stage + 1).padStart(2, '0')),
      el('span', {}, title),
    ], () => navigate(stage), `step-button${active ? ' active' : ''}${state.visited.has(stage) ? ' visited' : ''}`, { disabled: state.busy || state.mode !== 'form' || (stage > 0 && !allowedToFill()), 'aria-current': active ? 'step' : null })));
  });
  return el('aside', { class: 'sidebar', 'aria-label': '问卷阶段' },
    el('div', { class: 'brand' }, el('span', { class: 'brand-symbol', 'aria-hidden': 'true' }), el('span', { class: 'brand-word' }, 'EPIC')),
    el('p', { class: 'brand-subtitle' }, '妊娠选择 · 决策支持'),
    el('p', { class: 'sidebar-label' }, '你的填写旅程'),
    el('nav', {}, list),
    el('div', { class: 'sidebar-note' }, icon('leaf'), el('p', {}, '不必急着作出决定。'), el('p', {}, '这里先听见你的想法。')),
  );
}
function topbar() {
  const actions = el('div', { class: 'topbar-actions' });
  if (state.mode === 'form') {
    actions.append(button([icon('download'), '保存草稿'], exportDraft, 'quiet-button draft-save'));
    actions.append(button([icon('pause'), '暂时离开'], () => { state.pauseFromConsent = false; pause(); }, 'quiet-button'));
    actions.append(button('退出', stop, 'quiet-button'));
  }
  return el('header', { class: 'topbar' }, el('span', { class: 'topbar-title' }, '妊娠选择支持问卷'), actions);
}
function heading() {
  const stage = state.stage;
  const titles = ['从了解自己开始', '先了解你的基本情况', '看看身边可用的资源', '听见你自己的想法', '把选择放回你的生活', '整理信息与就医安排', '了解关系中的支持与压力', '此刻，你最需要什么', '给自己的回答，再看一眼'];
  const descriptions = [
    '这份问卷陪你整理意愿、现实条件和支持需求。没有标准答案，也不要求你现在作出决定。',
    '只填写你已经知道的信息。不清楚的部分可以留空，无需自行判断医学风险。',
    '关注实际可使用的资源和已落实的帮助。每一项困难，都可以成为寻求支持的起点。',
    '期待、担忧和矛盾可以同时存在。请按自己的想法回答，不需要迎合任何人的期待。',
    '每个人都有自己的经历和生活计划。这里没有需要被证明“正确”的答案。',
    '了解哪些信息已经明确，哪些还需要解释；把眼前的实际困难一项项梳理出来。',
    '没有伴侣并不意味着没有支持。请在安全、私密的环境下，按自己的意愿回答。',
    '按此刻的感受填写。无论是否已经决定，你都可以表达自己的需要。',
    '先设置回答的使用范围，再核对内容。你可以返回任何一节修改，未回答的部分会保留为空。',
  ];
  return el('div', { class: 'page-heading' },
    el('p', { class: 'eyebrow' }, stage === 0 ? '由你决定，按你的节奏' : `第 ${String(stage + 1).padStart(2, '0')} 步 · ${stageTitles[stage]}`),
    el('h1', { id: 'page-title' }, titles[stage]), el('p', { class: 'page-description' }, descriptions[stage]),
    el('div', { class: 'progress-row' }, el('span', {}, stageTitles[stage]), el('span', {}, `${stage + 1} / ${stageTitles.length} 个阶段`)),
    el('div', { class: 'progress-track', role: 'progressbar', 'aria-label': '当前填写阶段', 'aria-valuemin': 1, 'aria-valuemax': 9, 'aria-valuenow': stage + 1 }, el('div', { class: 'progress-fill', style: `width:${((stage + 1) / 9) * 100}%` })),
  );
}
function mobileNavigation() {
  const select = el('select', { 'aria-label': '跳转填写阶段', on: { change: event => navigate(Number(event.target.value)) } });
  stageTitles.forEach((title, stage) => select.append(el('option', { value: stage, selected: state.stage === stage, disabled: stage > 0 && !allowedToFill() }, `${String(stage + 1).padStart(2, '0')}  ${title}`)));
  return el('div', { class: 'mobile-steps' }, select);
}
function errorBanner() {
  if (!state.errors.length) return null;
  const banner = el('div', { class: 'error-banner', role: 'alert' }, el('p', {}, '还有几处需要核对，你的回答已保留在当前页面。'));
  for (const error of state.errors.slice(0, 6)) {
    const field = state.index.get(error.field);
    banner.append(button(`${field?.label || '提交'}：${error.message}`, () => revealError(error), ''));
  }
  return banner;
}
function questionCard(question) {
  const fields = question.fields.filter(field => currentAnswer(`${question.id}.${field.id}`).status !== 'skipped');
  if (!fields.length) return el('article', { class: 'question-card', 'data-question': question.id },
    el('div', { class: 'question-title' }, el('span', { class: 'question-number' }, String(question.source_number).padStart(2, '0')), el('h2', {}, question.title)),
    el('p', { class: 'question-outro' }, '根据前面的回答，这一题无需填写。'));
  const card = el('article', { class: 'question-card', 'data-question': question.id });
  card.append(el('div', { class: 'question-title' }, el('span', { class: 'question-number', 'aria-hidden': 'true' }, question.id === 'consent' ? '✓' : String(question.source_number).padStart(2, '0')), el('h2', {}, question.title)));
  if (cleanHelp(question.intro)) card.append(el('p', { class: 'question-intro' }, cleanHelp(question.intro)));
  for (const field of fields) card.append(renderField(question, field));
  const outro = cleanHelp(question.outro);
  if (outro && question.id !== 'consent') {
    if (question.id !== 'q03' || value('q03.pregnancy_status') === 'suspected_or_confirmed_ectopic') card.append(el('p', { class: `question-outro${question.id === 'q03' ? ' medical-note' : ''}` }, outro));
  }
  return card;
}
function renderFieldTools(ref, field, answer) {
  const tools = el('div', { class: 'field-tools' }, button('不愿回答', () => updateAnswer(ref, { status: 'declined', value: null }), ''));
  if (field.allow_not_applicable) tools.append(button('不适用', () => updateAnswer(ref, { status: 'not_applicable', value: null }), ''));
  if (answer.status === 'answered') tools.append(button('清空这项', () => updateAnswer(ref, { status: 'unanswered', value: null }), ''));
  return tools;
}

function renderField(question, field) {
  const ref = `${question.id}.${field.id}`;
  const answer = currentAnswer(ref);
  const errors = state.errors.filter(error => error.field === ref);
  const group = el('fieldset', { class: `field${errors.length ? ' has-error' : ''}`, id: refId(ref), 'data-field': ref, 'data-conditional': field.visible_when ? 'true' : null, 'aria-describedby': errors.length ? `${refId(ref)}-error` : null });
  const typeNames = { single_select: '单选', multi_select: field.max_selections ? `最多选 ${field.max_selections} 项` : '多选', ranked_values: '最多 3 项', text: '补充说明', field_refs: '按需选择' };
  const tag = `${field.required ? '必填' : '选填'}${typeNames[field.type] ? ` · ${typeNames[field.type]}` : ''}`;
  group.append(el('legend', { class: 'field-legend' }, field.label, el('span', { class: 'field-type' }, tag)));
  let help = cleanHelp(field.help);
  if (ref === 'consent.report_use') help = '回答仅用于本次个人支持。后续报告将使用云端模型分析获准使用的回答，你可以在提交前指定排除内容。当前先保存问卷，尚不生成报告。研究、对外共享或模型训练需另行征求同意。';
  if (ref === 'q17.value_priorities') help = '请按对你而言的重要程度排序。每项写下重要考虑、它与你的选择的关系，以及希望满足的条件。不确定顺序时可以暂不填写。';
  if (field.type === 'field_refs') help = '选中的回答及依赖它的补充，不会用于后续模型分析或报告展示；仍保留在原始答卷中。其他独立补充请另行选择。没有额外要求可以留空。';
  if (ref === 'q38.exclusion_notes') help = '如果上述选择还不能表达你的要求，可以补充说明。生成报告前需要先澄清并落实。';
  if (help) group.append(el('p', { class: 'field-help', id: `${refId(ref)}-help` }, help));
  if (answer.status === 'declined' || answer.status === 'not_applicable') {
    group.append(el('div', { class: 'field-state' }, answer.status === 'declined' ? '已选择不愿回答' : '已选择不适用', button('重新填写', () => updateAnswer(ref, { status: 'unanswered', value: null }), '')));
  } else if (field.type === 'single_select' || field.type === 'multi_select') {
    const options = el('div', { class: `options${question.id === 'consent' || ref === 'q38.report_action' ? ' single-column' : ''}` });
    for (const [i, option] of field.options.entries()) {
      const selected = answer.status === 'answered' && (field.type === 'multi_select' ? answer.value.includes(option.code) : answer.value === option.code);
      const control = el('input', { type: field.type === 'multi_select' ? 'checkbox' : 'radio', id: `${refId(ref)}-${i}`, name: ref, value: option.code, checked: selected, disabled: state.busy, on: { change: event => {
        if (field.type === 'single_select') updateAnswer(ref, { status: 'answered', value: option.code });
        else {
          const result = toggleOption(field, answer.status === 'answered' ? answer.value : [], option.code);
          if (result.error) { event.target.checked = selected; toast(typeof result.error === 'string' ? result.error : result.error.message); return; }
          updateAnswer(ref, result.values.length ? { status: 'answered', value: result.values } : { status: 'unanswered', value: null });
        }
      } } });
      options.append(el('label', { class: `option${selected ? ' selected' : ''}`, for: control.id }, control, el('span', {}, option.label)));
    }
    group.append(options);
  } else if (field.type === 'ranked_values') group.append(renderRanked(ref, field, answer));
  else if (field.type === 'field_refs') group.append(renderExclusions(ref, field, answer));
  else {
    const kind = field.type === 'text' ? 'textarea' : 'input';
    const nativeDate = field.type === 'date' || field.type === 'year_month';
    const onValueInput = event => {
      if (event.isComposing) return;
      // A partially edited native date has value="" and badInput=true. Keep it
      // invalid in state so leaving the step or exporting cannot treat it as blank.
      const answer = nativeDate && event.target.validity.badInput
        ? { status: 'answered', value: event.target.value }
        : inputAnswer(field, event.target.value);
      if (nativeDate) updateNativeDate(ref, answer);
      else updateAnswer(ref, answer);
    };
    const events = { input: onValueInput, compositionend: onValueInput };
    if (nativeDate) events.change = onValueInput;
    const control = el(kind, { id: `${refId(ref)}-input`, name: ref, class: `text-control${field.type !== 'text' ? ' short' : ''}`, type: field.type === 'integer' ? 'number' : field.type === 'date' ? 'date' : field.type === 'year_month' ? 'month' : null, min: field.minimum, max: field.maximum, step: field.type === 'integer' ? '1' : null, inputmode: field.type === 'integer' ? 'numeric' : null, maxlength: field.max_length || null, placeholder: field.type === 'text' ? '愿意的话，可以在这里补充…' : field.type === 'integer' ? '请填写' : null, disabled: state.busy, 'aria-label': field.label, 'aria-invalid': errors.length ? 'true' : null, 'aria-describedby': help ? `${refId(ref)}-help` : null, on: events });
    control.value = answer.status === 'answered' ? answer.value : '';
    group.append(control);
    if (field.type === 'text') group.append(el('p', { class: 'control-meta' }, `${control.value.length} / ${field.max_length || 2000}`));
  }
  if (!field.required && answer.status !== 'declined' && answer.status !== 'not_applicable') {
    group.append(renderFieldTools(ref, field, answer));
  }
  if (errors.length) group.append(el('p', { class: 'field-error', id: `${refId(ref)}-error` }, errors.map(error => error.message).join(' ')));
  return group;
}
function renderRanked(ref, field, answer) {
  const values = answer.status === 'answered' ? answer.value : [];
  const wrap = el('div');
  const set = values => updateAnswer(ref, values.length ? { status: 'answered', value: values } : { status: 'unanswered', value: null });
  const list = el('div', { class: 'rank-list' });
  const relationOptions = field.item_fields.find(item => item.id === 'relation').options;
  for (const [position, item] of values.entries()) {
    const card = el('div', { class: 'rank-item', 'data-rank': position });
    const actions = el('div', { class: 'rank-actions' });
    const move = offset => { const reordered = structuredClone(values); [reordered[position], reordered[position + offset]] = [reordered[position + offset], reordered[position]]; set(reordered); };
    actions.append(button('上移', () => move(-1), '', { disabled: state.busy || position === 0, 'aria-label': `将第${position + 1}项上移` }), button('下移', () => move(1), '', { disabled: state.busy || position === values.length - 1, 'aria-label': `将第${position + 1}项下移` }), button('删除', () => set(values.filter((_, i) => i !== position)), '', { 'aria-label': `删除第${position + 1}项` }));
    card.append(el('div', { class: 'rank-heading' }, el('span', { class: 'rank-position' }, `第 ${position + 1} 位 · 重要考虑`), actions));
    for (const key of ['factor', 'relation', 'conditions']) {
      const labels = { factor: '这项考虑是什么', relation: '你认为它与选择的关系', conditions: '希望满足的条件（选填）' };
      const id = `${refId(ref)}-${position}-${key}`;
      const change = event => { if (event.isComposing) return; const updated = structuredClone(values); updated[position][key] = event.target.value; set(updated); };
      const control = key === 'relation'
        ? el('select', { class: 'text-control', id, disabled: state.busy, on: { change } }, relationOptions.map(option => el('option', { value: option.code, selected: item.relation === option.code }, option.label)))
        : el(key === 'conditions' ? 'textarea' : 'input', { class: 'text-control', id, type: key === 'factor' ? 'text' : null, disabled: state.busy, maxlength: key === 'factor' ? field.factor_max_length : field.conditions_max_length, placeholder: key === 'factor' ? '写下对你重要的一点' : '如果有需要满足的条件，可以写在这里', on: { input: change, compositionend: change } });
      if (key !== 'relation') control.value = item[key];
      card.append(el('label', { class: 'rank-label', for: id }, labels[key]), control);
    }
    list.append(card);
  }
  wrap.append(list);
  if (!values.length) wrap.append(el('p', { class: 'rank-empty' }, '你可以选出最多三项重要考虑，按自己的优先顺序排列。'));
  if (values.length < (field.max_selections || 3)) wrap.append(button('+ 添加一项重要考虑', () => { set([...values, { factor: '', relation: 'uncertain', conditions: '' }]); document.getElementById(`${refId(ref)}-${values.length}-factor`)?.focus(); }, 'add-value'));
  return wrap;
}
function renderExclusions(ref, field, answer) {
  const selected = new Set(answer.status === 'answered' ? answer.value : []);
  const allowed = new Set(field.allowed_refs);
  const groups = el('div', { class: 'exclusions' });
  function setSelection(refs, checked) {
    const next = new Set(selected);
    for (const fieldRef of refs) checked ? next.add(fieldRef) : next.delete(fieldRef);
    updateAnswer(ref, { status: 'answered', value: [...next] });
  }
  for (const question of state.definition.questions) {
    const fields = question.fields.filter(f => allowed.has(`${question.id}.${f.id}`));
    if (!fields.length) continue;
    const refs = fields.map(f => `${question.id}.${f.id}`);
    const picked = refs.filter(fieldRef => selected.has(fieldRef)).length;
    const details = el('details', { class: 'exclusion-group', id: `exclude-group-${question.id}` });
    details.append(el('summary', {}, `${question.source_number}  ${question.title}${picked ? ` · 已选 ${picked} 项` : ''}`));
    const rows = el('div', { class: 'exclusion-fields' });
    const allId = `exclude-all-${question.id}`;
    const all = el('input', { type: 'checkbox', id: allId, checked: picked === fields.length, disabled: state.busy, on: { change: event => setSelection(refs, event.target.checked) } });
    all.indeterminate = picked > 0 && picked < fields.length;
    rows.append(el('label', { class: 'option all-fields', for: allId }, all, '选择本题全部回答'));
    for (const item of fields) {
      const fieldRef = `${question.id}.${item.id}`;
      const id = `exclude-${fieldRef.replaceAll('.', '-')}`;
      rows.append(el('label', { class: `option${selected.has(fieldRef) ? ' selected' : ''}`, for: id }, el('input', { type: 'checkbox', id, checked: selected.has(fieldRef), disabled: state.busy, on: { change: event => setSelection([fieldRef], event.target.checked) } }), el('span', {}, item.label)));
    }
    details.append(rows); groups.append(details);
  }
  return el('div', {}, el('div', { class: 'exclusion-header' }, el('span', {}, selected.size ? `已选择 ${selected.size} 项回答` : '暂无额外排除要求'), el('span', {}, '展开题目选择')), groups);
}
function reviewSummary() {
  const wrap = el('div', {}, el('h2', { class: 'review-title' }, '核对你的回答'));
  for (const [stage, section] of state.definition.sections.entries()) {
    if (stage === 0) continue;
    const questions = section.question_ids.filter(id => id !== 'q38').map(id => state.definition.questions.find(q => q.id === id));
    const answered = questions.reduce((total, q) => total + q.fields.filter(f => currentAnswer(`${q.id}.${f.id}`).status === 'answered').length, 0);
    const details = el('details', { class: 'review-section', id: `review-${section.id}` }, el('summary', {}, shortSection(section), el('span', {}, answered ? `${answered} 项已填写` : '尚未填写')));
    const content = el('div', { class: 'review-content' });
    for (const question of questions) {
      const answeredFields = question.fields.filter(f => !['unanswered', 'skipped'].includes(currentAnswer(`${question.id}.${f.id}`).status));
      const block = el('div', { class: 'review-question' }, el('h3', {}, `${question.source_number}  ${question.title}`));
      if (!answeredFields.length) block.append(el('p', { class: 'review-empty' }, question.fields.every(f => currentAnswer(`${question.id}.${f.id}`).status === 'skipped') ? '按条件跳过' : '尚未填写'));
      else for (const field of answeredFields) {
        const ref = `${question.id}.${field.id}`;
        block.append(el('dl', { class: 'review-row' }, el('dt', {}, field.label), el('dd', {}, labelAnswer(state.definition, ref, currentAnswer(ref)))));
      }
      content.append(block);
    }
    content.append(button('返回这一节修改', () => navigate(stage), 'review-edit'));
    details.append(content); wrap.append(details);
  }
  return wrap;
}
function renderReview() {
  const question = state.definition.questions.find(q => q.id === 'q38');
  const settings = el('article', { class: 'question-card', 'data-question': 'q38' }, el('div', { class: 'question-title' }, el('span', { class: 'question-number' }, '38'), el('h2', {}, '回答的使用与补充')));
  for (const field of question.fields.filter(f => f.id !== 'report_action')) settings.append(renderField(question, field));
  const confirmation = el('article', { class: 'question-card confirmation-card' }, renderField(question, question.fields.find(f => f.id === 'report_action')));
  return [settings, reviewSummary(), confirmation];
}
async function submit() {
  if (state.busy) return;
  const action = value('q38.report_action');
  if (action === 'stop') { stop(); return; }
  if (action === 'edit') { clearConfirmation(); navigate(1); return; }
  const errors = validateAnswers(state.definition, state.answers, { requireConsent: true, requireFinal: true });
  if (errors.length) { state.errors = errors; render(); revealError(errors[0]); return; }
  state.busy = true; state.errors = []; render();
  try {
    const route = state.submissionId ? `/api/submissions/${encodeURIComponent(state.submissionId)}/revisions` : '/api/submissions';
    const response = await fetch(route, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(makePayload(state.definition, state.answers)) });
    const result = await response.json();
    if (!response.ok) {
      state.errors = result.errors || [{ field: 'q38.report_action', code: 'request', message: result.error?.message || '提交暂未成功，请稍后重试。' }];
      return;
    }
    if (result.action === 'submitted') { state.record = result.submission; state.submissionId = result.submission.submission_id; state.answers = normalizeAnswers(state.definition, result.submission.answers); state.mode = 'success'; }
    else if (result.action === 'pause') { state.mode = 'paused'; state.pauseFromConsent = true; }
    else if (result.action === 'stop') { state.busy = false; stop(); return; }
    else { state.stage = 1; clearConfirmation(); }
  } catch {
    state.errors = [{ field: 'q38.report_action', code: 'network', message: '暂时无法连接，请检查服务后重试。你的回答仍保留在当前页面。' }];
  } finally {
    state.busy = false;
    if (state.errors.length) revealError(state.errors[0]);
    else { render(); window.scrollTo({ top: 0, behavior: 'smooth' }); }
  }
}
function downloadJSON(data, name) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = el('a', { href: url, download: name }); document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function exportDraft() {
  const errors = validateAnswers(state.definition, state.answers);
  if (errors.length) { state.errors = errors; render(); revealError(errors[0]); toast('请先核对已经填写的内容，再保存草稿。'); return; }
  const answers = structuredClone(state.answers);
  answers.q38.report_action = { status: 'unanswered', value: null };
  downloadJSON({ draft_type: 'epic_questionnaire', payload: makePayload(state.definition, answers) }, 'EPIC-问卷草稿.json');
  toast('草稿已下载，请保存在你方便且私密的位置。');
}
function chooseDraft() {
  const input = el('input', { type: 'file', accept: '.json,application/json', class: 'sr-only' });
  input.addEventListener('change', async () => {
    try {
      const file = input.files?.[0]; if (!file) return;
      if (file.size > 1024 * 1024) throw new Error('文件过大，请选择由本问卷保存的草稿。');
      const data = JSON.parse(await file.text());
      const payload = data?.draft_type === 'epic_questionnaire' ? data.payload : null;
      if (!payload || payload.questionnaire_id !== state.definition.questionnaire_id || payload.questionnaire_version !== state.definition.version || payload.schema_version !== state.definition.schema_version) throw new Error('这份草稿的格式或问卷版本不匹配。');
      const errors = validateAnswers(state.definition, payload.answers);
      if (errors.length) throw new Error('草稿中有不符合当前问卷的回答，请检查文件。');
      state.answers = normalizeAnswers(state.definition, payload.answers);
      state.answers.consent.report_use = { status: 'unanswered', value: null };
      state.answers.consent.private_completion = { status: 'unanswered', value: null };
      clearConfirmation(); state.stage = 0; state.mode = 'form'; state.errors = []; state.record = null; state.submissionId = null; state.visited = new Set([0]); render();
      toast('草稿已恢复。请重新确认填写意愿与当前环境。');
    } catch (error) { toast(error instanceof SyntaxError ? '无法读取这份草稿，请选择有效的问卷草稿文件。' : error.message); }
    finally { input.remove(); }
  });
  document.body.append(input); input.click();
}
function statusScreen() {
  if (state.mode === 'paused') return el('main', { class: 'status-screen', id: 'main', tabindex: '-1' },
    el('div', { class: 'status-symbol' }, icon('pause')), el('h1', {}, '先停一会儿，也没关系'),
    el('p', {}, '你的回答已暂时隐藏，仍保留在当前页面。等到方便、私密的时候，再回来继续。'),
    el('p', {}, '关闭或刷新页面会清空尚未提交的内容。'),
    button('继续填写', resume, 'button primary'), button('结束本次填写', stop, 'button'),
  );
  if (state.mode === 'stopped') return el('main', { class: 'status-screen', id: 'main', tabindex: '-1' },
    el('div', { class: 'status-symbol' }, icon('leaf')), el('h1', {}, '本次填写已结束'),
    el('p', {}, '当前页面的回答已清空，没有提交新的答卷。你可以关闭页面，或在准备好后重新开始。'),
    button('重新开始', () => { state.mode = 'form'; render(); }, 'button primary'),
  );
  const record = state.record;
  const time = new Date(record.submitted_at).toLocaleString('zh-CN', { hour12: false });
  return el('main', { class: 'status-screen', id: 'main', tabindex: '-1' },
    el('div', { class: 'status-symbol' }, icon('check')), el('h1', {}, '你的答卷已保存'),
    el('p', {}, '谢谢你认真听见自己的想法。你可以下载这份答卷，也可以继续修改；再次提交时会保存为新的版本。'),
    el('div', { class: 'submission-meta' }, el('div', {}, '当前版本 ', el('strong', {}, `第 ${record.revision} 版`)), el('div', {}, '保存时间 ', el('strong', {}, time))),
    button([icon('download'), '下载答卷'], () => downloadJSON(record, `EPIC-答卷-v${record.revision}.json`), 'button primary'),
    button('返回修改', () => { clearConfirmation(); state.stage = 8; state.mode = 'form'; state.errors = []; render(); window.scrollTo(0, 0); }, 'button'),
    el('p', { class: 'footer-note' }, '当前完成问卷保存，个人支持报告尚未生成。'),
  );
}
function render() {
  if (!state.definition) return;
  const workspace = el('div', { class: 'workspace' }, topbar());
  if (state.mode !== 'form') workspace.append(statusScreen());
  else {
    const main = el('main', { class: 'main-content', id: 'main', tabindex: '-1', 'aria-labelledby': 'page-title', 'aria-busy': state.busy ? 'true' : null }, heading(), mobileNavigation());
    if (state.stage === 0) main.append(el('div', { class: 'intro-banner' }, el('div', {}, el('h2', { class: 'intro-banner-title' }, '你可以选择回答多少'), el('p', {}, '除必要确认外，所有问题都可选填。'), el('p', {}, '你可以跳过、修改，也可以随时暂停。')), el('div', { class: 'intro-decoration' }, icon('leaf'))));
    else if (state.stage < 8) main.append(el('p', { class: 'section-kicker' }, icon('shield'), '这一节的问题均为选填，请按自己的意愿回答。'));
    const banner = errorBanner(); if (banner) main.append(banner);
    if (state.stage === 8) main.append(...renderReview());
    else for (const qid of stageQuestionIds()) main.append(questionCard(state.definition.questions.find(question => question.id === qid)));
    const actions = el('div', { class: 'page-actions' });
    if (state.stage > 0) actions.append(button([icon('back'), '上一节'], () => navigate(state.stage - 1), 'button ghost'));
    else actions.append(el('span', { class: 'field-type' }, '不需要现在作出决定'));
    let nextText = state.stage === 0 ? '开始填写' : state.stage === 7 ? '核对我的回答' : state.stage === 8 ? '确认并保存答卷' : '下一节';
    if (state.stage === 0 && value('consent.report_use') === 'disagree') nextText = '结束填写';
    if (state.stage === 0 && value('consent.private_completion') === 'uncomfortable' && value('consent.report_use') !== 'disagree') nextText = '先暂停填写';
    if (state.stage === 8 && value('q38.report_action') === 'edit') nextText = '返回修改';
    if (state.stage === 8 && value('q38.report_action') === 'stop') nextText = '结束填写';
    actions.append(button([state.busy ? '正在保存…' : nextText, icon('arrow')], state.stage === 0 ? begin : state.stage === 8 ? submit : () => navigate(state.stage + 1), 'button primary', { id: 'next-step' }));
    main.append(actions);
    main.append(el('div', { class: 'draft-tools' }, button('保存草稿到本机', exportDraft, ''), el('span', { 'aria-hidden': 'true' }, '·'), button('导入一份草稿', chooseDraft, '')));
    main.append(el('p', { class: 'footer-note' }, '提交前可以随时修改。刷新或关闭页面会清空未提交的内容，如需稍后继续，请先保存草稿。'));
    workspace.append(main);
  }
  root.replaceChildren(el('div', { class: 'shell' }, sidebar(), workspace));
}
async function load() {
  try {
    const response = await fetch('/api/questionnaire', { cache: 'no-store' });
    if (!response.ok) throw new Error('request');
    state.definition = await response.json();
    state.index = buildIndex(state.definition);
    state.answers = normalizeAnswers(state.definition, emptyAnswers(state.definition));
    render();
  } catch {
    root.replaceChildren(el('main', { class: 'loading-screen', id: 'main' }, el('span', { class: 'loading-mark' }, 'E'), el('h1', {}, '问卷暂时没有加载成功'), el('p', {}, '请检查服务是否已启动，然后重试。'), button('重新加载', load, 'button')));
  }
}
window.addEventListener('beforeunload', event => {
  if (state.mode === 'stopped' || state.mode === 'success' || !state.answers) return;
  const hasAnswers = Object.entries(state.answers).some(([qid, fields]) => qid !== 'consent' && Object.values(fields).some(answer => answer.status === 'answered' || answer.status === 'declined'));
  if (hasAnswers) { event.preventDefault(); event.returnValue = ''; }
});
load();
