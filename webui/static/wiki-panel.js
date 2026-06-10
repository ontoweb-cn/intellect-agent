/** Wiki / Vault browser panel — scoped catalog + iframe viewer. */

let _wikiCatalog = null;
let _wikiSelected = null;
let _wikiBuildPollTimer = null;

function _wikiT(key, fallback) {
  return typeof t === 'function' ? t(key) : fallback;
}

function _wikiEsc(s) {
  return typeof esc === 'function' ? esc(s) : String(s || '');
}

function _wikiVaultAbsUrl(relativeUrl) {
  const rel = String(relativeUrl || '').startsWith('/') ? String(relativeUrl).slice(1) : String(relativeUrl || '');
  return new URL(rel, document.baseURI || location.href).href;
}

function _wikiScopeKey(entry) {
  if (!entry) return '';
  return String(entry.scope || '') + ':' + String(entry.scope_id || '');
}

function _wikiBadgeLabel(badge) {
  const map = {
    ready: _wikiT('wiki_badge_ready', 'Ready'),
    building: _wikiT('wiki_badge_building', 'Building'),
    empty: _wikiT('wiki_badge_empty', 'Empty'),
    missing: _wikiT('wiki_badge_missing', 'Missing'),
    failed: _wikiT('wiki_badge_failed', 'Failed'),
    no_vault: _wikiT('wiki_badge_no_vault', 'No vault'),
  };
  return map[badge] || badge || '';
}

function _wikiStopBuildPoll() {
  if (_wikiBuildPollTimer) {
    clearInterval(_wikiBuildPollTimer);
    _wikiBuildPollTimer = null;
  }
}

function _wikiFindEntry(scope, scopeId) {
  if (!_wikiCatalog) return null;
  const key = String(scope || '') + ':' + String(scopeId || '');
  if (_wikiScopeKey(_wikiCatalog.personal) === key) return _wikiCatalog.personal;
  const teams = Array.isArray(_wikiCatalog.teams) ? _wikiCatalog.teams : [];
  const projects = Array.isArray(_wikiCatalog.projects) ? _wikiCatalog.projects : [];
  const global = _wikiCatalog.global;
  if (global && _wikiScopeKey(global) === key) return global;
  return teams.find(e => _wikiScopeKey(e) === key)
    || projects.find(e => _wikiScopeKey(e) === key)
    || null;
}

function _wikiViewerState(entry) {
  if (!entry) return 'loading';
  const wikiStatus = entry.wiki && entry.wiki.status;
  const built = entry.vault && entry.vault.built;
  const buildStatus = entry.vault && entry.vault.build && entry.vault.build.status;
  if (buildStatus === 'queued' || buildStatus === 'running') return 'building';
  if (wikiStatus === 'missing') return 'missing_wiki';
  if (buildStatus === 'failed') return 'failed';
  if (built) return 'ready';
  if (wikiStatus === 'empty' || wikiStatus === 'ready') return 'no_vault';
  return 'no_vault';
}

function _wikiRenderCatalogRow(entry, group) {
  const active = _wikiSelected && _wikiScopeKey(_wikiSelected) === _wikiScopeKey(entry);
  const badge = entry.badge || '';
  const slug = entry.slug || entry.scope_id || '';
  const sub = slug && slug !== entry.label ? slug : '';
  return '<button type="button" class="wiki-catalog-item side-menu-item' + (active ? ' active' : '') + '"'
    + ' role="treeitem" aria-selected="' + (active ? 'true' : 'false') + '"'
    + ' data-wiki-scope="' + _wikiEsc(entry.scope) + '"'
    + ' data-wiki-scope-id="' + _wikiEsc(entry.scope_id || '') + '">'
    + '<span class="wiki-catalog-item-main">'
    + '<span class="wiki-catalog-item-label">' + _wikiEsc(entry.label || slug) + '</span>'
    + (sub ? '<span class="wiki-catalog-item-sub">' + _wikiEsc(sub) + '</span>' : '')
    + '</span>'
    + '<span class="wiki-catalog-badge wiki-catalog-badge--' + _wikiEsc(badge) + '">' + _wikiEsc(_wikiBadgeLabel(badge)) + '</span>'
    + '</button>';
}

function renderWikiCatalog() {
  const el = document.getElementById('wikiCatalog');
  if (!el || !_wikiCatalog) return;
  const personal = _wikiCatalog.personal;
  const teams = Array.isArray(_wikiCatalog.teams) ? _wikiCatalog.teams : [];
  const projects = Array.isArray(_wikiCatalog.projects) ? _wikiCatalog.projects : [];
  let html = '<div class="wiki-catalog" role="tree">';
  html += '<div class="side-menu-group-title">' + _wikiEsc(_wikiT('wiki_catalog_personal', 'Personal')) + '</div>';
  if (personal) html += _wikiRenderCatalogRow(personal, 'personal');
  if (teams.length) {
    html += '<div class="side-menu-group-title">' + _wikiEsc(_wikiT('wiki_catalog_teams', 'Teams')) + '</div>';
    html += teams.map(row => _wikiRenderCatalogRow(row, 'team')).join('');
  }
  if (projects.length) {
    html += '<div class="side-menu-group-title">' + _wikiEsc(_wikiT('wiki_catalog_projects', 'Projects')) + '</div>';
    html += projects.map(row => _wikiRenderCatalogRow(row, 'project')).join('');
  }
  const global = _wikiCatalog.global;
  if (global && global.visible !== false) {
    html += '<div class="side-menu-group-title">' + _wikiEsc(_wikiT('wiki_catalog_global', 'Organization')) + '</div>';
    html += _wikiRenderCatalogRow(global, 'global');
    if (global.pending_contributions > 0) {
      html += '<div class="wiki-catalog-hint">' + _wikiEsc(
        _wikiT('wiki_pending_contributions', 'Pending reviews') + ': ' + global.pending_contributions
      ) + '</div>';
    }
  }
  html += '</div>';
  el.innerHTML = html;
}

const _WIKI_VIEWER_PANEL_IDS = {
  loading: 'wikiViewerLoading',
  missing_wiki: 'wikiViewerMissingWiki',
  no_vault: 'wikiViewerNoVault',
  building: 'wikiViewerBuilding',
  ready: 'wikiViewerReady',
  failed: 'wikiViewerFailed',
  empty: 'wikiViewerEmpty',
};

function _wikiSetViewerVisible(state) {
  Object.keys(_WIKI_VIEWER_PANEL_IDS).forEach(s => {
    const node = document.getElementById(_WIKI_VIEWER_PANEL_IDS[s]);
    if (node) node.hidden = s !== state;
  });
}

function _wikiRenderViewer(entry) {
  const titleEl = document.getElementById('wikiViewerTitle');
  const state = _wikiViewerState(entry);
  if (titleEl) titleEl.textContent = entry ? (entry.label || '') : '';

  const rebuildBtn = document.getElementById('wikiRebuildBtn');
  const openBtn = document.getElementById('wikiOpenTabBtn');
  const initBtn = document.getElementById('wikiInitBtn');
  if (rebuildBtn) rebuildBtn.hidden = !entry || state === 'missing_wiki' || state === 'building';
  if (openBtn) openBtn.hidden = state !== 'ready';
  const canWrite = entry && entry.can_write !== false;
  if (initBtn) initBtn.hidden = !entry || !canWrite || !(entry.wiki && entry.wiki.init_available);

  if (!entry) {
    _wikiSetViewerVisible('empty');
    return;
  }

  if (state === 'ready') {
    _wikiSetViewerVisible('ready');
    const frame = document.getElementById('wikiVaultFrame');
    const url = entry.vault && entry.vault.url;
    if (frame && url) {
      const nextSrc = _wikiVaultAbsUrl(url);
      if (frame.getAttribute('src') !== nextSrc) frame.src = nextSrc;
    }
    _wikiStopBuildPoll();
    return;
  }

  if (state === 'building') {
    _wikiSetViewerVisible('building');
    _wikiStartBuildPoll(entry);
    return;
  }

  _wikiStopBuildPoll();

  if (state === 'missing_wiki') {
    _wikiSetViewerVisible('missing_wiki');
    return;
  }
  if (state === 'failed') {
    _wikiSetViewerVisible('failed');
    const errEl = document.getElementById('wikiFailedError');
    const err = entry.vault && entry.vault.build && entry.vault.build.error;
    if (errEl) errEl.textContent = err || _wikiT('wiki_failed_unknown', 'Build failed.');
    return;
  }
  if (state === 'no_vault') {
    _wikiSetViewerVisible('no_vault');
    return;
  }
  _wikiSetViewerVisible('empty');
}

async function _wikiRefreshBuildProgress(entry) {
  if (!entry) return;
  const scope = entry.scope || 'global';
  const scopeId = entry.scope_id || '';
  try {
    const bs = await api('/api/wiki/build/status?scope=' + encodeURIComponent(scope)
      + '&scope_id=' + encodeURIComponent(scopeId));
    const job = bs && bs.current_job;
    const bar = document.getElementById('wikiBuildProgressBar');
    const sub = document.getElementById('wikiBuildProgressSub');
    const pct = job && typeof job.progress_pct === 'number' ? job.progress_pct : 15;
    if (bar) {
      bar.style.width = pct + '%';
      bar.setAttribute('aria-valuenow', String(pct));
    }
    if (sub && job) {
      const status = job.status || 'running';
      const started = job.started_at ? ' (' + status + ')' : '';
      sub.textContent = _wikiT('wiki_building_sub', 'Building vault…') + started
        + ' — ' + _wikiT('wiki_building_eta_note', 'Estimated progress');
    }
    if (job && job.status === 'done' && bs.vault_built) {
      entry.vault = entry.vault || {};
      entry.vault.built = true;
      entry.badge = 'ready';
      entry.vault.build = { status: 'idle', job_id: null };
      renderWikiCatalog();
      _wikiRenderViewer(entry);
      return;
    }
    if (job && job.status === 'failed') {
      entry.badge = 'failed';
      entry.vault = entry.vault || {};
      entry.vault.build = { status: 'failed', job_id: job.job_id, error: job.error };
      renderWikiCatalog();
      _wikiRenderViewer(entry);
    }
  } catch (_) {}
}

function _wikiStartBuildPoll(entry) {
  _wikiStopBuildPoll();
  void _wikiRefreshBuildProgress(entry);
  _wikiBuildPollTimer = setInterval(function() {
    void _wikiRefreshBuildProgress(entry);
  }, 2000);
}

function selectWikiScope(scope, scopeId) {
  const entry = _wikiFindEntry(scope, scopeId);
  if (!entry) return;
  _wikiSelected = entry;
  renderWikiCatalog();
  _wikiRenderViewer(entry);
}

async function loadWikiPanel(force) {
  _wikiBindCatalogClicks();
  const catalogEl = document.getElementById('wikiCatalog');
  if (!catalogEl) return;
  if (!force && _wikiCatalog) {
    renderWikiCatalog();
    if (_wikiSelected) _wikiRenderViewer(_wikiSelected);
    return;
  }
  catalogEl.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px">' + _wikiEsc(_wikiT('loading', 'Loading…')) + '</div>';
  _wikiSetViewerVisible('loading');
  try {
    const data = await api('/api/wiki/catalog');
    _wikiCatalog = data;
    if (!_wikiSelected && data && data.personal) {
      _wikiSelected = data.personal;
    }
    renderWikiCatalog();
    _wikiRenderViewer(_wikiSelected);
  } catch (e) {
    catalogEl.innerHTML = '<p class="members-error" style="padding:12px">' + _wikiEsc(e.message || String(e)) + '</p>';
    _wikiSetViewerVisible('empty');
  }
}

function _wikiScopeQuery(scope, scopeId) {
  if (scope === 'member' && scopeId) return '?member_id=' + encodeURIComponent(scopeId);
  if (scope === 'team' && scopeId) return '?team_id=' + encodeURIComponent(scopeId);
  if (scope === 'project' && scopeId) return '?project_id=' + encodeURIComponent(scopeId);
  return '';
}

async function initWikiForScope(scope, scopeId) {
  const entry = _wikiFindEntry(scope, scopeId) || _wikiSelected;
  const pathHint = entry && entry.wiki && entry.wiki.target_path_hint;
  const confirmTpl = _wikiT('wiki_init_confirm_body', 'Create wiki at {path}?');
  if (!window.confirm(confirmTpl.replace('{path}', pathHint || 'this location'))) return;
  let domain = '';
  if (typeof window.prompt === 'function') {
    const prompted = window.prompt(_wikiT('wiki_init_domain_prompt', 'Domain (optional):'), '');
    if (prompted === null) return;
    domain = String(prompted).trim();
  }
  try {
    const body = { trigger_build: true };
    if (scope) body.scope = scope;
    if (domain) body.domain = domain;
    const qs = _wikiScopeQuery(scope, scopeId);
    const r = await api('/api/wiki/init' + qs, { method: 'POST', body: JSON.stringify(body) });
    if (r && r.ok) {
      if (typeof showToast === 'function') showToast(_wikiT('wiki_init_success', 'Wiki initialized.'), 2500);
      await loadWikiPanel(true);
    } else {
      if (typeof showToast === 'function') showToast(_wikiT('wiki_init_failed', 'Failed to initialize wiki.') + (r && r.error ? ': ' + r.error : ''), 'error');
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast(_wikiT('wiki_init_failed', 'Failed to initialize wiki.') + ': ' + (e.message || String(e)), 'error');
  }
}

async function buildWikiVaultForSelection() {
  const entry = _wikiSelected;
  if (!entry) return;
  try {
    const body = { scope: entry.scope, scope_id: entry.scope_id || null };
    const r = await api('/api/wiki/build', { method: 'POST', body: JSON.stringify(body) });
    if (r && (r.ok || r.queued)) {
      if (typeof showToast === 'function') showToast(_wikiT('wiki_build_queued', 'Vault build queued.'), 2500);
      entry.badge = 'building';
      entry.vault = entry.vault || {};
      entry.vault.build = { status: r.status || 'queued', job_id: r.job_id };
      renderWikiCatalog();
      _wikiRenderViewer(entry);
    } else {
      if (typeof showToast === 'function') showToast((r && r.error) || _wikiT('wiki_build_failed', 'Failed to trigger vault build.'), 'error');
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast(_wikiT('wiki_build_failed', 'Failed to trigger vault build.') + ': ' + (e.message || String(e)), 'error');
  }
}

function openWikiVaultInTab() {
  const entry = _wikiSelected;
  if (!entry || !entry.vault || !entry.vault.url) return;
  window.open(_wikiVaultAbsUrl(entry.vault.url), '_blank', 'noopener');
}

function openWikiLogsPanel() {
  if (typeof switchPanel === 'function') switchPanel('logs');
}

function wikiInitCurrentScope() {
  if (!_wikiSelected) return;
  void initWikiForScope(_wikiSelected.scope, _wikiSelected.scope_id);
}

function _wikiBindCatalogClicks() {
  const el = document.getElementById('wikiCatalog');
  if (!el || el.dataset.wikiBound === '1') return;
  el.dataset.wikiBound = '1';
  el.addEventListener('click', function(ev) {
    const btn = ev.target && ev.target.closest ? ev.target.closest('[data-wiki-scope]') : null;
    if (!btn) return;
    selectWikiScope(btn.dataset.wikiScope || '', btn.dataset.wikiScopeId || '');
  });
}

if (typeof window !== 'undefined') {
  window.loadWikiPanel = loadWikiPanel;
  window.renderWikiCatalog = renderWikiCatalog;
  window.selectWikiScope = selectWikiScope;
  window.initWikiForScope = initWikiForScope;
  window.buildWikiVaultForSelection = buildWikiVaultForSelection;
  window.openWikiVaultInTab = openWikiVaultInTab;
  window.openWikiLogsPanel = openWikiLogsPanel;
  window.wikiInitCurrentScope = wikiInitCurrentScope;
  window._wikiStopBuildPoll = _wikiStopBuildPoll;
}

_wikiBindCatalogClicks();
