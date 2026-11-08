/* Page 2: the Set editor. State lives in `set`; every change autosaves. */
(async function () {
  'use strict';
  const S = window.Signerate;
  const $ = (id) => document.getElementById(id);

  const SETTING_KEYS = ['width', 'height', 'min_margin', 'base_thickness', 'text_thickness',
    'text_protrusion', 'bg_color', 'text_color', 'font_path', 'horizontal_align'];

  const state = {
    name: $('editor').dataset.setName,
    scope: 'new',        // "New only" every time the page loads
    dirty: false,
    formInvalid: false,
    busy: false,         // an export is running
    lastExport: null,    // {token, filename, set, count}
    preview: null,       // last answer of /api/preview
    previewError: '',
  };
  let set = null;
  const rows = new Map(); // sign id -> {sign, el, box, input, meta, shrunk, size}

  const list = $('sign-list');
  const addInput = $('add-input');
  const nameInput = $('set-name');

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
    set = await S.api('GET', S.setUrl(state.name));
  } catch (e) {
    document.body.replaceChildren(S.el('p', { class: 'empty-state', text: 'Could not load this Set: ' + e.message }));
    return;
  }
  $('close-panel').append(S.icon(S.ICON_CLOSE));

  const form = S.SettingsForm(set.settings, onSettingsChange);
  $('panel-form').append(form.el);

  // --- Helpers --------------------------------------------------------------

  function newId() {
    const bytes = crypto.getRandomValues(new Uint8Array(8));
    return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  }

  const newSigns = () => set.signs.filter((s) => !s.exported_at);
  const scopeCount = () => (state.scope === 'new' ? newSigns().length : set.signs.length);

  function sameSettings(a, b) {
    return SETTING_KEYS.every((k) => (typeof a[k] === 'number' || typeof b[k] === 'number')
      ? Number(a[k]) === Number(b[k])
      : String(a[k]).toLowerCase() === String(b[k]).toLowerCase());
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

  let saveTimer = null;
  let retryTimer = null;
  const saveState = $('save-state');

  function setSaveState(kind, message) {
    saveState.className = 'save-state' + (kind === 'error' ? ' error' : '');
    saveState.title = message || '';
    saveState.textContent = { saving: 'Saving…', saved: 'Saved', error: message || 'Not saved', '': '' }[kind];
  }

  function payload() {
    return {
      settings: set.settings,
      signs: set.signs.map((s) => ({ id: s.id, text: s.text, created_at: s.created_at, exported_at: s.exported_at })),
    };
  }

  async function doSave() {
    if (!state.dirty || state.formInvalid) return;
    state.dirty = false;
    setSaveState('saving');
    try {
      const data = await S.api('PUT', S.setUrl(state.name), payload());
      mergeServerMeta(data);
      if (!state.dirty) setSaveState('saved');
      refreshRowMeta();
      updateChrome();
    } catch (e) {
      state.dirty = true;
      setSaveState('error', 'Save failed: ' + e.message + ' (click to retry)');
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
    if (state.formInvalid) return;
    setSaveState('saving');
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => flush().catch(() => {}), 400);
  }

  saveState.addEventListener('click', () => {
    if (saveState.classList.contains('error') && !state.formInvalid) flush().catch(() => {});
  });

  function saveOnLeave() {
    if (!state.dirty || state.formInvalid) return;
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
    } catch (e) {
      if (mine !== previewSeq) return;
      state.previewError = e.message;
    }
    applyPreview();
  }
  const schedulePreview = S.debounce(runPreview, 150);

  function applyPreview() {
    const p = state.preview;
    const face = S.fontCss(set.settings.font_path);
    if (p) {
      p.ids.forEach((id, i) => {
        const r = rows.get(id);
        if (!r) return;
        r.size = p.signs[i].font_size;
        r.shrunk = p.signs[i].shrunk;
        r.el.classList.toggle('limiting', !!p.limiting_text && r.sign.text === p.limiting_text);
      });
    }
    for (const r of rows.values()) {
      r.input.style.fontFamily = face;
      r.box.replaceChildren(S.signSvg(set.settings, r.sign.text, r.size));
      renderMeta(r);
    }
    renderStats();
  }

  // --- Header, banners, panel -----------------------------------------------

  function updateChrome() {
    const st = set.settings;
    $('summary').textContent = S.summaryText(st);
    $('swatch-bg').style.backgroundColor = st.bg_color;
    $('swatch-text').style.backgroundColor = st.text_color;

    const warn = S.thicknessWarning(st);
    const tb = $('thickness-banner');
    tb.hidden = !warn;
    if (warn) {
      tb.classList.toggle('danger', warn.level === 'danger');
      tb.textContent = `${warn.title}: ${warn.detail}`;
    }
    $('changed-banner').hidden = !(set.exported_settings && !sameSettings(st, set.exported_settings));

    $('list-title').textContent = `Signs (${set.signs.length})`;
    updateExportBar();
    renderStats();
  }

  function renderStats() {
    const box = $('stats');
    const p = state.preview;
    const kids = [];
    if (state.previewError) {
      kids.push(S.el('div', { class: 'limit', text: 'Preview unavailable: ' + state.previewError }));
    } else if (!p || p.font_size === null) {
      kids.push(S.el('div', { text: 'Add a sign to see the font size.' }));
    } else {
      const locked = set.font_size !== null;
      kids.push(S.el('div', {}, 'Set font size: ', S.el('strong', { text: `${p.font_size.toFixed(2)} mm` })));
      kids.push(S.el('div', { text: locked
        ? 'Locked since the first export, so later exports match the printed signs.'
        : 'Automatic until the first export, then it is locked.' }));
      if (p.limiting_text) {
        kids.push(S.el('div', {}, 'Limited by: ', S.el('span', { class: 'limit', text: `"${p.limiting_text}"` })));
      }
      if (locked && p.optimum !== null && p.optimum < p.font_size) {
        kids.push(S.el('div', { text: `Best fit for all signs now would be ${p.optimum.toFixed(2)} mm.` }));
      }
      const shrunk = p.signs.filter((s) => s.shrunk).length;
      if (shrunk) kids.push(S.el('div', { class: 'limit', text: `${shrunk} ${shrunk === 1 ? 'sign is' : 'signs are'} shrunk to fit.` }));
    }
    const warn = S.thicknessWarning(set.settings);
    if (warn) kids.push(S.el('div', { class: 'limit', text: `${warn.title}: ${warn.detail}` }));
    box.replaceChildren(...kids);
  }

  function openPanel(open) {
    $('panel').classList.toggle('open', open);
    $('backdrop').classList.toggle('show', open);
  }
  $('open-panel').addEventListener('click', () => openPanel(true));
  $('close-panel').addEventListener('click', () => openPanel(false));
  $('backdrop').addEventListener('click', () => openPanel(false));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') openPanel(false); });

  function onSettingsChange(values, valid) {
    state.formInvalid = !valid;
    if (!valid) {
      setSaveState('error', 'Not saved: fix the highlighted fields');
      return;
    }
    set.settings = { ...set.settings, ...values };
    scheduleSave();
    updateChrome();
    schedulePreview();
  }

  $('changed-renew').addEventListener('click', renewAll);

  // --- Rows -----------------------------------------------------------------

  function renderMeta(r) {
    const { sign } = r;
    const kids = [];
    kids.push(sign.exported_at
      ? S.el('span', { class: 'badge badge-exported', text: `exported ${S.shortDate(sign.exported_at)}` })
      : S.el('span', { class: 'badge badge-new', text: 'NEW' }));
    if (r.shrunk) {
      const target = state.preview ? state.preview.font_size : '?';
      kids.push(S.el('span', { class: 'badge badge-shrunk', text: 'shrunk',
        title: `Does not fit at the Set font size (${target} mm). It is printed smaller (${r.size} mm).` }));
    }
    if (sign.exported_at) {
      kids.push(S.el('button', { class: 'link-btn', type: 'button', text: 'Mark as new',
        on: { click: () => renewSign(sign) } }));
    }
    r.meta.replaceChildren(...kids);
  }

  function refreshRowMeta() {
    for (const r of rows.values()) renderMeta(r);
    applyScopeDim();
  }

  function applyScopeDim() {
    for (const r of rows.values()) r.el.classList.toggle('dim', state.scope === 'new' && !!r.sign.exported_at);
  }

  function buildRow(sign) {
    const box = S.el('div', { class: 'preview-box' });
    const meta = S.el('div', { class: 'row-meta' });
    const input = S.el('input', { type: 'text', value: sign.text, maxLength: 200, attrs: { 'aria-label': 'Sign text' } });
    const del = S.el('button', { class: 'icon-btn danger', type: 'button', title: 'Delete sign', attrs: { 'aria-label': 'Delete sign' },
      on: { click: () => deleteSign(sign) } }, S.icon(S.ICON_TRASH));
    const el = S.el('div', { class: 'sign-row' }, box, input, meta, del);
    const r = { sign, el, box, input, meta, shrunk: false, size: null };

    input.addEventListener('input', () => {
      const v = input.value.trim();
      el.classList.toggle('editing-empty', !v);
      if (!v) return; // an empty sign is never saved; blur restores the old text
      sign.text = v;
      if (sign.exported_at) sign.exported_at = null; // changed after printing
      renderMeta(r);
      applyScopeDim();
      scheduleSave();
      updateChrome();
      schedulePreview();
    });
    input.addEventListener('blur', () => {
      if (!input.value.trim()) {
        input.value = sign.text;
        el.classList.remove('editing-empty');
      }
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') input.blur();
      if (e.key === 'Escape') { input.value = sign.text; input.blur(); }
    });

    rows.set(sign.id, r);
    return r;
  }

  function renderList() {
    const top = list.scrollTop;
    rows.clear();
    list.replaceChildren();
    if (!set.signs.length) {
      list.append(S.el('div', { class: 'empty-state', text: 'No signs yet. Type a name above and press Enter.' }));
    }
    for (const sign of set.signs) list.append(buildRow(sign).el);
    list.scrollTop = top;
    applyScopeDim();
    applyPreview();
    updateChrome();
  }

  // --- Adding, deleting, marking -------------------------------------------

  function addSigns(lines) {
    const now = new Date().toISOString();
    const fresh = lines.map((text) => ({ id: newId(), text: text.slice(0, 200), created_at: now, exported_at: null }));
    // The last line ends up on top ("newest first").
    set.signs.unshift(...fresh.reverse());
    list.scrollTop = 0;
    renderList();
    scheduleSave();
    schedulePreview();
  }

  addInput.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.isComposing) return;
    e.preventDefault();
    const text = addInput.value.trim();
    if (!text) return;
    addSigns([text]);
    addInput.value = '';
    addInput.focus();
  });

  addInput.addEventListener('paste', (e) => {
    const text = (e.clipboardData || window.clipboardData).getData('text');
    if (!/[\r\n]/.test(text.trim())) return; // single line: normal paste
    e.preventDefault();
    const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    if (lines.length) addSigns(lines);
    addInput.value = '';
  });

  function deleteSign(sign) {
    const index = set.signs.indexOf(sign);
    if (index < 0) return;
    set.signs.splice(index, 1);
    renderList();
    scheduleSave();
    schedulePreview();
    S.toast(`Deleted "${sign.text}"`, {
      ms: 6000,
      action: {
        label: 'Undo',
        fn: () => {
          set.signs.splice(Math.min(index, set.signs.length), 0, sign);
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
      refreshRowMeta();
      updateChrome();
    } catch (e) {
      S.toast('Could not mark as new: ' + e.message, { type: 'error' });
    }
  }

  async function renewAll() {
    try {
      await flush();
      const data = await S.api('POST', `${S.setUrl(state.name)}/renew-all`);
      mergeServerMeta(data);
      refreshRowMeta();
      updateChrome();
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
    if (wanted === state.name) { nameInput.value = state.name; return; }
    try {
      const data = await enqueue(async () => {
        await doSave().catch(() => {});
        return S.api('POST', `${S.setUrl(state.name)}/rename`, { name: wanted });
      });
      state.name = data.name;
      set.name = data.name;
      nameInput.value = data.name;
      nameError.hidden = true;
      document.title = `${data.name} - Signerate`;
      history.replaceState(null, '', S.editorUrl(data.name));
    } catch (e) {
      nameInput.value = state.name;
      showNameError(e.message);
    }
  }
  nameInput.addEventListener('blur', rename);
  nameInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') nameInput.blur();
    if (e.key === 'Escape') { nameInput.value = state.name; nameInput.blur(); }
  });

  // --- Export ---------------------------------------------------------------

  const exportBtn = $('export-btn');
  const statusLine = $('export-status');

  function updateExportBar() {
    const nNew = newSigns().length;
    const total = set.signs.length;
    const [btnNew, btnAll] = $('scope').children;
    btnNew.textContent = `New only (${nNew})`;
    btnAll.textContent = `All (${total})`;
    btnNew.classList.toggle('active', state.scope === 'new');
    btnAll.classList.toggle('active', state.scope === 'all');
    const count = scopeCount();
    exportBtn.textContent = state.busy ? 'Exporting…' : `Export ${count} ${count === 1 ? 'sign' : 'signs'}`;
    exportBtn.disabled = state.busy || count === 0;
    $('export-note').textContent = count === 0
      ? (total === 0 ? 'No signs yet' : 'Nothing new to export')
      : '';
  }

  for (const btn of $('scope').children) {
    btn.addEventListener('click', () => {
      state.scope = btn.dataset.scope;
      applyScopeDim();
      updateExportBar();
    });
  }

  function showStatus(kind, text, link) {
    statusLine.hidden = false;
    statusLine.className = 'export-status' + (kind === 'error' ? ' error' : '');
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
    $('add-input').parentElement.inert = busy;
    list.inert = busy;
    nameInput.disabled = busy;
    $('open-panel').disabled = busy;
    $('panel').inert = busy;
    updateExportBar();
  }

  async function doExport() {
    if (state.busy || scopeCount() === 0) return;
    const bar = $('progress-bar');
    setBusy(true);
    $('progress').hidden = false;
    bar.style.width = '0%';
    showStatus('info', 'Saving…');
    let poll = null;
    try {
      await flush();
      showStatus('info', 'Starting export…');
      poll = setInterval(async () => {
        try {
          const p = await S.api('GET', '/api/progress');
          if (p.active && p.total > 0) {
            bar.style.width = `${(p.current / p.total) * 100}%`;
            showStatus('info', `Generating sign ${p.current} of ${p.total}…`);
          }
        } catch (e) { /* the export request reports real errors */ }
      }, 500);
      const res = await S.api('POST', `${S.setUrl(state.name)}/export`, { scope: state.scope, format: $('format').value });
      bar.style.width = '100%';
      mergeServerMeta(res.set);
      state.lastExport = { token: res.token, filename: res.filename, set: state.name, count: res.count };
      showStatus('success', `Exported ${res.count} ${res.count === 1 ? 'sign' : 'signs'}: ${res.filename}`, state.lastExport);
      startDownload(res.token);
      refreshRowMeta();
      updateChrome();
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
  document.title = `${set.name} - Signerate`;
  renderList();
  runPreview();
  setSaveState('');

  try {
    const last = await S.api('GET', '/api/last-export');
    if (last && last.set === state.name) {
      state.lastExport = last;
      showStatus('info', `Last export: ${last.filename}`, last);
    }
  } catch (e) { /* optional */ }
})();
