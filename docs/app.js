(function () {
  'use strict';

  var LOCALES = [
    ['pt-BR', 'Português'], ['en', 'English'], ['ja', '日本語'], ['es', 'Español'], ['zh-CN', '简体中文']
  ];
  var GROUP_ORDER = ['start', 'core', 'body', 'release'];
  var ALIASES = { avatar: 'corpo', universo: 'mundo-3d' };
  var ANCHOR_RE = /^s-[A-Za-z0-9_-]+$/;

  var FALLBACK_UI = {
    brand: 'Célia-sama', label: 'Documentation', search: 'Search', searchPlaceholder: 'Search',
    noResults: 'No results', theme: 'Theme', light: 'Light', dark: 'Dark', language: 'Language',
    menu: 'Menu', onPage: 'On this page', skip: 'Skip to content', home: 'Home',
    previous: 'Previous', next: 'Next', comingSoon: 'Coming soon',
    routeIndex: 'Paths', copy: 'Copy', copied: 'Copied', related: 'Links', permalink: 'Direct link',
    details: 'Details', breadcrumb: 'Breadcrumb', pagination: 'Page navigation',
    models: 'Models', start: 'Get started', guides: 'Guides', mainNav: 'Main', project: 'Project',
    copyCode: 'Copy code', note: 'Note', footer: 'Documentation'
  };
  /* textos novos usados quando content.js não os traz */
  var BUILTIN = {
    'pt-BR': { routeIndex: 'Caminhos', copy: 'Copiar', copied: 'Copiado', related: 'Links', permalink: 'Link direto', details: 'Ver detalhes', breadcrumb: 'Trilha de navegação', pagination: 'Navegação entre páginas',
      models: 'Modelos', start: 'Começar', guides: 'Guias', mainNav: 'Principal', project: 'Projeto', copyCode: 'Copiar código', note: 'Nota', footer: 'Documentação',
      groups: { start: 'Começar', core: 'Arquitetura', body: 'Corpo e mundo', release: 'Release' } },
    en: { routeIndex: 'Paths', copy: 'Copy', copied: 'Copied', related: 'Links', permalink: 'Direct link', details: 'Details', breadcrumb: 'Breadcrumb', pagination: 'Page navigation',
      models: 'Models', start: 'Get started', guides: 'Guides', mainNav: 'Main', project: 'Project', copyCode: 'Copy code', note: 'Note', footer: 'Documentation',
      groups: { start: 'Getting started', core: 'Architecture', body: 'Body and world', release: 'Release' } },
    ja: { routeIndex: 'ガイド一覧', copy: 'コピー', copied: 'コピーしました', related: 'リンク', permalink: '直接リンク', details: '詳細', breadcrumb: 'パンくず', pagination: 'ページ移動',
      models: 'モデル', start: 'はじめる', guides: 'ガイド', mainNav: 'メイン', project: 'プロジェクト', copyCode: 'コードをコピー', note: '注記', footer: 'ドキュメント',
      groups: { start: 'はじめに', core: 'アーキテクチャ', body: '身体とワールド', release: 'リリース' } },
    es: { routeIndex: 'Rutas', copy: 'Copiar', copied: 'Copiado', related: 'Enlaces', permalink: 'Enlace directo', details: 'Ver detalles', breadcrumb: 'Ruta de navegación', pagination: 'Navegación entre páginas',
      models: 'Modelos', start: 'Empezar', guides: 'Guías', mainNav: 'Principal', project: 'Proyecto', copyCode: 'Copiar código', note: 'Nota', footer: 'Documentación',
      groups: { start: 'Primeros pasos', core: 'Arquitectura', body: 'Cuerpo y mundo', release: 'Versión' } },
    'zh-CN': { routeIndex: '导航路径', copy: '复制', copied: '已复制', related: '链接', permalink: '直达链接', details: '查看详情', breadcrumb: '面包屑导航', pagination: '页面导航',
      models: '模型', start: '开始使用', guides: '指南', mainNav: '主导航', project: '项目', copyCode: '复制代码', note: '注意', footer: '文档',
      groups: { start: '开始', core: '架构', body: '形象与世界', release: '发布' } }
  };
  var DOCS = (typeof window.CELIA_DOCS === 'object' && window.CELIA_DOCS) || {};

  var $ = function (id) { return document.getElementById(id); };
  var el = function (tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  var CHEVRON = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false"><path d="m6 9 6 6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  var HOME_ICO = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false"><path d="M4 11.5 12 5l8 6.5V19a1 1 0 0 1-1 1h-4v-5H9v5H5a1 1 0 0 1-1-1Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>';
  var DOC_GLYPH = '<svg viewBox="0 0 24 24" width="30" height="30" aria-hidden="true" focusable="false"><path d="M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  /* armazenamento tolerante (file://, modo privado) */
  var mem = {};
  function store(k, v) {
    if (v === undefined) {
      try { var r = window.localStorage.getItem(k); if (r) return r; } catch (e) { /* ignore */ }
      return mem[k] || null;
    }
    mem[k] = v;
    try { window.localStorage.setItem(k, v); } catch (e2) { /* ignore */ }
    return null;
  }

  /* state.page === '' significa Início (fora do shell da documentação) */
  var state = { lang: 'pt-BR', page: '', anchor: '', theme: 'light' };
  var collapsed = {};
  var index = {};
  var spy = null;

  function queryLang() {
    try { return new URLSearchParams(window.location.search).get('lang'); } catch (e) { return null; }
  }
  function validLang(l) {
    if (!l) return null;
    var s = String(l).toLowerCase(), i;
    for (i = 0; i < LOCALES.length; i++) if (LOCALES[i][0].toLowerCase() === s) return LOCALES[i][0];
    var short = s.slice(0, 2);
    for (i = 0; i < LOCALES.length; i++) if (LOCALES[i][0].slice(0, 2).toLowerCase() === short) return LOCALES[i][0];
    return null;
  }
  function data() { return DOCS[state.lang] || DOCS['pt-BR'] || DOCS.en || { ui: {}, pages: [] }; }
  function ui(k) {
    var u = data().ui || {};
    if (u[k] != null && u[k] !== '') return u[k];
    var b = BUILTIN[state.lang] || {};
    if (b[k]) return b[k];
    var en = (DOCS.en && DOCS.en.ui) || {};
    if (en[k]) return en[k];
    return FALLBACK_UI[k] != null ? FALLBACK_UI[k] : '';
  }
  function groupLabel(g) {
    var u = data().ui || {};
    if (!g) return ui('label');
    if (u.groups && u.groups[g]) return u.groups[g];
    var b = BUILTIN[state.lang] || {};
    if (b.groups && b.groups[g]) return b.groups[g];
    return g;
  }
  function pages() { return data().pages || []; }
  function pageById(id) { var p = pages(); for (var i = 0; i < p.length; i++) if (p[i].id === id) return p[i]; return null; }
  function resolveId(id) {
    if (!id) return null;
    if (pageById(id)) return id;
    if (ALIASES[id] && pageById(ALIASES[id])) return ALIASES[id];
    return null;
  }
  /* '' / 'home' = Início; null = rota inexistente */
  function resolveRoute(id) {
    if (id === '' || id === 'home') return '';
    return resolveId(id);
  }

  /* grupos na ordem da sidebar; páginas sem grupo ficam primeiro */
  function groups() {
    var map = {}, order = [];
    pages().forEach(function (p) {
      var g = p.group || '';
      if (!map[g]) { map[g] = []; order.push(g); }
      map[g].push(p);
    });
    order.sort(function (a, b) {
      function rank(x) { if (!x) return -1; var i = GROUP_ORDER.indexOf(x); return i < 0 ? GROUP_ORDER.length : i; }
      return rank(a) - rank(b);
    });
    return order.map(function (g) { return { key: g, label: groupLabel(g), pages: map[g] }; });
  }
  function ordered() {
    var out = [];
    groups().forEach(function (g) { out = out.concat(g.pages); });
    return out;
  }

  /* destinos do Início, com padrões quando a página ainda não existe */
  function firstOf(ids) { for (var i = 0; i < ids.length; i++) { var r = resolveId(ids[i]); if (r) return r; } return null; }
  function firstDoc() { var o = ordered(); return o.length ? o[0].id : null; }
  function startTarget() { return firstOf(['comecar', 'inicio']) || firstDoc(); }
  function docsTarget() { return firstOf(['arquitetura', 'inicio']) || firstDoc(); }
  function modelsTarget() {
    var t = resolveId('modelos');
    if (t) return t;
    var withModels = pages().filter(function (p) { return Array.isArray(p.models) && p.models.length; });
    return withModels.length ? withModels[0].id : null;
  }
  function homeData() { var h = data().home; return (h && typeof h === 'object') ? h : {}; }
  function homeText(k, uiKey) {
    var h = homeData();
    return (h[k] != null && String(h[k]).trim() !== '') ? String(h[k]) : ui(uiKey);
  }

  /* URLs reais: ?lang=xx#pagina/secao. O href sempre leva o idioma, então
     abrir em nova aba, copiar o link ou clicar com Ctrl funciona nativamente. */
  function href(id, anchor) {
    return '?lang=' + encodeURIComponent(state.lang) + (id ? '#' + id + (anchor ? '/' + anchor : '') : '');
  }
  function routeLink(a, id, anchor) {
    a.href = href(id, anchor);
    a.setAttribute('data-route', id);
    if (anchor) a.setAttribute('data-anchor', anchor);
    return a;
  }
  function readRoute() {
    var h = (window.location.hash || '').replace(/^#\/?/, '');
    var q = h.indexOf('?');
    if (q >= 0) h = h.slice(0, q);
    try { h = decodeURIComponent(h); } catch (e) { /* keep */ }
    var parts = h.split('/');
    var id = parts[0] || '', anchor = parts[1] || '';
    var anchorOnly = false;
    if (ANCHOR_RE.test(id) && !pageById(id)) { anchor = id; id = ''; anchorOnly = true; }
    return { id: id, anchor: ANCHOR_RE.test(anchor) ? anchor : '', anchorOnly: anchorOnly };
  }
  function currentUrl() { return (window.location.search || '') + (window.location.hash || ''); }
  function writeUrl(replace) {
    var url = href(state.page, state.anchor);
    try {
      if (replace) window.history.replaceState(null, '', url);
      else window.history.pushState(null, '', url);
    } catch (e) {
      try { window.location.hash = state.page + (state.anchor ? '/' + state.anchor : ''); } catch (e2) { /* ignore */ }
    }
  }

  function safeUrl(u) {
    u = String(u == null ? '' : u).trim();
    if (!u) return '';
    if (/^(https?:\/\/|mailto:)/i.test(u)) return u;
    if (/^[a-z][a-z0-9+.\-]*:/i.test(u) || u.indexOf('//') === 0) return '';
    return u;
  }

  function setMeta(sel, attr, val) {
    var m = document.querySelector(sel);
    if (m) m.setAttribute(attr, val);
  }

  /* ---------- chrome: textos, navegação do topo, rodapé ---------- */
  function applyChrome() {
    var d = document.documentElement;
    d.lang = state.lang;
    d.setAttribute('data-theme', state.theme);
    $('skip').textContent = ui('skip');
    $('brandText').textContent = ui('brand');
    routeLink($('brand'), '', '');
    $('brand').setAttribute('aria-label', ui('brand') + ' - ' + ui('home'));
    $('searchLabel').textContent = ui('search');
    $('searchBtnLabel').textContent = ui('search');
    $('q').placeholder = ui('searchPlaceholder');
    $('langLabel').textContent = ui('language');
    $('menuLabel').textContent = ui('menu');
    $('menuBtn').setAttribute('aria-label', ui('menu'));
    $('sidebar').setAttribute('aria-label', ui('label'));
    $('tocTitle').textContent = ui('onPage');
    $('toc').setAttribute('aria-label', ui('onPage'));
    var themeText = ui('theme') + ': ' + (state.theme === 'dark' ? ui('dark') : ui('light'));
    $('themeLabel').textContent = themeText;
    $('themeBtn').setAttribute('aria-label', themeText);
    $('themeBtn').setAttribute('title', themeText);
    $('themeBtn').setAttribute('aria-pressed', state.theme === 'dark' ? 'true' : 'false');
    var sel = $('lang');
    if (!sel.options.length) {
      LOCALES.forEach(function (l) { var o = el('option', null, l[1]); o.value = l[0]; o.lang = l[0]; sel.appendChild(o); });
    }
    sel.value = state.lang;
  }

  function topItems() {
    var items = [{ key: 'home', id: '', label: ui('home') }];
    var dt = docsTarget(); if (dt) items.push({ key: 'docs', id: dt, label: ui('label') });
    var mt = modelsTarget(); if (mt) items.push({ key: 'models', id: mt, label: ui('models') });
    return items;
  }
  function buildTopNav() {
    var nav = $('mainNav'); nav.textContent = '';
    nav.setAttribute('aria-label', ui('mainNav'));
    topItems().forEach(function (it) {
      var a = routeLink(el('a', null, it.label), it.id, '');
      a.setAttribute('data-nav', it.key);
      nav.appendChild(a);
    });
  }
  function markTopNav() {
    var mt = modelsTarget();
    var key = state.page === '' ? 'home' : (mt && state.page === mt ? 'models' : 'docs');
    var links = $('mainNav').querySelectorAll('a');
    for (var i = 0; i < links.length; i++) {
      if (links[i].getAttribute('data-nav') === key) links[i].setAttribute('aria-current', 'page');
      else links[i].removeAttribute('aria-current');
    }
  }

  function buildFooter() {
    var f = $('footer'); f.textContent = '';
    var inn = el('div', 'foot-in'), grid = el('div', 'foot-grid');
    groups().forEach(function (g) {
      var col = el('div', 'foot-col');
      col.appendChild(el('h2', 'foot-h', g.label));
      var ul = el('ul');
      g.pages.forEach(function (p) {
        var li = el('li'); li.appendChild(routeLink(el('a', null, p.title), p.id, '')); ul.appendChild(li);
      });
      col.appendChild(ul); grid.appendChild(col);
    });
    var proj = el('div', 'foot-col');
    proj.appendChild(el('h2', 'foot-h', ui('project')));
    var pul = el('ul');
    topItems().forEach(function (it) {
      var li = el('li'); li.appendChild(routeLink(el('a', null, it.label), it.id, '')); pul.appendChild(li);
    });
    proj.appendChild(pul); grid.appendChild(proj);
    inn.appendChild(grid);
    inn.appendChild(el('p', 'foot-note', ui('brand') + ' · ' + ui('footer')));
    f.appendChild(inn);
  }

  /* ---------- sidebar com grupos recolhíveis ---------- */
  function buildNav() {
    var nav = $('sidebar'); nav.textContent = '';
    groups().forEach(function (g, gi) {
      var wrap = el('div', 'grp');
      var open = !collapsed[g.key] || g.pages.some(function (p) { return p.id === state.page; });
      var lid = 'grp-' + gi;
      var btn = el('button', 'grp-btn'); btn.type = 'button';
      btn.setAttribute('aria-expanded', open ? 'true' : 'false'); btn.setAttribute('aria-controls', lid);
      btn.appendChild(el('span', null, g.label));
      var ic = el('span'); ic.innerHTML = CHEVRON; btn.appendChild(ic);
      var ul = el('ul', 'grp-list'); ul.id = lid; ul.hidden = !open;
      g.pages.forEach(function (p) {
        var li = el('li'); var a = routeLink(el('a', null, p.title), p.id, '');
        a.dataset.id = p.id;
        if (p.id === state.page) a.setAttribute('aria-current', 'page');
        li.appendChild(a); ul.appendChild(li);
      });
      btn.addEventListener('click', function () {
        var now = btn.getAttribute('aria-expanded') === 'true';
        btn.setAttribute('aria-expanded', now ? 'false' : 'true');
        ul.hidden = now; collapsed[g.key] = now;
      });
      wrap.appendChild(btn); wrap.appendChild(ul); nav.appendChild(wrap);
    });
  }
  function markNav() {
    var links = $('sidebar').querySelectorAll('a[data-id]');
    for (var i = 0; i < links.length; i++) {
      var on = links[i].dataset.id === state.page;
      if (on) {
        links[i].setAttribute('aria-current', 'page');
        var ul = links[i].closest('ul'); var b = ul && ul.previousSibling;
        if (ul && ul.hidden) { ul.hidden = false; if (b) b.setAttribute('aria-expanded', 'true'); }
      } else links[i].removeAttribute('aria-current');
    }
  }

  /* ---------- imagens com alternativa neutra (nunca <img> quebrada) ---------- */
  function placeholder(letter) {
    var s = el('span', 'ph'); s.setAttribute('aria-hidden', 'true');
    if (letter) s.textContent = letter; else s.innerHTML = DOC_GLYPH;
    return s;
  }
  function picture(src, cls, alt, opts) {
    opts = opts || {};
    var wrap = el('span', cls);
    function empty() {
      wrap.textContent = ''; wrap.classList.add('is-empty');
      if (opts.ph) wrap.appendChild(placeholder(opts.letter));
      if (opts.onEmpty) opts.onEmpty(wrap);
    }
    var s = safeUrl(src);
    if (!s) { empty(); return wrap; }
    var img = document.createElement('img');
    img.alt = alt || ''; img.decoding = 'async';
    if (opts.lazy) img.loading = 'lazy';
    img.addEventListener('error', empty);
    img.src = s;
    wrap.appendChild(img);
    return wrap;
  }

  /* ---------- Início ---------- */
  function statusPill() { return el('span', 'status', ui('comingSoon')); }
  function action(cls, label, id) {
    if (id === null || id === undefined) {
      var off = el('span', 'btn btn-off'); off.setAttribute('aria-disabled', 'true');
      off.appendChild(el('span', null, label)); off.appendChild(statusPill());
      return off;
    }
    return routeLink(el('a', cls, label), id, '');
  }
  function homeFeatures() {
    var h = homeData();
    var list = Array.isArray(h.features) && h.features.length ? h.features : null;
    if (!list) {
      list = ordered().filter(function (p) { return p.id !== 'inicio'; }).map(function (p) {
        return { page: p.id, title: p.title, text: p.summary || '', image: '' };
      });
    }
    return list.slice(0, 6);
  }
  function renderHome() {
    var root = $('homeMain'); root.textContent = '';
    var h = homeData();
    var brand = ui('brand');

    var band = el('section', 'band'); band.setAttribute('aria-labelledby', 'homeTitle');
    var inn = el('div', 'band-in');
    inn.appendChild(picture(h.image, 'band-art', brand, { ph: true, letter: String(brand).charAt(0) || 'C' }));
    var h1 = el('h1', 'band-title', h.title || brand); h1.id = 'homeTitle'; inn.appendChild(h1);
    var intro = pageById('inicio');
    var tag = h.tagline || (intro && intro.summary) || '';
    if (tag) inn.appendChild(el('p', 'band-tag', tag));
    if (h.support) inn.appendChild(el('p', 'band-sub', h.support));
    var cta = el('div', 'band-cta');
    var st = startTarget(), dt = docsTarget(), mt = modelsTarget();
    cta.appendChild(action('btn btn-primary', homeText('cta', 'start'), st));
    if (dt !== st) cta.appendChild(action('btn btn-ghost', homeText('docs', 'guides'), dt));
    if (mt && mt !== st) cta.appendChild(action('btn btn-ghost', homeText('models', 'models'), mt));
    inn.appendChild(cta);
    band.appendChild(inn); root.appendChild(band);

    var feats = homeFeatures();
    if (feats.length) {
      var sec = el('section', 'feats');
      sec.setAttribute('aria-labelledby', 'featsTitle');
      var h2 = el('h2', 'sr', ui('guides')); h2.id = 'featsTitle'; sec.appendChild(h2);
      var ul = el('ul', 'feats-grid');
      feats.forEach(function (f, fi) {
        var rid = f.page ? resolveId(f.page) : null;
        var target = rid ? pageById(rid) : null;
        var title = f.title || (target && target.title) || '';
        var text = f.text || (target && target.summary) || '';
        if (!title) return;
        var li = el('li', 'feat' + (rid ? '' : ' is-off'));
        /* com destino: um único <a> envolve imagem, título e texto;
           sem destino: um <div> com o mesmo layout */
        var box = rid ? routeLink(el('a', 'feat-in'), rid, '') : el('div', 'feat-in');
        box.appendChild(picture(f.image, 'feat-img', '', { ph: true, lazy: true }));
        var h3 = el('h3', null, title); h3.id = 'feat-t-' + fi;
        box.appendChild(h3);
        if (rid) box.setAttribute('aria-labelledby', h3.id);
        if (text) box.appendChild(el('p', null, text));
        if (!rid) box.appendChild(statusPill());
        li.appendChild(box);
        ul.appendChild(li);
      });
      sec.appendChild(ul); root.appendChild(sec);
    }
  }

  /* ---------- corpo do artigo ---------- */
  function linkItem(l) {
    var li = el('li');
    var target = null;
    if (l.page) target = resolveId(l.page);
    var url = l.url ? safeUrl(l.url) : '';
    if (!target && url.charAt(0) === '#') { target = resolveId(url.slice(1).split('/')[0]); url = ''; }
    if (url === '#') url = '';
    if (target) {
      li.appendChild(routeLink(el('a', null, l.label), target, ''));
    } else if (url) {
      var b = el('a', null, l.label); b.href = url;
      if (/^https?:/i.test(url)) { b.target = '_blank'; b.rel = 'noopener noreferrer'; }
      li.appendChild(b);
    } else {
      li.appendChild(el('span', null, l.label));
      li.appendChild(statusPill());
    }
    return li;
  }
  function linkList(links) {
    var ul = el('ul', 'reflist');
    links.forEach(function (l) { ul.appendChild(linkItem(l)); });
    return ul;
  }

  function copyText(text, btn, label) {
    var done = function () {
      btn.textContent = ui('copied'); $('live').textContent = ui('copied');
      setTimeout(function () { btn.textContent = label; }, 1500);
    };
    function fallback() {
      var t = el('textarea'); t.value = text; t.setAttribute('readonly', ''); t.style.position = 'fixed'; t.style.opacity = '0';
      document.body.appendChild(t); t.select();
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (e2) { ok = false; }
      document.body.removeChild(t);
      if (ok) done();
    }
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(text).then(done, fallback); return; }
    } catch (e) { /* fall through */ }
    fallback();
  }
  function codeBlock(text, language) {
    var lang = String(language || '').trim() || 'text';
    var box = el('div', 'code');
    var head = el('div', 'code-head');
    head.appendChild(el('span', 'code-lang', lang));
    var label = ui('copy');
    var btn = el('button', 'copy', label); btn.type = 'button';
    btn.setAttribute('aria-label', ui('copyCode') + ' (' + lang + ')');
    btn.addEventListener('click', function () { copyText(String(text), btn, label); });
    head.appendChild(btn);
    var pre = el('pre'); pre.tabIndex = 0;
    pre.setAttribute('role', 'region'); pre.setAttribute('aria-label', lang);
    var code = el('code', null, String(text));
    code.className = 'language-' + lang.replace(/[^A-Za-z0-9_+-]/g, '');
    pre.appendChild(code);
    box.appendChild(head); box.appendChild(pre);
    return box;
  }
  function callout(note) {
    var text = typeof note === 'object' && note ? (note.text || '') : String(note);
    var title = typeof note === 'object' && note && note.title ? note.title : ui('note');
    var a = el('aside', 'callout'); a.setAttribute('aria-label', title);
    a.appendChild(el('p', 'note-title', title));
    a.appendChild(el('p', null, text));
    return a;
  }

  function hasModels(p) { return Array.isArray(p.models) && p.models.length > 0; }
  /* a página "Modelos" já tem o título; evita repetir o cabeçalho */
  function modelsHeading(p) { return hasModels(p) && p.title !== ui('models'); }
  function renderModels(root, p) {
    if (!hasModels(p)) return;
    if (modelsHeading(p)) { var h = el('h2', null, ui('models')); h.id = 's-models'; root.appendChild(h); }
    var ul = el('ul', 'models');
    p.models.forEach(function (m) {
      if (!m || !m.name) return;
      var li = el('li', 'model');
      if (m.image) {
        li.appendChild(picture(m.image, 'model-img', '', { lazy: false, onEmpty: function (w) {
          if (w.parentNode) w.parentNode.removeChild(w);
          li.classList.add('no-img');
        } }));
      } else li.classList.add('no-img');
      var body = el('div', 'model-body');
      body.appendChild(el('h3', null, m.name));
      if (m.description) body.appendChild(el('p', null, m.description));
      li.appendChild(body);
      if (Array.isArray(m.links) && m.links.length) li.appendChild(linkList(m.links));
      ul.appendChild(li);
    });
    root.appendChild(ul);
  }

  function renderSections(root, p) {
    (p.sections || []).forEach(function (s, i) {
      var sid = 's-' + i;
      if (s.title) {
        var h = el('h2', null, s.title); h.id = sid;
        var a = routeLink(el('a', 'anchor', '#'), p.id, sid);
        a.setAttribute('aria-label', ui('permalink') + ': ' + s.title);
        h.appendChild(a); root.appendChild(h);
      }
      if (s.text) root.appendChild(el('p', null, s.text));
      if (s.items && s.items.length) {
        var ul = el('ul', 'items');
        s.items.forEach(function (t) { ul.appendChild(el('li', null, t)); });
        root.appendChild(ul);
      }
      if (s.code) root.appendChild(codeBlock(s.code, s.language));
      if (s.note) root.appendChild(callout(s.note));
      if (s.links && s.links.length) root.appendChild(linkList(s.links));
    });
    renderModels(root, p);
    if (p.links && p.links.length) {
      var h2 = el('h2', null, ui('related')); h2.id = 's-links'; root.appendChild(h2);
      root.appendChild(linkList(p.links));
    }
  }

  function breadcrumb(p) {
    var nav = el('nav', 'crumbs'); nav.setAttribute('aria-label', ui('breadcrumb'));
    var ol = el('ol');
    var li0 = el('li');
    var a = routeLink(el('a'), '', ''); a.innerHTML = HOME_ICO;
    a.setAttribute('aria-label', ui('home')); li0.appendChild(a);
    ol.appendChild(li0);
    if (p.group) { var lg = el('li'); lg.appendChild(el('span', null, groupLabel(p.group))); ol.appendChild(lg); }
    var last = el('li'); var cur = el('span', null, p.title); cur.setAttribute('aria-current', 'page'); last.appendChild(cur);
    ol.appendChild(last);
    nav.appendChild(ol);
    return nav;
  }

  function pager(root, p) {
    var all = ordered(); var i = all.indexOf(p);
    var wrap = el('nav', 'pager'); wrap.setAttribute('aria-label', ui('pagination'));
    if (i > 0) {
      var a = routeLink(el('a', 'prev'), all[i - 1].id, ''); a.rel = 'prev';
      a.appendChild(el('small', null, ui('previous'))); a.appendChild(el('b', null, all[i - 1].title)); wrap.appendChild(a);
    }
    if (i >= 0 && i < all.length - 1) {
      var b = routeLink(el('a', 'next'), all[i + 1].id, ''); b.rel = 'next';
      b.appendChild(el('small', null, ui('next'))); b.appendChild(el('b', null, all[i + 1].title)); wrap.appendChild(b);
    }
    if (wrap.childNodes.length) root.appendChild(wrap);
  }

  /* índice de caminhos agrupados (visão geral) */
  function routeIndex(root, current) {
    var gs = groups().map(function (g) {
      return { key: g.key, label: g.label, pages: g.pages.filter(function (x) { return x.id !== current.id; }) };
    }).filter(function (g) { return g.pages.length; });
    if (!gs.length) return;
    var h = el('h2', null, data().ui && data().ui.routeIndex ? data().ui.routeIndex : ui('routeIndex')); h.id = 's-routes';
    root.appendChild(h);
    var box = el('div', 'paths');
    gs.forEach(function (g) {
      box.appendChild(el('h3', null, g.label));
      var ul = el('ul');
      g.pages.forEach(function (x) {
        var li = el('li'); var a = routeLink(el('a'), x.id, '');
        a.appendChild(el('b', null, x.title));
        if (x.summary) a.appendChild(el('span', null, x.summary));
        li.appendChild(a); ul.appendChild(li);
      });
      box.appendChild(ul);
    });
    root.appendChild(box);
  }
  function scopeNote(root) {
    var rel = pageById('lancamento');
    if (!rel) return;
    var note = el('aside', 'note'); note.setAttribute('aria-label', rel.title);
    note.appendChild(el('p', 'note-title', rel.kicker || rel.title));
    if (rel.summary) note.appendChild(el('p', null, rel.summary));
    var a = routeLink(el('a', null, rel.title + ' - ' + ui('details')), rel.id, '');
    var p = el('p'); p.appendChild(a); note.appendChild(p);
    root.appendChild(note);
  }

  function tocEntries(p, isIntro) {
    var out = [];
    (p.sections || []).forEach(function (s, i) { if (s.title) out.push({ id: 's-' + i, title: s.title }); });
    if (modelsHeading(p)) out.push({ id: 's-models', title: ui('models') });
    if (isIntro && groups().length) out.push({ id: 's-routes', title: (data().ui && data().ui.routeIndex) || ui('routeIndex') });
    if (p.links && p.links.length) out.push({ id: 's-links', title: ui('related') });
    return out;
  }

  function renderArticle(view, p) {
    var isIntro = p.id === 'inicio';
    var art = el('article', 'article');
    art.appendChild(breadcrumb(p));
    var entries = tocEntries(p, isIntro);
    /* sumário recolhido para telas estreitas */
    if (entries.length >= 2) {
      var det = el('details', 'toc-inline'); det.appendChild(el('summary', null, ui('onPage')));
      var ul = el('ul');
      entries.forEach(function (e) { var li = el('li'); li.appendChild(routeLink(el('a', null, e.title), p.id, e.id)); ul.appendChild(li); });
      det.appendChild(ul); art.appendChild(det);
    }
    if (p.kicker) art.appendChild(el('p', 'kicker', p.kicker));
    art.appendChild(el('h1', null, p.title));
    if (p.summary) art.appendChild(el('p', 'lede', p.summary));
    if (isIntro) scopeNote(art);
    renderSections(art, p);
    if (isIntro) routeIndex(art, p);
    pager(art, p);
    view.appendChild(art);
    return entries;
  }

  function buildToc(entries) {
    var toc = $('toc'), list = $('tocList'), doc = $('doc'); list.textContent = '';
    if (spy) { spy.disconnect(); spy = null; }
    if (entries.length < 2) { toc.hidden = true; doc.classList.add('no-toc'); return; }
    entries.forEach(function (e) {
      var li = el('li'); var a = routeLink(el('a', null, e.title), state.page, e.id); a.dataset.t = e.id;
      li.appendChild(a); list.appendChild(li);
    });
    toc.hidden = false; doc.classList.remove('no-toc');
    setTocCurrent(entries[0].id);
    if ('IntersectionObserver' in window) {
      spy = new IntersectionObserver(function (es) {
        es.forEach(function (x) { if (x.isIntersecting) setTocCurrent(x.target.id); });
      }, { rootMargin: '-80px 0px -70% 0px' });
      entries.forEach(function (e) { var t = document.getElementById(e.id); if (t) spy.observe(t); });
    }
  }
  function setTocCurrent(id) {
    var links = $('tocList').querySelectorAll('a');
    for (var i = 0; i < links.length; i++) {
      if (links[i].dataset.t === id) links[i].setAttribute('aria-current', 'true');
      else links[i].removeAttribute('aria-current');
    }
  }

  function scrollToAnchor(id, smooth) {
    var t = id && document.getElementById(id);
    if (!t) return false;
    try { t.scrollIntoView(smooth ? { behavior: 'smooth' } : undefined); } catch (e) { t.scrollIntoView(); }
    setTocCurrent(id);
    return true;
  }
  function focusMain() {
    var m = $(state.page === '' ? 'homeMain' : 'main');
    try { m.focus({ preventScroll: true }); } catch (x) { m.focus(); }
  }

  function render(focus) {
    var home = state.page === '';
    if (!home && !pageById(state.page)) { state.page = ''; state.anchor = ''; home = true; }
    document.body.classList.toggle('is-home', home);
    $('homeView').hidden = !home; $('shell').hidden = home;
    markTopNav();
    var title, desc;
    if (home) {
      closeMenu();
      renderHome();
      var h = homeData();
      title = ui('brand') + ' · ' + ui('label');
      desc = h.tagline || ui('label');
      if (focus) { window.scrollTo(0, 0); focusMain(); $('live').textContent = h.title || ui('brand'); }
    } else {
      var view = $('view'); view.textContent = '';
      var p = pageById(state.page);
      var entries = renderArticle(view, p);
      markNav(); buildToc(entries);
      title = p.title + ' · ' + ui('brand');
      desc = p.summary || ui('label');
      if (focus) {
        if (!(state.anchor && scrollToAnchor(state.anchor, false))) window.scrollTo(0, 0);
        focusMain();
        $('live').textContent = p.title;
      } else if (state.anchor) {
        scrollToAnchor(state.anchor, false);
      }
    }
    document.title = title;
    setMeta('meta[name="description"]', 'content', desc);
    setMeta('meta[property="og:title"]', 'content', title);
    setMeta('meta[property="og:description"]', 'content', desc);
  }
  function fullRender(focus) { applyChrome(); buildTopNav(); buildNav(); buildFooter(); render(focus); }

  /* ---------- navegação ---------- */
  function navigate(id, anchor) {
    anchor = anchor || '';
    var rid = resolveRoute(id);
    if (rid === null) { rid = ''; anchor = ''; }
    var samePage = rid === state.page;
    closeMenu(); closeResults(); closeMobileSearch();
    state.page = rid; state.anchor = anchor;
    if (href(rid, anchor) !== currentUrl()) writeUrl(false);
    if (samePage && anchor && scrollToAnchor(anchor, true)) return;
    render(true);
  }

  function syncFromUrl() {
    var l = validLang(queryLang()) || state.lang;
    if (!DOCS[l]) l = state.lang;
    var r = readRoute();
    var rid = r.anchorOnly ? state.page : resolveRoute(r.id);
    var bad = rid === null;
    if (bad) rid = '';
    var anchor = bad ? '' : r.anchor;
    if (l !== state.lang) {
      state.lang = l; state.page = rid; state.anchor = anchor;
      fullRender(true);
    } else if (rid !== state.page) {
      state.page = rid; state.anchor = anchor; buildNav(); render(true);
    } else if (anchor !== state.anchor) {
      state.anchor = anchor; if (anchor) scrollToAnchor(anchor, false);
    }
    if (bad) writeUrl(true);
  }
  window.addEventListener('popstate', syncFromUrl);
  window.addEventListener('hashchange', syncFromUrl);
  try { if ('scrollRestoration' in window.history) window.history.scrollRestoration = 'manual'; } catch (e) { /* ignore */ }

  /* Um único tratador de cliques para toda rota interna. Cliques com
     Ctrl/Meta/Shift/Alt ou botão do meio seguem o comportamento nativo
     (nova aba/janela), e o href já carrega o idioma. */
  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest ? e.target.closest('a[data-route]') : null;
    if (!a) return;
    if (e.defaultPrevented || e.button > 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    var tgt = a.getAttribute('target');
    if (tgt && tgt !== '_self') return;
    e.preventDefault();
    navigate(a.getAttribute('data-route'), a.getAttribute('data-anchor') || '');
  });
  /* "Ir para o conteúdo" não pode mudar o hash (viraria rota inválida) */
  $('skip').addEventListener('click', function (e) { e.preventDefault(); focusMain(); });

  /* ---------- tema e idioma ---------- */
  $('themeBtn').addEventListener('click', function () {
    state.theme = state.theme === 'dark' ? 'light' : 'dark';
    store('celia-docs-theme', state.theme); applyChrome();
  });
  $('lang').addEventListener('change', function (e) {
    var l = validLang(e.target.value); if (!l) return;
    state.lang = l; store('celia-docs-lang', l);
    if (state.page !== '' && !pageById(state.page)) { state.page = ''; state.anchor = ''; }
    writeUrl(false); fullRender(false);
    $('live').textContent = ui('language') + ': ' + e.target.options[e.target.selectedIndex].text;
  });

  /* ---------- menu móvel (só na documentação) ---------- */
  var menuBtn = $('menuBtn'), side = $('sidebar'), scrim = $('scrim');
  function openMenu() {
    side.classList.add('open'); scrim.hidden = false; menuBtn.setAttribute('aria-expanded', 'true');
    var a = side.querySelector('a[aria-current="page"]') || side.querySelector('a'); if (a) a.focus();
  }
  function closeMenu() {
    if (!side.classList.contains('open')) return;
    side.classList.remove('open'); scrim.hidden = true; menuBtn.setAttribute('aria-expanded', 'false');
  }
  menuBtn.addEventListener('click', function () { side.classList.contains('open') ? closeMenu() : openMenu(); });
  scrim.addEventListener('click', function () { closeMenu(); menuBtn.focus(); });

  /* ---------- busca ---------- */
  var topbar = $('top'), searchBtn = $('searchBtn');
  function closeMobileSearch() {
    topbar.classList.remove('search-open'); searchBtn.setAttribute('aria-expanded', 'false');
  }
  searchBtn.addEventListener('click', function () {
    var open = !topbar.classList.contains('search-open');
    topbar.classList.toggle('search-open', open);
    searchBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) $('q').focus();
  });
  function mobileSearch() { return window.getComputedStyle(searchBtn).display !== 'none'; }
  window.addEventListener('resize', function () {
    if (window.innerWidth > 960) { closeMenu(); closeMobileSearch(); }
  });

  function norm(s) { return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, ''); }
  function buildIndex() {
    if (index[state.lang]) return index[state.lang];
    var out = [];
    pages().forEach(function (p) {
      var head = [p.summary, p.kicker].filter(Boolean);
      out.push({ p: p, sec: '', title: norm(p.title), body: norm(head.join(' ')), raw: [p.summary].filter(Boolean), label: p.title, ctx: groupLabel(p.group) });
      var pageEntry = out[out.length - 1];
      (p.sections || []).forEach(function (s, i) {
        var note = s.note ? (typeof s.note === 'object' ? (s.note.text || '') : String(s.note)) : '';
        var parts = [s.text || '', (s.items || []).join(' '), s.code || '', note, (s.links || []).map(function (l) { return l.label; }).join(' ')];
        var raw = [s.text || ''].concat(s.items || []).concat(note).concat(parts[4]).filter(Boolean);
        if (!s.title && !raw.length) return;
        out.push({ p: p, sec: 's-' + i, title: norm(s.title || ''), body: norm(parts.join(' ')), raw: raw.concat(s.code ? [s.code] : []), label: s.title || p.title, ctx: p.title });
      });
      if (hasModels(p)) {
        var mtxt = p.models.map(function (m) {
          return [m.name, m.description, (m.links || []).map(function (l) { return l.label; }).join(' ')].filter(Boolean).join(' ');
        });
        if (modelsHeading(p)) {
          out.push({ p: p, sec: 's-models', title: norm(ui('models')), body: norm(mtxt.join(' ')), raw: mtxt, label: ui('models'), ctx: p.title });
        } else {
          pageEntry.body += ' ' + norm(mtxt.join(' ')); pageEntry.raw = pageEntry.raw.concat(mtxt);
        }
      }
      (p.links || []).forEach(function (l) { out[out.length - 1].body += ' ' + norm(l.label); });
    });
    index[state.lang] = out; return out;
  }
  function snippet(raw, terms) {
    for (var i = 0; i < raw.length; i++) {
      var n = norm(raw[i]);
      for (var j = 0; j < terms.length; j++) {
        var at = n.indexOf(terms[j]);
        if (at >= 0) {
          var s = Math.max(0, at - 40);
          var txt = raw[i].slice(s, s + 130).replace(/\s+/g, ' ');
          return (s > 0 ? '…' : '') + txt + (s + 130 < raw[i].length ? '…' : '');
        }
      }
    }
    return raw[0] ? raw[0].replace(/\s+/g, ' ').slice(0, 110) : '';
  }
  function search(qs) {
    var terms = norm(qs).split(/\s+/).filter(Boolean);
    if (!terms.length) return [];
    var res = [];
    buildIndex().forEach(function (e, order) {
      var score = 0, ok = true;
      terms.forEach(function (t) {
        var inT = e.title.indexOf(t) >= 0, inB = e.body.indexOf(t) >= 0;
        if (!inT && !inB) ok = false; else score += inT ? 5 : 1;
      });
      if (ok) res.push({ e: e, score: score + (e.sec ? 0 : 1), order: order });
    });
    res.sort(function (a, b) { return b.score - a.score || a.order - b.order; });
    return res.slice(0, 12).map(function (r) { return { e: r.e, snip: snippet(r.e.raw, terms) }; });
  }

  var q = $('q'), box = $('results'), sbox = $('searchBox'), active = -1, current = [];
  function closeResults() { box.hidden = true; q.setAttribute('aria-expanded', 'false'); q.removeAttribute('aria-activedescendant'); active = -1; }
  function setActive(n) {
    var items = box.querySelectorAll('.res'); if (!items.length) return;
    active = (n + items.length) % items.length;
    for (var i = 0; i < items.length; i++) items[i].setAttribute('aria-selected', i === active ? 'true' : 'false');
    q.setAttribute('aria-activedescendant', items[active].id);
    items[active].scrollIntoView({ block: 'nearest' });
  }
  function mark(text, terms) {
    var frag = document.createDocumentFragment();
    var n = norm(text), pos = 0;
    if (n.length !== text.length) { frag.appendChild(document.createTextNode(text)); return frag; }
    var hits = [];
    terms.forEach(function (t) { var at = n.indexOf(t); if (at >= 0) hits.push([at, at + t.length]); });
    hits.sort(function (a, b) { return a[0] - b[0]; });
    hits.forEach(function (h) {
      if (h[0] < pos) return;
      frag.appendChild(document.createTextNode(text.slice(pos, h[0])));
      var m = document.createElement('mark'); m.textContent = text.slice(h[0], h[1]); frag.appendChild(m);
      pos = h[1];
    });
    frag.appendChild(document.createTextNode(text.slice(pos)));
    return frag;
  }
  function pick(r) { q.value = ''; navigate(r.e.p.id, r.e.sec); }
  function showResults() {
    var val = q.value.trim();
    box.textContent = ''; active = -1;
    if (!val) { closeResults(); return; }
    var terms = norm(val).split(/\s+/).filter(Boolean);
    current = search(val);
    if (!current.length) {
      box.appendChild(el('div', 'res-empty', ui('noResults') + ': ' + val));
    } else {
      current.forEach(function (r, i) {
        var a = routeLink(el('a', 'res'), r.e.p.id, r.e.sec); a.id = 'res-' + i;
        a.setAttribute('role', 'option'); a.setAttribute('aria-selected', 'false');
        if (r.e.ctx) a.appendChild(el('span', 'ctx', r.e.ctx));
        var b = el('b'); b.appendChild(mark(r.e.label, terms)); a.appendChild(b);
        if (r.snip) { var s = el('small'); s.appendChild(mark(r.snip, terms)); a.appendChild(s); }
        box.appendChild(a);
      });
    }
    box.hidden = false; q.setAttribute('aria-expanded', 'true');
    $('live').textContent = current.length ? current.length + ' – ' + ui('search') : ui('noResults');
  }
  /* o clique no resultado passa pelo tratador global; aqui só limpa o campo.
     mousedown não tira o foco do campo (também vale ao arrastar a rolagem). */
  box.addEventListener('mousedown', function (e) { e.preventDefault(); });
  box.addEventListener('click', function (e) {
    if (e.target.closest && e.target.closest('a.res') && !(e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button > 0)) q.value = '';
  });
  box.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { e.preventDefault(); closeResults(); q.focus(); }
  });
  q.addEventListener('input', function () { showResults(); if (q.value.trim()) q.removeAttribute('aria-invalid'); });
  q.addEventListener('focus', function () { if (q.value.trim()) showResults(); });
  /* fecha só quando o foco sai de toda a caixa de busca (resultados focáveis ficam abertos) */
  sbox.addEventListener('focusout', function (e) {
    if (e.relatedTarget && sbox.contains(e.relatedTarget)) return;
    setTimeout(function () { if (!sbox.contains(document.activeElement)) closeResults(); }, 0);
  });
  q.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); if (box.hidden) showResults(); setActive(active + 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(active - 1); }
    else if (e.key === 'Enter') {
      e.preventDefault();
      if (!q.value.trim()) { q.setAttribute('aria-invalid', 'true'); $('live').textContent = ui('searchPlaceholder'); return; }
      if (box.hidden) showResults();
      var r = current[active >= 0 ? active : 0];
      if (r) { q.blur(); pick(r); }
    } else if (e.key === 'Escape') {
      if (!box.hidden) closeResults();
      else if (q.value) q.value = '';
      else if (mobileSearch()) { closeMobileSearch(); searchBtn.focus(); }
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && side.classList.contains('open')) { closeMenu(); menuBtn.focus(); }
    var tag = (document.activeElement || {}).tagName || '';
    if (e.key === '/' && !/^(INPUT|SELECT|TEXTAREA)$/.test(tag) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      if (mobileSearch()) { topbar.classList.add('search-open'); searchBtn.setAttribute('aria-expanded', 'true'); }
      q.focus();
    }
  });
  document.addEventListener('click', function (e) { if (!sbox.contains(e.target)) closeResults(); });

  /* ---------- init ---------- */
  (function init() {
    var st = store('celia-docs-theme');
    if (st === 'light' || st === 'dark') state.theme = st;
    else state.theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    var l = validLang(queryLang()) || validLang(store('celia-docs-lang')) || 'pt-BR';
    if (!DOCS[l]) l = DOCS['pt-BR'] ? 'pt-BR' : (Object.keys(DOCS)[0] || l);
    state.lang = l;
    var r = readRoute();
    var rid = r.anchorOnly ? '' : resolveRoute(r.id);
    state.page = rid === null ? '' : rid;
    state.anchor = rid ? r.anchor : '';
    fullRender(false);
    /* alias antigo, rota inválida ou idioma ausente -> URL canônica sem criar histórico */
    var canonical = href(state.page, state.anchor);
    if (canonical !== currentUrl()) writeUrl(true);
  })();

})();
