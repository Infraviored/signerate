/* Page 2: the Set editor. State lives in `set`; every change autosaves. */
(async function () {
  'use strict';
  const S = window.Signerate;
  const $ = (id) => document.getElementById(id);

  const SETTING_KEYS = ['width', 'height', 'min_margin', 'base_thickness', 'text_thickness',
    'text_protrusion', 'bg_color', 'text_color', 'font_path', 'horizontal_align', 'text_size', 'frame_width',
    'shape', 'shape_params'];
  const PANEL_KEY = 'signerate.panelOpen';
  const DEST_KEY = 'signerate.destination';
  const STACK_BELOW = 760; // workspace width in px; keep in step with the @container rule in style.css
  const PHONE = window.matchMedia('(max-width: 720px)');

  const state = {
    name: $('editor').dataset.setName,
    selected: new Set(), // ids of the signs the next export acts on (client state only)
    stale: false,        // another tab changed the Set; saving is stopped until a reload
    format: '3mf',
    destination: 'download',
    dirty: false,
    formInvalid: false,
    busy: false,         // an export is running
    lastExport: null,    // {token, filename, set, count}
    preview: null,       // last answer of /api/preview
    previewError: '',
    outline: null,       // contour to draw; only the server defines shapes
  };
  let set = null;
  let scale = 1;
  const rows = new Map(); // sign id -> {sign, el, box, check, input, note, actions, shrunk, size}
  let heads = [];         // day headings: {check, signs}

  const list = $('sign-list');
  const addInput = $('add-input');
  const nameInput = $('set-name');
  const topbar = document.querySelector('.topbar');
  const NAME_WIDE = 16; // characters; past this the name takes its own line on a phone
  const fitName = () => topbar.classList.toggle('name-wide', nameInput.value.length > NAME_WIDE);

  // --- Serialised network access -------------------------------------------

  let chain = Promise.resolve();
  function enqueue(fn) {
    const p = chain.then(fn);
    chain = p.catch(() => {});
    return p;
  }

  // --- Load -----------------------------------------------------------------

  try {
    await S.loadFonts();
  } catch (e) {
    S.toast('Could not load the font list: ' + e.message, { type: 'error' });
  }
  try {
    await S.loadShapes();
  } catch (e) {
    S.toast('Could not load the shape list: ' + e.message, { type: 'error' });
  }
  try {
    set = await S.api('GET', S.setUrl(state.name));
  } catch (e) {
    document.body.replaceChildren(S.el('p', { class: 'empty-state', text: 'Could not load this set: ' + e.message }));
    return;
  }
  $('close-panel').append(S.icon(S.ICON_CLOSE));

  state.outline = set.outline;
  const form = S.SettingsForm(set.settings, onSettingsChange, () => state.preview && state.preview.font_size);
  form.setOutline(set.outline);
  $('panel-form').append(form.el);

  // --- Helpers --------------------------------------------------------------

  function newId() {
    const bytes = crypto.getRandomValues(new Uint8Array(8));
    return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  }

  const newSigns = () => set.signs.filter((s) => !s.exported_at);
  const selectedSigns = () => set.signs.filter((s) => state.selected.has(s.id));
  const sameIds = (ids) => ids.length > 0 && ids.length === state.selected.size && ids.every((id) => state.selected.has(id));

  function dayKey(iso) {
    const d = new Date(iso);
    return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  }

  function dayLabel(iso) {
    const d = new Date(iso);
    const now = new Date();
    if (dayKey(iso) === dayKey(now.toISOString())) return 'Today';
    const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
    if (dayKey(iso) === dayKey(yesterday.toISOString())) return 'Yesterday';
    return d.toLocaleDateString('en-GB', d.getFullYear() === now.getFullYear()
      ? { day: 'numeric', month: 'short' }
      : { day: 'numeric', month: 'short', year: 'numeric' });
  }
  const plural = (n) => `${n} ${n === 1 ? 'sign' : 'signs'}`;

  function sameParams(x, y) {
    const keys = new Set([...Object.keys(x || {}), ...Object.keys(y || {})]);
    return [...keys].every((k) => ((x || {})[k] ?? null) === ((y || {})[k] ?? null));
  }

  function sameSettings(a, b) {
    return SETTING_KEYS.every((k) => {
      const fallback = { frame_width: 0, shape: 'rect', shape_params: {} }[k] ?? null;
      const x = a[k] === undefined ? fallback : a[k];
      const y = b[k] === undefined ? fallback : b[k];
      if (k === 'shape_params') return sameParams(x, y);
      return (typeof x === 'number' && typeof y === 'number')
        ? x === y
        : String(x).toLowerCase() === String(y).toLowerCase();
    });
  }

  /* Takes the server's view of timestamps, frozen size etc. without touching texts. */
  function mergeServerMeta(data) {
    if (!state.dirty) {
      const byId = new Map(data.signs.map((s) => [s.id, s]));
      for (const s of set.signs) {
        const r = byId.get(s.id);
        if (r) { s.created_at = r.created_at; s.exported_at = r.exported_at; }
      }
    }
    set.font_size = data.font_size;
    set.exported_settings = data.exported_settings;
    set.exports = data.exports;
    set.updated_at = data.updated_at;
  }

  // --- Saving ---------------------------------------------------------------

  const STALE_MESSAGE = 'Changed elsewhere. Click to reload.';
  let saveTimer = null;
  let retryTimer = null;
  const saveState = $('save-state');

  function setSaveState(kind, message) {
    saveState.className = 'save-state' + (kind === 'error' ? ' error' : '');
    saveState.title = message || '';
    saveState.textContent = { saving: 'Saving', saved: 'Saved', error: message || 'Not saved', '': '' }[kind];
  }

  function payload() {
    return {
      base_updated_at: set.updated_at,
      settings: set.settings,
      signs: set.signs.map((s) => ({ id: s.id, text: s.text, created_at: s.created_at, exported_at: s.exported_at })),
    };
  }

  async function doSave() {
    if (state.stale) throw new Error(STALE_MESSAGE);
    if (!state.dirty || state.formInvalid) return;
    state.dirty = false;
    setSaveState('saving');
    try {
      const data = await S.api('PUT', S.setUrl(state.name), payload());
      mergeServerMeta(data);
      if (!state.dirty) setSaveState('saved');
      updateChrome();
    } catch (e) {
      state.dirty = true;
      if (e.status === 409) { // another tab saved in between: keep the text on screen, never overwrite
        state.stale = true;
        clearTimeout(retryTimer);
        setSaveState('error', STALE_MESSAGE);
        throw e;
      }
      if (e.status === 400) form.setError(e.message);
      setSaveState('error', 'Not saved: ' + e.message + ' Click to retry.');
      if (!e.status || e.status >= 500) {
        clearTimeout(retryTimer);
        retryTimer = setTimeout(() => flush().catch(() => {}), 5000);
      }
      throw e;
    }
  }

  function flush() {
    clearTimeout(saveTimer);
    return enqueue(doSave);
  }

  function scheduleSave() {
    state.dirty = true;
    if (state.formInvalid || state.stale) return;
    setSaveState('saving');
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => flush().catch(() => {}), 400);
  }

  saveState.addEventListener('click', () => {
    if (state.stale) { location.reload(); return; }
    if (saveState.classList.contains('error') && !state.formInvalid) flush().catch(() => {});
  });

  function saveOnLeave() {
    if (!state.dirty || state.formInvalid || state.stale) return;
    state.dirty = false;
    fetch(S.setUrl(state.name), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload()),
      keepalive: true,
    }).catch(() => {});
  }
  window.addEventListener('pagehide', saveOnLeave);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush().catch(() => {});
  });

  // --- Preview (sizes come from the server, same function as the export) ----

  let previewSeq = 0;
  async function runPreview() {
    const mine = ++previewSeq;
    const ids = set.signs.map((s) => s.id);
    try {
      const res = await S.preview(set.settings, set.signs.map((s) => s.text), set.font_size);
      if (mine !== previewSeq) return;
      state.preview = { ...res, ids };
      state.previewError = '';
      state.outline = res.outline;
      form.setOutline(res.outline);
      if (!state.formInvalid) form.setError('');
    } catch (e) {
      if (mine !== previewSeq) return;
      state.previewError = e.message;
      if (e.status === 400) form.setError(e.message);
    }
    applyPreview();
  }
  const schedulePreview = S.debounce(runPreview, 120);

  function drawRow(r) {
    r.box.replaceChildren(S.signSvg(set.settings, r.sign.text, r.size, scale, { outline: state.outline, x: r.x }));
  }

  function applyPreview() {
    const st = set.settings;
    const room = $('workspace').clientWidth;
    const narrow = room < STACK_BELOW; // rows stack: sign above its text, full width
    scale = S.plateScale(st, narrow ? room - 32 : 300, narrow ? 170 : 100);
    $('workspace').style.setProperty('--plate-w', `${st.width * scale}px`);

    const p = state.preview;
    if (p) {
      p.ids.forEach((id, i) => {
        const r = rows.get(id);
        if (!r) return;
        r.size = p.signs[i].font_size;
        r.shrunk = p.signs[i].shrunk;
        r.x = p.signs[i].x;
      });
    }
    for (const r of rows.values()) {
      if (!r.size && p) r.size = p.font_size;
      drawRow(r);
      renderNote(r);
    }
    $('add-preview').replaceChildren(S.signSvg(st, '', null, scale, { outline: state.outline }));
    renderStats();
  }

  // --- Header, banners, panel -----------------------------------------------

  function updateChrome() {
    const st = set.settings;
    $('summary').textContent = S.summaryText(st);

    const warn = S.thicknessWarning(st);
    const tb = $('thickness-banner');
    tb.hidden = !(warn && warn.level === 'danger');
    if (warn) tb.textContent = warn.text;
    $('changed-banner').hidden = !(set.exported_settings && !sameSettings(st, set.exported_settings));

    updateExportBar();
    renderStats();
  }

  function renderStats() {
    const p = state.preview;
    const st = set.settings;
    const kids = [];
    if (state.previewError) {
      kids.push(S.el('div', { class: 'limit', text: 'No preview: ' + state.previewError }));
    } else if (!p || p.font_size === null) {
      kids.push(S.el('div', { text: 'Add a sign to see the text size.' }));
    } else {
      kids.push(S.el('div', { class: 'stat-size', text: `${p.font_size.toFixed(2)} mm` }));
      if (st.text_size) {
        kids.push(S.el('div', { text: 'Fixed by you.' }));
      } else if (set.font_size !== null) {
        kids.push(S.el('div', { text: 'Locked at the first export, so new signs match the ones already printed.' }));
      } else if (p.limiting_text) {
        kids.push(S.el('div', { text: `Set by the longest sign, "${p.limiting_text}".` }));
      } else {
        kids.push(S.el('div', { text: 'Set by the plate height.' }));
      }
      const shrunk = p.signs.filter((s) => s.shrunk).length;
      if (shrunk) kids.push(S.el('div', { class: 'limit', text: `${plural(shrunk)} shrunk to fit.` }));
    }
    const warn = S.thicknessWarning(st);
    if (warn) kids.push(S.el('div', { class: 'limit', text: warn.text }));
    $('stats').replaceChildren(...kids);
  }

  function openPanel(open) {
    $('panel').hidden = !open;
    $('editor').classList.toggle('panel-open', open);
    $('open-panel').setAttribute('aria-pressed', String(open));
    try { localStorage.setItem(PANEL_KEY, open ? '1' : '0'); } catch (e) { /* private mode */ }
    applyPreview();
  }
  $('open-panel').addEventListener('click', () => openPanel($('panel').hidden));
  $('close-panel').addEventListener('click', () => openPanel(false));
  window.addEventListener('resize', S.debounce(applyPreview, 100));

  // The toasts sit just above the export bar, however tall it wraps.
  if (window.ResizeObserver) {
    new ResizeObserver(([entry]) => {
      document.documentElement.style.setProperty('--bar-h', `${Math.ceil(entry.target.offsetHeight)}px`);
    }).observe(document.querySelector('.export-bar'));
  }

  function onSettingsChange(values, valid) {
    state.formInvalid = !valid;
    if (!valid) {
      setSaveState('error', 'Not saved: fix the highlighted field');
      return;
    }
    set.settings = { ...set.settings, ...values };
    scheduleSave();
    updateChrome();
    applyPreview();
    schedulePreview();
  }

  $('changed-renew').addEventListener('click', renewAll);

  // --- Rows -----------------------------------------------------------------

  function renderNote(r) {
    r.note.textContent = r.shrunk ? 'Shrunk to fit' : '';
    r.note.title = r.shrunk ? `Too long for the set's text size. Printed at ${r.size} mm.` : '';
  }

  function renderActions(r) {
    const kids = [S.el('button', { class: 'icon-btn row-confirm', type: 'button', title: 'Done', attrs: { 'aria-label': 'Done' },
      on: { mousedown: (e) => e.preventDefault(), click: () => addInput.focus() } }, S.icon(S.ICON_CHECK))];
    if (r.sign.exported_at) {
      kids.push(S.el('button', { class: 'icon-btn', type: 'button', title: 'Mark as new', attrs: { 'aria-label': `Mark ${r.sign.text} as new` },
        on: { click: () => renewSign(r.sign) } }, S.icon(S.ICON_RENEW)));
    }
    kids.push(S.el('button', { class: 'icon-btn danger', type: 'button', title: 'Delete', attrs: { 'aria-label': `Delete ${r.sign.text}` },
      on: { click: () => deleteSign(r.sign) } }, S.icon(S.ICON_TRASH)));
    r.actions.replaceChildren(...kids);
  }

  /* A checkbox in a native label: the label is the (large) touch target. */
  function pick(input) {
    return S.el('label', { class: 'pick' }, input);
  }

  function checkbox(label, onChange) {
    return S.el('input', { type: 'checkbox', attrs: { 'aria-label': label }, on: { change: (e) => onChange(e.target.checked) } });
  }

  function setSelected(sign, on) {
    if (on) state.selected.add(sign.id); else state.selected.delete(sign.id);
  }

  /* Brings every checkbox, row tint, day heading and the export bar in line with `state.selected`. */
  function applySelection() {
    for (const id of [...state.selected]) if (!rows.has(id)) state.selected.delete(id);
    for (const r of rows.values()) {
      const on = state.selected.has(r.sign.id);
      r.check.checked = on;
      r.el.classList.toggle('selected', on);
      r.el.classList.toggle('is-new', !r.sign.exported_at);
      r.check.setAttribute('aria-label', `Select ${r.sign.text}`);
    }
    for (const h of heads) {
      const n = h.signs.filter((s) => state.selected.has(s.id)).length;
      h.check.checked = n === h.signs.length;
      h.check.indeterminate = n > 0 && n < h.signs.length;
    }
    updateExportBar();
  }

  function buildRow(sign) {
    const box = S.el('div', { class: 'plate-slot' });
    const check = checkbox('Select sign', (on) => { setSelected(sign, on); applySelection(); });
    const input = S.el('textarea', { class: 'sign-input', rows: sign.text.split('\n').length, value: sign.text, maxLength: 200, attrs: { 'aria-label': 'Sign text' } });
    const note = S.el('span', { class: 'row-note' });
    const actions = S.el('div', { class: 'row-actions' });
    const el = S.el('div', { class: 'sign-row', dataset: { id: sign.id } }, pick(check), box, input, note, actions);
    const r = { sign, el, box, check, input, note, actions, shrunk: false, size: null, moved: false };

    box.addEventListener('click', () => { setSelected(sign, !state.selected.has(sign.id)); applySelection(); });

    input.addEventListener('input', () => {
      input.rows = input.value.split('\n').length;
      const v = input.value.trim();
      el.classList.toggle('editing-empty', !v);
      if (!v) return; // an empty sign is never saved; blur restores the old text
      sign.text = v;
      if (sign.exported_at) { // changed after printing; it moves to New when editing ends
        sign.exported_at = null;
        r.moved = true;
        state.selected.add(sign.id); // new again: ready for the next export
      }
      drawRow(r);
      scheduleSave();
      updateChrome();
      applySelection();
      schedulePreview();
    });
    input.addEventListener('blur', () => {
      if (!input.value.trim()) {
        if (sign.fresh) { removeSign(sign); return; }
        input.value = sign.text;
        el.classList.remove('editing-empty');
      }
      sign.fresh = false;
      if (r.moved) setTimeout(renderList, 150); // after a click on another row has landed
    });
    input.addEventListener('keydown', (e) => {
      // Plain Enter is a line break; the check mark or a click elsewhere confirms.
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) addInput.focus();
      if (e.key === 'Escape') { input.value = sign.text; input.rows = sign.text.split('\n').length; input.blur(); }
    });

    renderActions(r);
    rows.set(sign.id, r);
    return r;
  }

  function groupHead(title, signs) {
    const check = checkbox(`Select all signs of ${title}`, (on) => {
      for (const sign of signs) setSelected(sign, on);
      applySelection();
    });
    heads.push({ check, signs });
    return S.el('h3', { class: 'group-head' }, pick(check), S.el('span', { text: title }), S.el('span', { class: 'group-count', text: String(signs.length) }));
  }

  /* One chronological list, newest first, with one heading per day the signs were created. */
  function renderList() {
    const top = list.scrollTop;
    const active = document.activeElement && document.activeElement.closest && document.activeElement.closest('.sign-row[data-id]');
    const activeId = active ? active.dataset.id : null;
    const caret = activeId ? document.activeElement.selectionStart : null;

    const old = rows.size ? new Map([...rows].map(([id, r]) => [id, r.size])) : new Map();
    rows.clear();
    heads = [];
    list.replaceChildren();
    if (!set.signs.length) {
      list.append(S.el('div', { class: 'empty-state', text: 'No signs yet. Start typing in the blank row above.' }));
    }

    // Array.sort is stable: signs created at the same moment keep their stored order.
    const ordered = set.signs
      .map((sign) => ({ sign, t: Date.parse(sign.created_at) || 0 }))
      .sort((x, y) => y.t - x.t)
      .map((x) => x.sign);
    const byDay = new Map();
    for (const sign of ordered) {
      const day = dayKey(sign.created_at);
      if (!byDay.has(day)) byDay.set(day, []);
      byDay.get(day).push(sign);
    }
    for (const signs of byDay.values()) {
      list.append(groupHead(dayLabel(signs[0].created_at), signs));
      for (const sign of signs) list.append(buildRow(sign).el);
    }

    for (const [id, r] of rows) r.size = old.get(id) || null;
    list.scrollTop = top;
    applySelection();
    applyPreview();
    updateChrome();

    if (activeId && rows.has(activeId)) {
      const input = rows.get(activeId).input;
      input.focus();
      if (caret !== null) input.setSelectionRange(caret, caret);
    }
  }

  // --- Adding, deleting, marking -------------------------------------------

  function addSigns(lines) {
    const now = new Date().toISOString();
    const fresh = lines.map((text) => ({ id: newId(), text: text.slice(0, 200), created_at: now, exported_at: null }));
    // The last line ends up on top ("newest first").
    set.signs.unshift(...fresh.reverse());
    for (const sign of fresh) state.selected.add(sign.id);
    list.scrollTop = 0;
    renderList();
    scheduleSave();
    schedulePreview();
    return fresh[0];
  }

  /* Typing in the blank row creates the sign and carries on in its own row. */
  addInput.addEventListener('input', () => {
    const text = addInput.value;
    if (!text.trim()) return;
    addInput.value = '';
    const sign = addSigns([text.trimStart()]);
    sign.fresh = true;
    const input = rows.get(sign.id).input;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  });

  addInput.addEventListener('paste', (e) => {
    const text = (e.clipboardData || window.clipboardData).getData('text');
    if (!/[\r\n]/.test(text.trim())) return; // single line: normal paste
    e.preventDefault();
    const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    if (lines.length) addSigns(lines);
    addInput.value = '';
    addInput.focus();
  });

  function removeSign(sign) {
    const index = set.signs.indexOf(sign);
    if (index < 0) return -1;
    set.signs.splice(index, 1);
    state.selected.delete(sign.id);
    renderList();
    scheduleSave();
    schedulePreview();
    return index;
  }

  function deleteSign(sign) {
    const wasSelected = state.selected.has(sign.id);
    const index = removeSign(sign);
    if (index < 0) return;
    S.toast(`Deleted "${sign.text}"`, {
      ms: 6000,
      action: {
        label: 'Undo',
        fn: () => {
          set.signs.splice(Math.min(index, set.signs.length), 0, sign);
          if (wasSelected) state.selected.add(sign.id);
          renderList();
          scheduleSave();
          schedulePreview();
        },
      },
    });
  }

  async function renewSign(sign) {
    try {
      await flush();
      const data = await S.api('POST', `${S.setUrl(state.name)}/signs/${sign.id}/renew`);
      mergeServerMeta(data);
      state.selected.add(sign.id);
      renderList();
    } catch (e) {
      S.toast('Could not mark as new: ' + e.message, { type: 'error' });
    }
  }

  async function renewAll() {
    try {
      await flush();
      const data = await S.api('POST', `${S.setUrl(state.name)}/renew-all`);
      mergeServerMeta(data);
      for (const s of set.signs) state.selected.add(s.id);
      renderList();
      schedulePreview();
    } catch (e) {
      S.toast('Could not mark all as new: ' + e.message, { type: 'error' });
    }
  }

  // --- Rename ---------------------------------------------------------------

  const nameError = $('name-error');
  let nameErrorTimer = null;

  function showNameError(message) {
    nameError.textContent = message;
    nameError.hidden = false;
    clearTimeout(nameErrorTimer);
    nameErrorTimer = setTimeout(() => { nameError.hidden = true; }, 5000);
  }

  async function rename() {
    const wanted = nameInput.value.trim();
    if (wanted === state.name) { nameInput.value = state.name; fitName(); return; }
    try {
      const data = await enqueue(async () => {
        await doSave().catch(() => {});
        return S.api('POST', `${S.setUrl(state.name)}/rename`, { name: wanted });
      });
      state.name = data.name;
      set.name = data.name;
      set.updated_at = data.updated_at;
      nameInput.value = data.name;
      fitName();
      nameError.hidden = true;
      document.title = `${data.name} - Signerate`;
      history.replaceState(null, '', S.editorUrl(data.name));
    } catch (e) {
      nameInput.value = state.name;
      fitName();
      showNameError(e.message);
    }
  }
  nameInput.addEventListener('blur', rename);
  nameInput.addEventListener('input', fitName);
  nameInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') nameInput.blur();
    if (e.key === 'Escape') { nameInput.value = state.name; fitName(); nameInput.blur(); }
  });

  // --- Export ---------------------------------------------------------------

  const exportBtn = $('export-btn');
  const statusLine = $('export-status');

  function updateExportBar() {
    const newIds = newSigns().map((x) => x.id);
    const allIds = set.signs.map((x) => x.id);
    const total = allIds.length;
    const [btnNew, btnAll] = $('scope').children;
    btnNew.textContent = `New (${newIds.length})`;
    btnAll.textContent = `All (${total})`;
    btnNew.classList.toggle('active', sameIds(newIds));
    btnAll.classList.toggle('active', sameIds(allIds));
    for (const b of $('format').children) b.classList.toggle('active', b.dataset.format === state.format);
    for (const b of $('destination').children) b.classList.toggle('active', b.dataset.destination === state.destination);
    const count = selectedSigns().length;
    exportBtn.textContent = state.busy ? 'Exporting'
      : count ? `Export ${plural(count)}`
      : (total ? 'Select signs to export' : 'No signs yet');
    exportBtn.disabled = state.busy || count === 0;
  }

  $('scope').children[0].addEventListener('click', () => {
    state.selected = new Set(newSigns().map((x) => x.id));
    applySelection();
  });
  $('scope').children[1].addEventListener('click', () => {
    state.selected = new Set(set.signs.map((x) => x.id));
    applySelection();
  });
  for (const btn of $('format').children) {
    btn.addEventListener('click', () => {
      state.format = btn.dataset.format;
      updateExportBar();
    });
  }

  for (const btn of $('destination').children) {
    btn.addEventListener('click', () => {
      state.destination = btn.dataset.destination;
      try { localStorage.setItem(DEST_KEY, state.destination); } catch (e) { /* private mode */ }
      updateExportBar();
    });
  }

  function showStatus(kind, text, link) {
    statusLine.hidden = false;
    statusLine.className = 'export-status' + (kind === 'error' ? ' error' : '');
    statusLine.title = link ? link.filename : '';
    statusLine.replaceChildren(S.el('span', { text }));
    if (link) {
      statusLine.append(S.el('a', { href: `/api/download?id=${encodeURIComponent(link.token)}`, text: 'Download again' }));
    }
  }

  function startDownload(token) {
    const a = S.el('a', { href: `/api/download?id=${encodeURIComponent(token)}` });
    a.download = '';
    document.body.append(a);
    a.click();
    a.remove();
  }

  function setBusy(busy) {
    state.busy = busy;
    addInput.disabled = busy;
    list.inert = busy;
    nameInput.disabled = busy;
    $('panel').inert = busy;
    updateExportBar();
  }

  async function doExport() {
    const ids = selectedSigns().map((x) => x.id);
    if (state.busy || ids.length === 0) return;
    const bar = $('progress-bar');
    setBusy(true);
    $('progress').hidden = false;
    bar.style.width = '0%';
    showStatus('info', 'Saving');
    let poll = null;
    try {
      await flush();
      showStatus('info', 'Starting');
      poll = setInterval(async () => {
        try {
          const p = await S.api('GET', '/api/progress');
          if (p.active && p.total > 0) {
            bar.style.width = `${(p.current / p.total) * 100}%`;
            showStatus('info', `Sign ${p.current} of ${p.total}`);
          }
        } catch (e) { /* the export request reports real errors */ }
      }, 500);
      const res = await S.api('POST', `${S.setUrl(state.name)}/export`, { ids, format: state.format, destination: state.destination });
      bar.style.width = '100%';
      mergeServerMeta(res.set);
      for (const id of ids) state.selected.delete(id);
      state.lastExport = { token: res.token, filename: res.filename, set: state.name, count: res.count };
      if (state.destination === 'nextcloud') {
        showStatus('success', `Saved ${plural(res.count)} to Nextcloud.`, state.lastExport);
      } else {
        showStatus('success', `Exported ${plural(res.count)}.`, state.lastExport);
        startDownload(res.token);
      }
      renderList();
      schedulePreview();
    } catch (e) {
      showStatus('error', 'Export failed: ' + e.message);
    } finally {
      clearInterval(poll);
      setBusy(false);
      setTimeout(() => { $('progress').hidden = true; }, 1500);
    }
  }
  exportBtn.addEventListener('click', doExport);

  // --- Start ----------------------------------------------------------------

  nameInput.value = set.name;
  fitName();
  document.title = `${set.name} - Signerate`;
  let panelOpen = false;
  try { panelOpen = localStorage.getItem(PANEL_KEY) === '1'; } catch (e) { /* private mode */ }
  if (panelOpen && !PHONE.matches) { // on a phone the sheet covers half the screen: start closed
    $('panel').hidden = false;
    $('editor').classList.add('panel-open');
    $('open-panel').setAttribute('aria-pressed', 'true');
  }
  state.selected = new Set(newSigns().map((x) => x.id)); // everything not yet printed starts selected
  renderList();
  runPreview();
  setSaveState('');

  try {
    const targets = await S.api('GET', '/api/export-targets');
    if (targets.nextcloud) {
      $('destination').hidden = false;
      $('destination').title = `Nextcloud saves to ${targets.folder}`;
      try { if (localStorage.getItem(DEST_KEY) === 'nextcloud') state.destination = 'nextcloud'; } catch (e) { /* private mode */ }
      updateExportBar();
    }
  } catch (e) { /* Download still works */ }

  try {
    const last = await S.api('GET', '/api/last-export');
    if (last && last.set === state.name) {
      state.lastExport = last;
      showStatus('info', last.count ? `Last export: ${plural(last.count)}.` : 'Last export ready.', last);
    }
  } catch (e) { /* optional */ }
})();
