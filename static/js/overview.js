/* Page 1: Sign Sets overview and the New Set wizard. */
(async function () {
  'use strict';
  const S = window.Signerate;
  const $ = (id) => document.getElementById(id);

  const grid = $('set-grid');
  const wizard = $('wizard');
  const deleteDialog = $('delete-dialog');
  let sets = [];

  for (const btn of document.querySelectorAll('[data-close]')) {
    btn.addEventListener('click', () => btn.closest('dialog').close());
  }

  try {
    await S.loadFonts();
  } catch (e) {
    S.toast('Could not load the font list: ' + e.message, { type: 'error' });
  }

  // --- Overview -------------------------------------------------------------

  function card(set) {
    const st = set.settings;
    const meta = S.el('div', { class: 'set-card-meta' },
      `${S.num(st.width)}×${S.num(st.height)} mm · ${set.sign_count} ${set.sign_count === 1 ? 'sign' : 'signs'} · `,
      S.el('span', { class: set.new_count ? 'new-count' : '', text: `${set.new_count} new` }));
    const preview = S.el('div', { class: 'preview-box' });
    preview.append(S.signSvg(st, set.preview_text, set.font_size));

    return S.el('div', { class: 'set-card' },
      preview,
      S.el('div', {},
        S.el('a', { class: 'set-card-name', href: S.editorUrl(set.name), text: set.name }),
        meta),
      S.el('div', { class: 'set-card-actions' },
        S.el('button', { class: 'btn btn-secondary btn-small', type: 'button', text: 'Duplicate as new Set…',
          on: { click: () => openWizard(set.name) } }),
        S.el('button', { class: 'icon-btn danger', type: 'button', title: 'Delete Set', attrs: { 'aria-label': `Delete ${set.name}` },
          on: { click: () => askDelete(set) } }, S.icon(S.ICON_TRASH))));
  }

  function render() {
    grid.replaceChildren();
    if (!sets.length) {
      grid.append(S.el('div', { class: 'empty-state', text: 'No Sets yet. Create your first one with "+ New Set".' }));
      return;
    }
    for (const set of sets) grid.append(card(set));
  }

  async function refresh() {
    try {
      sets = await S.api('GET', '/api/sets');
      render();
    } catch (e) {
      grid.replaceChildren(S.el('div', { class: 'empty-state', text: 'Could not load Sets: ' + e.message }));
    }
  }

  // --- Delete ---------------------------------------------------------------

  let pendingDelete = null;

  function askDelete(set) {
    pendingDelete = set.name;
    $('delete-text').textContent =
      `"${set.name}" and its ${set.sign_count} signs will be moved to the trash folder on the server.`;
    deleteDialog.showModal();
  }

  $('delete-confirm').addEventListener('click', async () => {
    const name = pendingDelete;
    deleteDialog.close();
    try {
      await S.api('DELETE', S.setUrl(name));
      S.toast(`Moved "${name}" to the trash.`);
    } catch (e) {
      S.toast('Delete failed: ' + e.message, { type: 'error' });
    }
    refresh();
  });

  // --- Wizard ---------------------------------------------------------------

  let form = null;
  let chosen = null; // name of the Set to copy from, or null for blank

  const optionsBox = $('wizard-options');
  const copyRow = $('copy-signs-row');
  const nameInput = $('wizard-name');
  const nameError = $('wizard-name-error');

  function option(value, title, sub, set) {
    const radio = S.el('input', { type: 'radio', name: 'start', value });
    radio.checked = (value === (chosen || ''));
    radio.addEventListener('change', () => {
      chosen = value || null;
      copyRow.hidden = !chosen;
    });
    const box = S.el('div', { class: 'preview-box' });
    if (set) box.append(S.signSvg(set.settings, set.preview_text, set.font_size));
    return S.el('label', { class: 'wizard-option' }, radio,
      set ? box : null,
      S.el('span', { class: 'option-text' }, title, S.el('small', { text: sub })));
  }

  function openWizard(preselect) {
    chosen = preselect || null;
    $('copy-signs').checked = false;
    copyRow.hidden = !chosen;
    optionsBox.replaceChildren(
      option('', 'Blank', 'Default dimensions and style', null),
      ...sets.map((s) => option(s.name, s.name,
        `${S.num(s.settings.width)}×${S.num(s.settings.height)} mm · ${s.sign_count} signs`, s)));
    showStep(1);
    $('wizard-create').disabled = false;
    nameInput.value = '';
    nameError.textContent = '';
    if (!wizard.open) wizard.showModal();
  }

  function showStep(n) {
    $('wizard-step1').hidden = n !== 1;
    $('wizard-step2').hidden = n !== 2;
  }

  const refreshPreview = S.debounce(async () => {
    if (!form) return;
    const st = form.get();
    if (Object.keys(S.checkSettings(st)).length) return;
    try {
      const res = await S.preview(st, ['Sample']);
      $('wizard-preview').replaceChildren(S.signSvg(st, 'Sample', res.font_size));
    } catch (e) {
      $('wizard-preview').replaceChildren(S.el('span', { class: 'muted', text: e.message }));
    }
  }, 200);

  $('wizard-next').addEventListener('click', async () => {
    let base;
    try {
      base = chosen
        ? sets.find((s) => s.name === chosen).settings
        : await S.api('GET', '/api/defaults');
    } catch (e) {
      S.toast('Could not load defaults: ' + e.message, { type: 'error' });
      return;
    }
    if (!form) {
      form = S.SettingsForm(base, refreshPreview);
      $('wizard-form').append(form.el);
    } else {
      form.set(base);
    }
    showStep(2);
    refreshPreview();
    nameInput.focus();
  });

  $('wizard-back').addEventListener('click', () => showStep(1));
  nameInput.addEventListener('input', () => { nameError.textContent = ''; });

  async function create() {
    const name = nameInput.value.trim();
    if (!name) { nameError.textContent = 'Please enter a name.'; nameInput.focus(); return; }
    if (sets.some((s) => s.name.toLowerCase() === name.toLowerCase())) {
      nameError.textContent = 'A Set with this name already exists.';
      nameInput.focus();
      return;
    }
    const st = form.get();
    if (Object.keys(S.checkSettings(st)).length) {
      S.toast('Fix the highlighted fields first.', { type: 'error' });
      return;
    }
    const btn = $('wizard-create');
    btn.disabled = true;
    try {
      const body = { name, settings: st };
      if (chosen) {
        body.from = chosen;
        body.copy_signs = $('copy-signs').checked;
      }
      const created = await S.api('POST', '/api/sets', body);
      location.href = S.editorUrl(created.name);
    } catch (e) {
      nameError.textContent = e.message;
      btn.disabled = false;
    }
  }

  $('wizard-create').addEventListener('click', create);
  nameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') create(); });
  $('new-set-btn').addEventListener('click', () => openWizard(null));

  await refresh();
})();
