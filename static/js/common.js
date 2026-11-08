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

  // --- On-screen keyboard ---------------------------------------------------
  // Where the browser does not resize the page for the keyboard (iOS), the visual
  // viewport shrinks instead. Follow it so the editor never ends up behind the
  // keyboard. `kb-open` lets the CSS drop the export bar while someone types.

  S.coarsePointer = window.matchMedia('(pointer: coarse)').matches;
  const vv = window.visualViewport;
  if (vv) {
    const root = document.documentElement;
    const follow = () => {
      const squeezed = vv.scale <= 1.01 && window.innerHeight - vv.height > 80;
      if (squeezed) {
        root.style.setProperty('--app-h', `${Math.round(vv.height)}px`);
        root.style.setProperty('--app-top', `${Math.round(vv.offsetTop)}px`);
      } else {
        root.style.removeProperty('--app-h');
        root.style.removeProperty('--app-top');
      }
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName || '');
      root.classList.toggle('kb-open', S.coarsePointer && typing && vv.scale <= 1.01 && vv.height < screen.height * 0.72);
    };
    vv.addEventListener('resize', follow);
    vv.addEventListener('scroll', follow);
    document.addEventListener('focusin', follow);
    document.addEventListener('focusout', () => setTimeout(follow, 50));
  }

  // --- API ------------------------------------------------------------------

  S.api =async function (method, url, body, extra) {
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

  // --- Shapes ---------------------------------------------------------------

  S.shapes = [];
  S.loadShapes = async function () {
    if (!S.shapes.length) S.shapes = await S.api('GET', '/api/shapes');
  };

  // --- Sign preview ---------------------------------------------------------

  const LINE_PITCH = 1.2; // same as fonts.LINE_PITCH on the server

  S.guessFontSize = (st) => (st.height - 2 * (st.min_margin + (st.frame_width || 0))) * 0.7;

  /* Pixels per millimetre so a plate fits inside maxW x maxH. */
  S.plateScale = (st, maxW, maxH) => Math.min(maxW / st.width, maxH / st.height);

  let clipSeq = 0;

  /* A sign as SVG in mm units, drawn at `scale` px/mm. fontSize null = rough guess.
     geo (optional, from the server): {outline, x}. outline = {path, w, h} is the contour
     (path null = plain rectangle); x = where the aligned text edge sits, plate-centred.
     The shapes themselves are defined server-side only (outlines.py). An outline made for
     other dimensions is stretched to fit, so a size edit never flashes a wrong shape. */
  S.signSvg = function (st, text, fontSize, scale, geo) {
    geo = geo || {};
    const outline = st.shape && st.shape !== 'rect' && geo.outline && geo.outline.path ? geo.outline : null;
    const w = st.width, h = st.height, f = st.frame_width || 0, m = st.min_margin + f;
    const size = fontSize || S.guessFontSize(st);
    let x = w / 2, anchor = 'middle';
    if (st.horizontal_align === 'left') { x = m; anchor = 'start'; }
    else if (st.horizontal_align === 'right') { x = w - m; anchor = 'end'; }
    if (typeof geo.x === 'number' && anchor !== 'middle') x = w / 2 + geo.x;
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
    let body;
    if (outline) {
      const id = 'plate-clip-' + (++clipSeq);
      const sx = w / outline.w, sy = h / outline.h;
      const stretch = Math.abs(sx - 1) > 1e-9 || Math.abs(sy - 1) > 1e-9 ? { transform: `scale(${sx} ${sy})` } : {};
      // The frame is a stroke of twice its width, clipped to the contour: the inner edge is
      // the contour moved in by exactly the frame width.
      body = [S.svg('g', stretch,
        S.svg('path', { d: outline.path, fill: st.bg_color }),
        f > 0 ? S.svg('clipPath', { id }, S.svg('path', { d: outline.path })) : null,
        f > 0 ? S.svg('path', { d: outline.path, fill: 'none', stroke: st.text_color, 'stroke-width': 2 * f, 'clip-path': `url(#${id})` }) : null)];
    } else {
      body = [
        S.svg('rect', { x: 0, y: 0, width: w, height: h, fill: st.bg_color, rx: 0.8, ry: 0.8 }),
        f > 0 ? S.svg('rect', { x: f / 2, y: f / 2, width: w - f, height: h - f, fill: 'none', stroke: st.text_color, 'stroke-width': f }) : null,
      ];
    }
    return S.svg('svg', { class: 'sign-svg', width: w * scale, height: h * scale, viewBox: `0 0 ${w} ${h}` }, ...body, ...labels);
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
    // Ranges depend on the plate and the shape, so the server has the last word (shown in the same error line).
    for (const [key, v] of Object.entries(s.shape_params || {})) {
      if (!fin(v) || v < 0) err['shape_' + key] = 'Shape values must be 0 or more.';
    }
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
    const DEFAULTS = { frame_width: 0, shape: 'rect', shape_params: {} };
    let current = { ...DEFAULTS, ...initial };
    let outline = null; // newest outline from the server: its automatic values are the placeholders
    const shapeInputs = {};

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

    // Shape picker: thumbnails drawn from the server's outlines, then only the fields of the chosen shape.
    const shapePicker = S.el('div', { class: 'shape-picker', attrs: { role: 'radiogroup', 'aria-label': 'Shape' } });
    const shapeButtons = new Map();
    for (const sh of S.shapes) {
      const art = sh.path ? S.svg('path', { d: sh.path }) : S.svg('rect', { width: 300, height: 120 });
      const btn = S.el('button', { type: 'button', class: 'shape-option', dataset: { shape: sh.id },
        attrs: { role: 'radio', 'aria-checked': 'false' }, on: { click: () => pickShape(sh.id) } },
        S.svg('svg', { viewBox: '-4 -4 308 128', 'aria-hidden': 'true' }, art),
        S.el('span', { text: sh.name }));
      shapeButtons.set(sh.id, btn);
      shapePicker.append(btn);
    }
    const shapeFields = S.el('div', { class: 'shape-fields' });
    const shapeHint = S.el('div', { class: 'field-hint', text: 'Leave a field empty for the automatic value.' });
    const shapeInfo = (id) => S.shapes.find((sh) => sh.id === id) || { params: [] };
    const autoText = (key) => {
      const p = outline && outline.shape === current.shape && outline.params[key];
      return p ? S.num(p.auto) : 'Auto';
    };

    function renderShape() {
      for (const [id, btn] of shapeButtons) {
        btn.classList.toggle('active', id === current.shape);
        btn.setAttribute('aria-checked', String(id === current.shape));
      }
      for (const k of Object.keys(shapeInputs)) delete shapeInputs[k];
      const params = shapeInfo(current.shape).params;
      const fields = params.map((p) => {
        const given = (current.shape_params || {})[p.key];
        const input = S.el('input', { type: 'number', id: 'f-shape_' + p.key, value: given === undefined || given === null ? '' : given,
          placeholder: autoText(p.key), attrs: { min: 0, step: p.unit ? 0.5 : 0.1 } });
        input.addEventListener('input', changed);
        shapeInputs[p.key] = input;
        return S.el('div', { class: 'field' }, label(p.unit ? `${p.label} (${p.unit})` : p.label, 'f-shape_' + p.key), input);
      });
      shapeFields.replaceChildren(...fields);
      shapeHint.hidden = !params.length;
    }

    function pickShape(id) {
      if (id === current.shape) return;
      current = { ...current, shape: id, shape_params: {} };
      renderShape();
      changed();
    }

    const el = S.el('div', { class: 'settings-form' },
      S.el('h3', { class: 'form-section', text: 'Plate' }),
      pair(numberField('width'), numberField('height')),
      pair(numberField('base_thickness'), numberField('min_margin')),
      pair(numberField('frame_width')),
      S.el('h3', { class: 'form-section', text: 'Shape' }),
      shapePicker, shapeFields, shapeHint,
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
    renderShape();

    function read() {
      const s = { ...current };
      for (const [key] of FIELDS) s[key] = inputs[key].value === '' ? NaN : parseFloat(inputs[key].value);
      s.bg_color = colorBg.value;
      s.text_color = colorText.value;
      s.horizontal_align = align.value;
      s.shape = current.shape;
      s.shape_params = {};
      for (const [key, input] of Object.entries(shapeInputs)) {
        if (input.value !== '') s.shape_params[key] = parseFloat(input.value);
      }
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
      for (const [key, input] of Object.entries(shapeInputs)) input.classList.toggle('invalid', !!errors['shape_' + key]);
      errorLine.textContent = Object.values(errors)[0] || '';
      current = { ...current, ...s };
      onChange(s, Object.keys(errors).length === 0);
    }

    return {
      el,
      get: read,
      /* The server's outline for the current settings: fills the placeholders with the automatic values. */
      setOutline(o) {
        outline = o;
        for (const [key, input] of Object.entries(shapeInputs)) input.placeholder = autoText(key);
      },
      /* A message from the server about these settings (e.g. a value out of range for this plate). */
      setError(message) {
        errorLine.textContent = message || Object.values(S.checkSettings(read()))[0] || '';
      },
      set(s) {
        current = { ...DEFAULTS, ...s };
        renderShape();
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
        if (!S.coarsePointer) search.focus(); // on touch the keyboard would cover the list
        const sel = list.querySelector('.selected');
        if (sel) sel.scrollIntoView({ block: 'nearest' });
        if (getComputedStyle(content).position === 'static') content.scrollIntoView({ block: 'nearest' }); // inline list on phones
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
