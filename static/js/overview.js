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

  const shelfScale = (st) => S.plateScale(st, 240, 90);
  const facts = (set) => {
    const st = set.settings;
    return `${S.num(st.width)} × ${S.num(st.height)} mm, ${set.sign_count} ${set.sign_count === 1 ? 'sign' : 'signs'}`;
  };

  function shelf(set) {
    const st = set.settings;
    const scale = shelfScale(st);
    const plates = S.el('div', { class: 'shelf-signs' });
    if (set.previews.length) {
      for (const p of set.previews) plates.append(S.signSvg(st, p.text, p.font_size, scale));
    } else {
      plates.append(S.signSvg(st, '', null, scale));
    }
    return S.el('section', { class: 'shelf' },
      S.el('div', { class: 'shelf-head' },
        S.el('h2', {}, S.el('a', { class: 'shelf-name', href: S.editorUrl(set.name), text: set.name })),
        S.el('span', { class: 'shelf-facts', text: facts(set) }),
        set.new_count ? S.el('span', { class: 'tag-new', text: `${set.new_count} new` }) : null,
        S.el('div', { class: 'shelf-actions' },
          S.el('button', { class: 'btn', type: 'button', text: 'Duplicate', on: { click: () => openWizard(set.name) } }),
          S.el('button', { class: 'icon-btn danger', type: 'button', title: 'Delete', attrs: { 'aria-label': `Delete ${set.name}` },
            on: { click: () => askDelete(set) } }, S.icon(S.ICON_TRASH)))),
      plates);
  }

  function render() {
    grid.replaceChildren();
    if (!sets.length) {
      grid.append(S.el('div', { class: 'empty-state', text: 'No sets yet. Create your first one with New set.' }));
      return;
    }
    for (const set of sets) grid.append(shelf(set));
  }

  /* Sizes come from the same server function as the editor and the export. */
  async function loadPreviews(set) {
    const texts = set.preview_texts || [];
    set.previews = texts.map((text) => ({ text, font_size: set.font_size }));
    if (!texts.length) return;
    try {
      const res = await S.preview(set.settings, texts, set.font_size);
      set.previews = res.signs;
    } catch (e) { /* keep the set's own size */ }
  }

  async function refresh() {
    try {
      sets = await S.api('GET', '/api/sets');
      await Promise.all(sets.map(loadPreviews));
      render();
    } catch (e) {
      grid.replaceChildren(S.el('div', { class: 'empty-state', text: 'Could not load sets: ' + e.message }));
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
    const first = set && set.previews[0];
    return S.el('label', { class: 'wizard-option' }, radio,
      S.el('span', { class: 'option-text' }, title, S.el('small', { text: sub })),
      set ? S.signSvg(set.settings, first ? first.text : '', first ? first.font_size : null, S.plateScale(set.settings, 150, 44)) : null);
  }

  function openWizard(preselect) {
    chosen = preselect || null;
    $('copy-signs').checked = false;
    copyRow.hidden = !chosen;
    optionsBox.replaceChildren(
      option('', 'Blank', 'Default size and style', null),
      ...sets.map((s) => option(s.name, s.name, facts(s), s)));
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
      $('wizard-preview').replaceChildren(S.signSvg(st, 'Sample', res.font_size, S.plateScale(st, 420, 100)));
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
      nameError.textContent = 'A set with this name already exists.';
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
