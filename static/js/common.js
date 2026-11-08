/* Shared helpers for both pages. Everything user-controlled goes through
   textContent / DOM properties, never through innerHTML. */
(function () {
  'use strict';
  const S = (window.Signerate = {});
  const SVG_NS = 'http://www.w3.org/2000/svg';

  // --- DOM helpers ----------------------------------------------------------

  function apply(node, props) {
    for (const [key, value] of Object.entries(props || {})) {
      if (value === undefined || value === null) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key === 'on') for (const [ev, fn] of Object.entries(value)) node.addEventListener(ev, fn);
      else if (key === 'attrs') for (const [a, v] of Object.entries(value)) node.setAttribute(a, v);
      else if (key === 'dataset') Object.assign(node.dataset, value);
      else node[key] = value;
    }
  }

  S.el = function (tag, props, ...children) {
    const node = document.createElement(tag);
    apply(node, props);
    for (const c of children.flat()) if (c) node.append(c);
    return node;
  };

  S.svg = function (tag, attrs, ...children) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [a, v] of Object.entries(attrs || {})) node.setAttribute(a, v);
    for (const c of children) if (c) node.append(c);
    return node;
  };

  S.icon = function (pathData) {
    return S.svg('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' },
      ...pathData.map((d) => S.svg('path', { d })));
  };

  S.ICON_TRASH = ['M3 6h18', 'M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2', 'M10 11v6', 'M14 11v6'];
  S.ICON_CLOSE = ['M18 6L6 18', 'M6 6l12 12'];
  S.ICON_CHECK = ['M20 6L9 17l-5-5'];
  S.ICON_RENEW = ['M3 12a9 9 0 1 0 3-6.7L3 8', 'M3 3v5h5'];

  S.debounce = function (fn, ms) {
    let t = null;
    const wrapped = (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
    wrapped.cancel = () => clearTimeout(t);
    return wrapped;
  };

  // --- API ------------------------------------------------------------------

  S.api = async function (method, url, body, extra) {
    const init = { method, ...extra };
    if (body !== undefined) {
      init.headers = { 'Content-Type': 'application/json' };
      init.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(url, init);
    } catch (e) {
      throw new Error('Cannot reach the server.');
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) {
      const err = new Error((data && data.error) || `Request failed (${res.status})`);
      err.status = res.status;
      throw err;
    }
    return data;
  };

  S.setUrl = (name) => '/api/sets/' + encodeURIComponent(name);
  S.editorUrl = (name) => '/set/' + encodeURIComponent(name);

  // --- Formatting -----------------------------------------------------------

  S.num = (v) => String(+Number(v).toFixed(2));
  S.shortDate = (iso) => new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });

  S.summaryText = function (st) {
    return `${S.num(st.width)} × ${S.num(st.height)} × ${S.num(st.base_thickness)} mm, ${S.fontName(st.font_path)}`;
  };

  // --- Fonts ----------------------------------------------------------------

  S.fonts = { list: [], index: new Map() };

  S.loadFonts = async function () {
    S.fonts.list = await S.api('GET', '/api/fonts');
    S.fonts.index = new Map(S.fonts.list.map((f, i) => [f.path, { name: f.name, face: `FontPreview_${i + 1}` }]));
    const css = S.fonts.list.map((f, i) =>
      `@font-face { font-family: 'FontPreview_${i + 1}'; src: url('/api/font?path=${encodeURIComponent(f.path)}'); }`
    ).join('\n');
    let style = document.getElementById('dynamic-fonts');
    if (!style) {
      style = document.createElement('style');
      style.id = 'dynamic-fonts';
      document.head.append(style);
    }
    style.textContent = css;
  };

  S.fontCss = (path) => {
    const f = S.fonts.index.get(path);
    return f ? `'${f.face}', sans-serif` : 'sans-serif';
  };

  S.fontName = (path) => {
    const f = S.fonts.index.get(path);
    if (f) return f.name;
    return (path || '').split('/').pop().replace(/\.[^.]+$/, '') || 'No font';
  };

  // --- Sign preview ---------------------------------------------------------

  const LINE_PITCH = 1.2; // same as fonts.LINE_PITCH on the server

  S.guessFontSize = (st) => (st.height - 2 * (st.min_margin + (st.frame_width || 0))) * 0.7;

  /* Pixels per millimetre so a plate fits inside maxW x maxH. */
  S.plateScale = (st, maxW, maxH) => Math.min(maxW / st.width, maxH / st.height);

  /* A sign as SVG in mm units, drawn at `scale` px/mm. fontSize null = rough guess. */
  S.signSvg = function (st, text, fontSize, scale) {
    const w = st.width, h = st.height, f = st.frame_width || 0, m = st.min_margin + f;
    const size = fontSize || S.guessFontSize(st);
    let x = w / 2, anchor = 'middle';
    if (st.horizontal_align === 'left') { x = m; anchor = 'start'; }
    else if (st.horizontal_align === 'right') { x = w - m; anchor = 'end'; }
    const lines = String(text).split('\n');
    const labels = lines.map((line, n) => {
      const label = S.svg('text', {
        x, y: h / 2 + (n - (lines.length - 1) / 2) * LINE_PITCH * size,
        fill: st.text_color, 'font-family': S.fontCss(st.font_path),
        'font-size': size, 'text-anchor': anchor, 'dominant-baseline': 'central',
      });
      label.textContent = line;
      return label;
    });
    return S.svg('svg', { class: 'sign-svg', width: w * scale, height: h * scale, viewBox: `0 0 ${w} ${h}` },
      S.svg('rect', { x: 0, y: 0, width: w, height: h, fill: st.bg_color, rx: 0.8, ry: 0.8 }),
      f > 0 ? S.svg('rect', { x: f / 2, y: f / 2, width: w - f, height: h - f, fill: 'none', stroke: st.text_color, 'stroke-width': f }) : null,
      ...labels);
  };

  S.preview = (settings, texts, fontSize) =>
    S.api('POST', '/api/preview', { settings, texts, font_size: fontSize || null });

  // --- Settings -------------------------------------------------------------

  /* Mirrors the server validation; returns {key: message}. */
  S.checkSettings = function (s) {
    const err = {};
    const fin = (v) => typeof v === 'number' && Number.isFinite(v);
    for (const k of ['width', 'height', 'base_thickness', 'text_thickness']) {
      if (!fin(s[k]) || s[k] <= 0) err[k] = 'Must be greater than 0.';
    }
    if (!fin(s.min_margin) || s.min_margin < 0) err.min_margin = 'Must be 0 or more.';
    if (!fin(s.frame_width) || s.frame_width < 0) err.frame_width = 'Must be 0 or more.';
    if (!fin(s.text_protrusion)) err.text_protrusion = 'Must be a number.';
    if (!err.min_margin && !err.frame_width && !err.width && !err.height
        && 2 * (s.min_margin + s.frame_width) >= Math.min(s.width, s.height)) {
      err.min_margin = 'Margin and frame are too large for this plate.';
    }
    if (!s.font_path) err.font_path = 'Choose a font.';
    if (s.text_size !== null && (!fin(s.text_size) || s.text_size <= 0)) err.text_size = 'Text size must be greater than 0.';
    return err;
  };

  S.thicknessWarning = function (s) {
    const minZ = s.base_thickness + s.text_protrusion - s.text_thickness;
    if (minZ < -0.01) return { level: 'danger', text: `The text goes through the back of the plate. Reduce the text depth by ${Math.abs(minZ).toFixed(2)} mm.` };
    if (minZ < 0.4) return { level: 'warn', text: `Thin back wall: ${minZ.toFixed(2)} mm behind the text.` };
    return null;
  };

  const FIELDS = [
    ['width', 'Width (mm)', '', { min: 1, step: 0.5 }],
    ['height', 'Height (mm)', '', { min: 1, step: 0.5 }],
    ['base_thickness', 'Thickness (mm)', '', { min: 0.1, step: 0.2 }],
    ['min_margin', 'Margin (mm)', 'Kept free around the text.', { min: 0, step: 0.5 }],
    ['frame_width', 'Frame (mm)', '0 is no frame. Uses the text colour.', { min: 0, step: 0.5 }],
    ['text_thickness', 'Text depth (mm)', 'How deep the letters go.', { min: 0.1, step: 0.2 }],
    ['text_protrusion', 'Raised by (mm)', '0 is flush with the plate.', { step: 0.1 }],
  ];

  /* Dimension and style fields, shared by the editor panel and the New Set wizard.
     onChange(settings, valid) fires on every edit. suggestSize() prefills a fixed text size. */
  S.SettingsForm = function (initial, onChange, suggestSize) {
    const inputs = {};
    let current = { frame_width: 0, ...initial };

    const FIT_HINT = 'Fills the width or height. The longest sign sets the size for all.';
    const FIXED_HINT = 'A sign too long for this size shrinks to fit.';
    const sizeFit = S.el('button', { type: 'button', text: 'Fit plate', on: { click: () => setFixed(false) } });
    const sizeFixed = S.el('button', { type: 'button', text: 'Fixed', on: { click: () => setFixed(true) } });
    const sizeInput = S.el('input', { type: 'number', id: 'f-text_size', attrs: { min: 0.5, step: 0.5, 'aria-label': 'Fixed text size in mm' } });
    const sizeHint = S.el('div', { class: 'field-hint' });
    sizeInput.addEventListener('input', changed);

    function showSizeMode(fixed) {
      sizeFit.classList.toggle('active', !fixed);
      sizeFixed.classList.toggle('active', fixed);
      sizeInput.hidden = !fixed;
      sizeHint.textContent = fixed ? FIXED_HINT : FIT_HINT;
    }

    function setFixed(fixed) {
      if (fixed && sizeInput.value === '') {
        const guess = (suggestSize && suggestSize()) || S.guessFontSize(read());
        sizeInput.value = Math.round(guess * 2) / 2 || 1;
      }
      showSizeMode(fixed);
      changed();
      if (fixed) sizeInput.focus();
    }

    function showSize(value) {
      sizeInput.value = value || '';
      showSizeMode(!!value);
    }

    const label = (text, forId) => S.el('label', { text, htmlFor: forId || '' });

    function numberField(key) {
      const [, text, hint, attrs] = FIELDS.find((f) => f[0] === key);
      const input = S.el('input', { type: 'number', id: 'f-' + key, value: current[key], attrs });
      input.addEventListener('input', changed);
      inputs[key] = input;
      return S.el('div', { class: 'field' }, label(text, 'f-' + key), input,
        hint ? S.el('div', { class: 'field-hint', text: hint }) : null);
    }

    const colorBg = S.el('input', { type: 'color', id: 'f-bg_color', value: current.bg_color });
    const colorText = S.el('input', { type: 'color', id: 'f-text_color', value: current.text_color });
    const align = S.el('select', { id: 'f-horizontal_align' },
      ...['left', 'center', 'right'].map((v) => S.el('option', { value: v, text: v[0].toUpperCase() + v.slice(1) })));
    align.value = current.horizontal_align;
    const picker = S.FontPicker(current.font_path, (path) => { current.font_path = path; changed(); });
    const errorLine = S.el('div', { class: 'field-error' });
    for (const el of [colorBg, colorText]) el.addEventListener('input', changed);
    align.addEventListener('change', changed);

    const pair = (...children) => S.el('div', { class: 'field-pair' }, ...children);

    const el = S.el('div', { class: 'settings-form' },
      S.el('h3', { class: 'form-section', text: 'Plate' }),
      pair(numberField('width'), numberField('height')),
      pair(numberField('base_thickness'), numberField('min_margin')),
      pair(numberField('frame_width')),
      S.el('h3', { class: 'form-section', text: 'Text' }),
      S.el('div', { class: 'field' }, label('Font'), picker.el),
      S.el('div', { class: 'field' }, label('Text size'),
        S.el('div', { class: 'size-row' },
          S.el('div', { class: 'segmented segmented-light', attrs: { role: 'group', 'aria-label': 'Text size mode' } }, sizeFit, sizeFixed),
          S.el('label', { class: 'size-input' }, sizeInput)),
        sizeHint),
      pair(numberField('text_thickness'), numberField('text_protrusion')),
      S.el('div', { class: 'field' }, label('Alignment', 'f-horizontal_align'), align),
      S.el('h3', { class: 'form-section', text: 'Colours' }),
      pair(S.el('div', { class: 'field' }, label('Plate', 'f-bg_color'), colorBg),
        S.el('div', { class: 'field' }, label('Text', 'f-text_color'), colorText)),
      errorLine);

    showSize(current.text_size);

    function read() {
      const s = { ...current };
      for (const [key] of FIELDS) s[key] = inputs[key].value === '' ? NaN : parseFloat(inputs[key].value);
      s.bg_color = colorBg.value;
      s.text_color = colorText.value;
      s.horizontal_align = align.value;
      s.text_size = sizeFixed.classList.contains('active')
        ? (sizeInput.value === '' ? NaN : parseFloat(sizeInput.value))
        : null;
      return s;
    }

    function changed() {
      const s = read();
      const errors = S.checkSettings(s);
      for (const [key] of FIELDS) inputs[key].classList.toggle('invalid', !!errors[key]);
      sizeInput.classList.toggle('invalid', !!errors.text_size);
      errorLine.textContent = Object.values(errors)[0] || '';
      current = { ...current, ...s };
      onChange(s, Object.keys(errors).length === 0);
    }

    return {
      el,
      get: read,
      set(s) {
        current = { frame_width: 0, ...s };
        for (const [key] of FIELDS) inputs[key].value = current[key];
        colorBg.value = s.bg_color;
        colorText.value = s.text_color;
        align.value = s.horizontal_align;
        picker.set(s.font_path);
        showSize(s.text_size);
        sizeInput.classList.remove('invalid');
        for (const [key] of FIELDS) inputs[key].classList.remove('invalid');
        errorLine.textContent = '';
      },
    };
  };

  // --- Font dropdown --------------------------------------------------------

  S.FontPicker = function (value, onSelect) {
    const label = S.el('span');
    const trigger = S.el('div', { class: 'dropdown-trigger', tabIndex: 0 }, label);
    const search = S.el('input', { type: 'text', placeholder: 'Search fonts', attrs: { 'aria-label': 'Search fonts' } });
    const list = S.el('div', { class: 'dropdown-list' });
    const content = S.el('div', { class: 'dropdown-content' },
      S.el('div', { class: 'dropdown-search' }, search), list);
    const root = S.el('div', { class: 'custom-dropdown' }, trigger, content);
    let selected = value;

    function showLabel() {
      label.textContent = S.fontName(selected);
      label.style.fontFamily = S.fontCss(selected);
    }

    function close() {
      content.classList.remove('show');
      trigger.classList.remove('active');
    }

    for (const f of S.fonts.list) {
      const opt = S.el('div', { class: 'dropdown-option', text: f.name, dataset: { path: f.path, name: f.name.toLowerCase() } });
      opt.style.fontFamily = S.fontCss(f.path);
      if (f.path === selected) opt.classList.add('selected');
      opt.addEventListener('click', () => {
        selected = f.path;
        list.querySelectorAll('.selected').forEach((o) => o.classList.remove('selected'));
        opt.classList.add('selected');
        showLabel();
        close();
        onSelect(f.path);
      });
      list.append(opt);
    }

    trigger.addEventListener('click', () => {
      const open = content.classList.contains('show');
      close();
      if (!open) {
        content.classList.add('show');
        trigger.classList.add('active');
        search.focus();
        const sel = list.querySelector('.selected');
        if (sel) sel.scrollIntoView({ block: 'nearest' });
      }
    });
    search.addEventListener('input', () => {
      const q = search.value.toLowerCase();
      for (const opt of list.children) opt.hidden = !opt.dataset.name.includes(q);
    });
    document.addEventListener('click', (e) => { if (!root.contains(e.target)) close(); });
    showLabel();

    return {
      el: root,
      set(path) {
        selected = path;
        list.querySelectorAll('.selected').forEach((o) => o.classList.remove('selected'));
        for (const opt of list.children) if (opt.dataset.path === path) opt.classList.add('selected');
        showLabel();
      },
    };
  };

  // --- Toasts ---------------------------------------------------------------

  /* opts: {type: 'error', action: {label, fn}, ms}. Returns a function that dismisses it. */
  S.toast = function (message, opts) {
    opts = opts || {};
    let box = document.querySelector('.toasts');
    if (!box) {
      box = S.el('div', { class: 'toasts' });
      document.body.append(box);
    }
    const node = S.el('div', { class: 'toast' + (opts.type === 'error' ? ' error' : ''), attrs: { role: 'status' } },
      S.el('span', { text: message }));
    const dismiss = () => { clearTimeout(timer); node.remove(); };
    if (opts.action) {
      node.append(S.el('button', { text: opts.action.label, on: { click: () => { dismiss(); opts.action.fn(); } } }));
    }
    const timer = setTimeout(dismiss, opts.ms || 5000);
    box.append(node);
    return dismiss;
  };
})();
