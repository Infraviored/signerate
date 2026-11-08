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
    return `${S.num(st.width)} × ${S.num(st.height)} × ${S.num(st.base_thickness)} mm · ${S.fontName(st.font_path)}`;
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

  /* A sign as SVG in mm units. fontSize null = rough guess before the server answered. */
  S.signSvg = function (st, text, fontSize) {
    const w = st.width, h = st.height, m = st.min_margin;
    const size = fontSize || (h - 2 * m) * 0.7;
    let x = w / 2, anchor = 'middle';
    if (st.horizontal_align === 'left') { x = m; anchor = 'start'; }
    else if (st.horizontal_align === 'right') { x = w - m; anchor = 'end'; }
    const label = S.svg('text', {
      x, y: h / 2, fill: st.text_color, 'font-family': S.fontCss(st.font_path),
      'font-size': size, 'text-anchor': anchor, 'dominant-baseline': 'central',
    });
    label.textContent = text;
    return S.svg('svg', { class: 'sign-preview', viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: 'xMidYMid meet' },
      S.svg('rect', { x: 0, y: 0, width: w, height: h, fill: st.bg_color, rx: 1, ry: 1 }),
      label);
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
    if (!fin(s.text_protrusion)) err.text_protrusion = 'Must be a number.';
    if (!err.min_margin && !err.width && !err.height && 2 * s.min_margin >= Math.min(s.width, s.height)) {
      err.min_margin = 'Too large for the width/height.';
    }
    if (!s.font_path) err.font_path = 'Choose a font.';
    return err;
  };

  S.thicknessWarning = function (s) {
    const minZ = s.base_thickness + s.text_protrusion - s.text_thickness;
    if (minZ < -0.01) return { level: 'danger', title: 'POKES THROUGH BACK', detail: `Text is ${Math.abs(minZ).toFixed(2)} mm too deep!` };
    if (minZ < 0.4) return { level: 'warn', title: 'FRAGILE BACK', detail: `Remaining back wall: ${minZ.toFixed(2)} mm` };
    return null;
  };

  const FIELDS = [
    ['width', 'Width (mm)', 'The full horizontal width of each sign plate.', { min: 1, step: 0.5 }],
    ['height', 'Height (mm)', 'The vertical height of each sign plate.', { min: 1, step: 0.5 }],
    ['base_thickness', 'Total Thickness (mm)', 'The thickness of the background plate.', { min: 0.1, step: 0.2 }],
    ['text_thickness', 'Text Thickness (mm)', 'The depth of the text extrusion. Should usually be less than (Total Thickness + Protrusion).', { min: 0.1, step: 0.2 }],
    ['min_margin', 'Min. Margin (mm)', 'Minimum empty space ensured between the text and the sign edges.', { min: 0, step: 0.5 }],
    ['text_protrusion', 'Protrusion (mm)', 'Distance the text sticks out from the front surface. 0 = Flush with top.', { step: 0.1 }],
  ];

  /* Dimension and style fields, shared by the editor panel and the New Set wizard.
     onChange(settings, valid) fires on every edit. */
  S.SettingsForm = function (initial, onChange) {
    const inputs = {};
    let current = { ...initial };

    function labelRow(text, tip, forId) {
      return S.el('div', { class: 'label-wrapper' },
        S.el('label', { text, htmlFor: forId }),
        tip ? S.el('span', { class: 'help-icon', text: '?', attrs: { 'data-tooltip': tip } }) : null);
    }

    function numberField(key, label, tip, attrs) {
      const input = S.el('input', { type: 'number', id: 'f-' + key, value: current[key], attrs });
      input.addEventListener('input', changed);
      inputs[key] = input;
      return S.el('div', { class: 'field-group' }, labelRow(label, tip, 'f-' + key), input);
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

    const num = (key) => FIELDS.find((f) => f[0] === key);
    const pair = (a, b) => S.el('div', { class: 'grid' },
      numberField(a, num(a)[1], num(a)[2], num(a)[3]), numberField(b, num(b)[1], num(b)[2], num(b)[3]));

    const el = S.el('div', { class: 'settings-form' },
      S.el('div', { class: 'form-section', text: 'Dimensions' }),
      pair('width', 'height'),
      pair('base_thickness', 'text_thickness'),
      pair('min_margin', 'text_protrusion'),
      S.el('div', { class: 'form-section', text: 'Style' }),
      S.el('div', { class: 'grid' },
        S.el('div', { class: 'field-group' }, labelRow('Background Color', null, 'f-bg_color'), colorBg),
        S.el('div', { class: 'field-group' }, labelRow('Text Color', null, 'f-text_color'), colorText)),
      S.el('div', { class: 'field-group' }, labelRow('Font', null), picker.el),
      S.el('div', { class: 'field-group' }, labelRow('Horizontal Align', null, 'f-horizontal_align'), align),
      errorLine);

    function read() {
      const s = { ...current };
      for (const [key] of FIELDS) s[key] = inputs[key].value === '' ? NaN : parseFloat(inputs[key].value);
      s.bg_color = colorBg.value;
      s.text_color = colorText.value;
      s.horizontal_align = align.value;
      return s;
    }

    function changed() {
      const s = read();
      const errors = S.checkSettings(s);
      for (const [key] of FIELDS) inputs[key].classList.toggle('invalid', !!errors[key]);
      errorLine.textContent = Object.values(errors)[0] || '';
      current = { ...current, ...s };
      onChange(s, Object.keys(errors).length === 0);
    }

    return {
      el,
      get: read,
      set(s) {
        current = { ...s };
        for (const [key] of FIELDS) inputs[key].value = s[key];
        colorBg.value = s.bg_color;
        colorText.value = s.text_color;
        align.value = s.horizontal_align;
        picker.set(s.font_path);
        for (const [key] of FIELDS) inputs[key].classList.remove('invalid');
        errorLine.textContent = '';
      },
    };
  };

  // --- Font dropdown --------------------------------------------------------

  S.FontPicker = function (value, onSelect) {
    const label = S.el('span');
    const trigger = S.el('div', { class: 'dropdown-trigger', tabIndex: 0 }, label);
    const search = S.el('input', { type: 'text', placeholder: 'Search fonts...' });
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
