const $ = (id) => document.getElementById(id);
let state = { step: 1, run: null, result: null, file: null, decisions: {} };
let historyItems = [];
let historyPage = 1;
const historySize = 5;
const summaryMetrics = [
  ['inserted', 'Casos inseridos', 'inserido', 'inseridos'],
  ['updated', 'Casos atualizados', 'atualizado', 'atualizados'],
  ['removed', 'Casos removidos', 'removido', 'removidos'],
  ['unchanged', 'Sem alterações', 'sem alteração', 'sem alterações'],
  ['invalid', 'CNJ inválido', 'CNJ inválido', 'CNJs inválidos'],
];

function showError(message) {
  const box = $('alert');
  $('alert-message').textContent = message;
  box.classList.toggle('hidden', !message);
  if (message) box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function setStep(step) {
  state.step = step;
  for (let n = 1; n <= 3; n++) $(`screen-${n}`).classList.toggle('hidden', n !== step);
  showError('');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || 'Não foi possível concluir a ação.');
  return body;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const ICON_PATHS = {
  upload: ['M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4', 'M17 8l-5-5-5 5M12 3v12'],
  chevronLeft: ['m15 18-6-6 6-6'],
  chevronRight: ['m9 18 6-6-6-6'],
  firstPage: ['M5 5v14', 'm18 18-6-6 6-6'],
  lastPage: ['M19 5v14', 'm6 18 6-6-6-6'],
  check: ['M20 6 9 17l-5-5'],
  x: ['M18 6 6 18', 'M6 6 18 18'],
  calendar: ['M8 2v4', 'M16 2v4', 'M3 10h18', 'M5 4h14a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z'],
};

function icon(name, size = 16) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [key, value] of Object.entries({ width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' })) svg.setAttribute(key, value);
  svg.classList.add('icon');
  for (const shape of ICON_PATHS[name]) {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', shape);
    svg.append(path);
  }
  return svg;
}

function metric(value, label) {
  const card = element('div', 'metric');
  if (label === 'CNJ inválido' && Number(value) > 0) card.classList.add('invalid');
  card.append(element('span', 'number', String(value)), element('span', 'label', label));
  return card;
}

function renderMetrics(target, entries) {
  target.replaceChildren(...entries.map(([number, label]) => metric(number, label)));
}

function displayDate(value) {
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value));
}

function localIso(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function datePicker(folder, initialValue, onChange) {
  const root = element('div', 'date-picker');
  const trigger = element('button', 'date-trigger');
  trigger.type = 'button';
  trigger.setAttribute('aria-label', `Data de remoção da pasta ${folder}`);
  trigger.setAttribute('aria-haspopup', 'dialog');
  trigger.setAttribute('aria-expanded', 'false');
  const panel = element('div', 'calendar-panel');
  panel.id = `calendar-${folder}`;
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', `Selecionar data de remoção da pasta ${folder}`);
  trigger.setAttribute('aria-controls', panel.id);
  let selected = initialValue;
  let cursor = new Date(`${initialValue}T12:00:00`);

  function close() {
    root.classList.remove('open');
    trigger.setAttribute('aria-expanded', 'false');
  }

  function choose(day) {
    selected = localIso(day);
    onChange(selected);
    updateTrigger();
    close();
    trigger.focus();
  }

  function updateTrigger() {
    const [year, month, day] = selected.split('-');
    trigger.replaceChildren(document.createTextNode(`${day}/${month}/${year}`), icon('calendar'));
  }

  function renderCalendar() {
    const header = element('div', 'calendar-header');
    const previous = element('button', 'calendar-nav');
    previous.type = 'button';
    previous.setAttribute('aria-label', 'Mês anterior');
    previous.append(icon('chevronLeft'));
    previous.addEventListener('click', () => { cursor = new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1); renderCalendar(); });
    const next = element('button', 'calendar-nav');
    next.type = 'button';
    next.setAttribute('aria-label', 'Próximo mês');
    next.append(icon('chevronRight'));
    next.addEventListener('click', () => { cursor = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1); renderCalendar(); });
    header.append(previous, element('strong', '', new Intl.DateTimeFormat('pt-BR', { month: 'long', year: 'numeric' }).format(cursor)), next);

    const grid = element('div', 'calendar-grid');
    for (const name of ['D', 'S', 'T', 'Q', 'Q', 'S', 'S']) grid.append(element('span', 'calendar-weekday', name));
    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    for (let index = 0; index < 42; index++) {
      const day = new Date(first.getFullYear(), first.getMonth(), index + 1 - first.getDay());
      const iso = localIso(day);
      const button = element('button', 'calendar-day', String(day.getDate()));
      button.type = 'button';
      button.setAttribute('aria-label', new Intl.DateTimeFormat('pt-BR', { dateStyle: 'full' }).format(day));
      if (iso === selected) button.setAttribute('aria-current', 'date');
      if (iso === localIso(new Date())) button.classList.add('today');
      if (day.getMonth() !== cursor.getMonth()) button.classList.add('outside');
      button.addEventListener('click', () => choose(day));
      grid.append(button);
    }
    const footer = element('div', 'calendar-footer');
    const today = element('button', '', 'Hoje');
    today.type = 'button';
    today.addEventListener('click', () => choose(new Date()));
    footer.append(today);
    panel.replaceChildren(header, grid, footer);
  }

  trigger.addEventListener('click', () => {
    const opening = !root.classList.contains('open');
    for (const picker of document.querySelectorAll('.date-picker.open')) {
      picker.classList.remove('open');
      picker.querySelector('.date-trigger').setAttribute('aria-expanded', 'false');
    }
    if (opening) {
      cursor = new Date(`${selected}T12:00:00`);
      renderCalendar();
      root.classList.add('open');
      trigger.setAttribute('aria-expanded', 'true');
    }
  });
  updateTrigger();
  root.append(trigger, panel);
  return root;
}

async function loadHistory() {
  try {
    historyItems = await request('/api/history');
    renderHistory();
  } catch (error) { showError(error.message); }
}

function renderHistory() {
  const list = $('history-list');
  const pagination = $('history-pagination');
  pagination.classList.toggle('hidden', historyItems.length === 0);
  if (!historyItems.length) {
    list.replaceChildren(element('div', 'empty', 'Nenhuma reconciliação concluída'));
    return;
  }
  const pages = Math.ceil(historyItems.length / historySize);
  historyPage = Math.min(historyPage, pages);
  const start = (historyPage - 1) * historySize;
  const visible = historyItems.slice(start, start + historySize);
  list.replaceChildren(...visible.map((item) => {
      const row = element('div', 'history-row');
      const body = element('div', 'body');
      body.append(element('strong', '', displayDate(item.timestamp)));
      const counts = element('div', 'history-counts');
      if (item.summary) {
        for (const [key, , singular, plural] of summaryMetrics) {
          const value = item.summary[key];
          const count = element('span', `history-count${key === 'invalid' && Number(value) > 0 ? ' invalid' : ''}`);
          count.append(element('b', '', String(value ?? '—')), document.createTextNode(` ${value === 1 ? singular : plural}`));
          counts.append(count);
        }
      } else {
        counts.append(element('span', '', 'Resumo indisponível para esta execução anterior.'));
      }
      body.append(counts);
      row.append(body, element('span', 'tag green', 'Concluído'));
      return row;
  }));
  $('history-page-info').textContent = `Página ${historyPage} de ${pages} de ${historyItems.length} resultados`;
  $('history-first').disabled = historyPage === 1;
  $('history-prev').disabled = historyPage === 1;
  $('history-next').disabled = historyPage === pages;
  $('history-last').disabled = historyPage === pages;
}

function chooseFile(file) {
  state.file = file || null;
  $('process').disabled = !file;
  $('selected-file').classList.toggle('hidden', !file);
  $('selected-file').textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB` : '';
}

function setProcessing(busy) {
  $('dropzone').classList.toggle('processing', busy);
  $('dropzone').setAttribute('aria-busy', String(busy));
  $('dropzone-title').textContent = busy ? 'Processando arquivo…' : 'Selecione ou arraste o arquivo aqui';
  $('dropzone-hint').textContent = busy ? 'Validando e consolidando os casos' : 'Arquivo .xlsx · até 25 MB';
  $('file').disabled = busy;
  $('process').disabled = busy || !state.file;
  $('process').replaceChildren(document.createTextNode(busy ? 'Processando…' : 'Continuar'), busy ? document.createTextNode('') : icon('chevronRight'));
}

async function processFile() {
  if (!state.file || $('file').disabled) return;
  setProcessing(true);
  showError('');
  try {
    const form = new FormData();
    form.append('file', state.file);
    const result = await request('/api/process', { method: 'POST', body: form });
    state.run = result.run_id;
    state.result = result;
    const c = result.comparison;
    renderMetrics($('process-metrics'), [[result.cases, 'Casos consolidados'], [c.new.length, 'Casos novos'], [c.updated.length, 'Casos remanejados'], [result.invalid.length, 'CNJ inválido']]);
    if (c.missing.length || result.invalid.length) {
      $('upload-card').classList.add('hidden');
      review();
    }
    else { setStep(1); await finish(); }
  } catch (error) {
    showError(error.message);
  } finally {
    setProcessing(false);
  }
}

function renderMissing() {
  const missing = state.result.comparison.missing;
  $('missing-count').textContent = `${missing.length} ${missing.length === 1 ? 'caso' : 'casos'}`;
  if (!missing.length) {
    $('missing-list').replaceChildren(element('div', 'card empty', 'Nenhuma possível mudança de patrocínio'));
    return;
  }
  $('missing-list').replaceChildren(...missing.map((item) => {
    const folder = String(item.legal_folder);
    const card = element('div', 'review-card');
    const top = element('div', 'review-top');
    const info = element('div');
    info.append(element('strong', 'mono', item.case_number || 'CNJ não informado'), element('div', 'review-sub', `Pasta ${folder}${item.plaintiff_name ? ` · ${item.plaintiff_name}` : ''}`));
    top.append(info, element('span', 'tag yellow', 'Ausente'));
    const controls = element('div', 'review-controls');
    const dateWrap = element('div', 'date-field is-hidden');
    dateWrap.append(element('span', '', 'Data de remoção'));
    let removalDate = localIso(new Date());
    dateWrap.append(datePicker(folder, removalDate, (value) => {
      removalDate = value;
      if (state.decisions[folder]?.action === 'remove') state.decisions[folder].deleted_at = value;
    }));
    const toggle = element('label', 'remove-toggle');
    const input = element('input');
    input.type = 'checkbox';
    input.setAttribute('role', 'switch');
    input.addEventListener('change', () => {
      if (input.checked) state.decisions[folder] = { action: 'remove', deleted_at: removalDate };
      else {
        delete state.decisions[folder];
        const picker = dateWrap.querySelector('.date-picker');
        picker.classList.remove('open');
        picker.querySelector('.date-trigger').setAttribute('aria-expanded', 'false');
      }
      dateWrap.classList.toggle('is-hidden', !input.checked);
    });
    const track = element('span', 'toggle-track');
    track.setAttribute('aria-hidden', 'true');
    toggle.append(input, track, document.createTextNode('Remover caso'));
    controls.append(toggle);
    controls.append(dateWrap);
    card.append(top, controls);
    return card;
  }));
}

function renderInvalid() {
  const invalid = state.result.invalid;
  $('invalid-count').textContent = `${invalid.length} ${invalid.length === 1 ? 'caso' : 'casos'}`;
  const list = $('invalid-list');
  if (!invalid.length) {
    list.replaceChildren(element('div', 'empty', 'Nenhum CNJ inválido encontrado'));
    return;
  }
  const table = element('table');
  const head = element('thead');
  const headers = element('tr');
  headers.append(element('th', '', 'Pasta'), element('th', '', 'Número do caso'), element('th', '', 'Situação'));
  head.append(headers);
  const body = element('tbody');
  for (const item of invalid) {
    const row = element('tr');
    row.append(element('td', 'mono', String(item.legal_folder ?? '—')), element('td', 'mono', item.case_number || 'Não informado'), element('td', '', 'Ignorado'));
    body.append(row);
  }
  table.append(head, body);
  list.replaceChildren(table);
}

function review() {
  state.decisions = {};
  renderMissing();
  renderInvalid();
  setStep(2);
}

async function finish() {
  const missing = state.result.comparison.missing;
  for (const item of missing) {
    const folder = String(item.legal_folder);
    const decision = state.decisions[folder];
    if (decision?.action === 'remove' && !decision.deleted_at) return showError(`Informe a data de remoção da pasta ${folder}.`);
  }
  const button = $('finish');
  button.disabled = true;
  button.textContent = 'Finalizando…';
  try {
    const result = await request('/api/finish', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ run_id: state.run, decisions: state.decisions }) });
    const s = result.stats;
    renderMetrics($('final-metrics'), [[state.result.cases, 'Casos consolidados'], ...summaryMetrics.map(([key, label]) => [s[key], label])]);
    $('retry-finish').classList.add('hidden');
    setStep(3);
    return true;
  } catch (error) {
    if (state.step === 1 && state.run) {
      $('upload-card').classList.add('hidden');
      $('retry-finish').classList.remove('hidden');
    }
    showError(error.message);
    return false;
  }
  finally { button.disabled = false; button.replaceChildren(document.createTextNode('Continuar'), icon('chevronRight')); }
}

async function reset() {
  if (state.run && state.step !== 3) {
    try { await request('/api/cancel', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ run_id: state.run }) }); } catch (_) { /* A UI can still reset. */ }
  }
  state = { step: 1, run: null, result: null, file: null, decisions: {} };
  $('file').value = '';
  chooseFile(null);
  $('upload-card').classList.remove('hidden');
  $('retry-finish').classList.add('hidden');
  setStep(1);
  loadHistory();
}

$('file').addEventListener('change', (event) => chooseFile(event.target.files[0]));
$('process').addEventListener('click', processFile);
$('retry-finish-button').addEventListener('click', finish);
$('retry-cancel').addEventListener('click', reset);
$('back-process').addEventListener('click', reset);
$('finish').addEventListener('click', finish);
$('restart').addEventListener('click', reset);
$('history-prev').addEventListener('click', () => { if (historyPage > 1) { historyPage--; renderHistory(); } });
$('history-next').addEventListener('click', () => { if (historyPage * historySize < historyItems.length) { historyPage++; renderHistory(); } });
$('history-first').addEventListener('click', () => { historyPage = 1; renderHistory(); });
$('history-last').addEventListener('click', () => { historyPage = Math.ceil(historyItems.length / historySize); renderHistory(); });
const dropzone = $('dropzone');
for (const placeholder of document.querySelectorAll('[data-icon]')) placeholder.replaceChildren(icon(placeholder.dataset.icon, Number(placeholder.dataset.size || 16)));
dropzone.addEventListener('dragover', (event) => { event.preventDefault(); if (!$('file').disabled) dropzone.classList.add('dragging'); });
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragging'));
dropzone.addEventListener('drop', (event) => { event.preventDefault(); dropzone.classList.remove('dragging'); if (!$('file').disabled) chooseFile(event.dataTransfer.files[0]); });
document.addEventListener('pointerdown', (event) => {
  for (const picker of document.querySelectorAll('.date-picker.open')) {
    if (!picker.contains(event.target)) {
      picker.classList.remove('open');
      picker.querySelector('.date-trigger').setAttribute('aria-expanded', 'false');
    }
  }
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  for (const picker of document.querySelectorAll('.date-picker.open')) {
    picker.classList.remove('open');
    const trigger = picker.querySelector('.date-trigger');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.focus();
  }
});
loadHistory();
