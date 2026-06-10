/** Settings → OAuth Providers panel — manage login, model, and server OAuth providers. */

var _memberOAuthProvidersCache = null;
var _oauthYamlMigration = null;
var _oauthPanelActiveTab = 'login';

var OAUTH_FLOW_FIELDS = {
  pkce_loopback:    ['client_id', 'client_secret', 'authorize_url', 'token_url', 'userinfo_url', 'scopes', 'logo'],
  oidc_discovery:   ['client_id', 'client_secret', 'oidc_discovery_url', 'scopes', 'logo'],
  device_code:      ['client_id', 'client_secret', 'device_code_url', 'token_url', 'scopes', 'logo'],
  trusted_header:   ['tenant_config', 'logo'],
  oauth2_wecom:     ['client_id', 'client_secret', 'scopes', 'logo'],
  oauth2_dingtalk:  ['client_id', 'client_secret', 'scopes', 'logo'],
  oauth2_feishu:    ['client_id', 'client_secret', 'scopes', 'logo']
};

function _fieldDefKey(fieldDef) {
  if (!fieldDef) return '';
  return typeof fieldDef === 'string' ? fieldDef : String(fieldDef.key || '');
}

/** Prefer API ``credential_fields``; fall back to flow-specific legacy field names. */
function _editFieldsForProvider(p) {
  if (p && Array.isArray(p.credential_fields) && p.credential_fields.length) {
    var out = p.credential_fields.slice();
    var hasLogo = false;
    for (var i = 0; i < out.length; i++) {
      if (_fieldDefKey(out[i]) === 'logo') hasLogo = true;
    }
    if (!hasLogo) {
      out.push({ key: 'logo', label: (t('oauth_field_logo') || 'Logo'), legacy: true });
    }
    return out;
  }
  var flow = (p && p.auth_flow) || 'pkce_loopback';
  return _editFieldsForFlow(flow);
}

function _editFieldsForFlow(flow) {
  var legacy = OAUTH_FLOW_FIELDS[flow] || OAUTH_FLOW_FIELDS.pkce_loopback;
  var rows = [];
  for (var i = 0; i < legacy.length; i++) {
    rows.push({ key: legacy[i], legacy: true });
  }
  return rows;
}

function _flowLabel(flow) {
  var map = {
    pkce_loopback:  t('oauth_flow_pkce_loopback')  || 'PKCE Loopback',
    oidc_discovery: t('oauth_flow_oidc_discovery') || 'OIDC Discovery',
    device_code:    t('oauth_flow_device_code')    || 'Device Code',
    trusted_header: t('oauth_flow_trusted_header') || 'Trusted Header',
    oauth2_wecom:   t('oauth_flow_wecom') || 'WeCom',
    oauth2_dingtalk: t('oauth_flow_dingtalk') || 'DingTalk',
    oauth2_feishu:  t('oauth_flow_feishu') || 'Feishu'
  };
  return map[flow] || flow;
}

// Built-in SVG logos for common providers (fallback when DB logo_svg is empty)
// Built-in SVG logos matching login/register page ICON_ART
var BUILTIN_LOGOS = {
  github: {
    bg: '#24292f',
    svg: '<svg viewBox="0 0 24 24"><path fill="#fff" d="M12 2C6.477 2 2 6.484 2 12.021c0 4.428 2.865 8.178 6.839 9.504.5.092.682-.217.682-.482 0-.237-.009-.866-.013-1.7-2.782.605-3.369-1.341-3.369-1.341-.454-1.156-1.11-1.464-1.11-1.464-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0 1 12 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .267.18.578.688.48A10.001 10.001 0 0 0 22 12.021C22 6.484 17.523 2 12 2Z"/></svg>'
  },
  google: {
    bg: '#fff',
    svg: '<svg viewBox="0 0 24 24"><path fill="#4285F4" d="M11.6 9.8v2.4h6.7c-.3 1.5-1.8 4.4-6.7 4.4-4 0-7.3-3.3-7.3-7.3S7.6 2 11.6 2c2.3 0 3.8 1 4.7 1.8l3.2-3.1C16.9.7 14.5 0 11.6 0 5.2 0 0 5.2 0 11.6S5.2 23.2 11.6 23.2c6.7 0 11.1-4.7 11.1-11.4 0-.8-.1-1.4-.2-2H11.6Z"/></svg>'
  },
  gitee: {
    bg: '#c71d23',
    svg: '<svg viewBox="0 0 24 24"><path fill="#fff" d="M11.984 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.016 0zm6.09 5.333c.328 0 .593.266.592.593v1.482a.594.594 0 0 1-.593.592H9.777c-.982 0-1.778.796-1.778 1.778v5.63c0 .327.266.592.593.592h5.63c.982 0 1.778-.796 1.778-1.778v-.296a.593.593 0 0 0-.592-.593h-4.15a.592.592 0 0 1-.592-.592v-1.482a.593.593 0 0 1 .593-.592h6.815c.327 0 .593.265.593.592v3.408a4 4 0 0 1-4 4H5.926a.593.593 0 0 1-.593-.593V9.778a4.444 4.444 0 0 1 4.445-4.444h8.296Z"/></svg>'
  },
  azure_ad: {
    bg: '#0078d4',
    svg: '<svg viewBox="0 0 24 24"><path fill="#fff" d="M11.4 3 3 18.6h6.1L11.4 3Zm1.2 0 8.4 15.6h-6.1L12.6 3ZM7.8 16.8h8.4L12 8.8 7.8 16.8Z"/></svg>'
  },
  ontoweb: {
    bg: '#5b4b8a',
    svg: '<img src="static/logo.png" width="20" height="20" style="border-radius:4px" alt="ONTOWEB">'
  },
  openai_codex: {
    bg: '#10a37f',
    svg: '<svg viewBox="0 0 24 24"><path fill="#fff" d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>'
  },
  xai: {
    bg: '#000',
    svg: '<svg viewBox="0 0 24 24"><text x="12" y="19" text-anchor="middle" font-size="20" font-weight="bold" fill="white">x</text></svg>'
  },
  gemini: {
    bg: '#1a1a2e',
    svg: '<svg viewBox="0 0 24 24"><path fill="#8ab4f8" d="M12 2l2.4 7.4h7.8l-6.3 4.6 2.4 7.4-6.3-4.6-6.3 4.6 2.4-7.4-6.3-4.6h7.8z"/></svg>'
  },
  qwen: {
    bg: '#615ced',
    svg: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="none" stroke="white" stroke-width="1.5"/><text x="12" y="18" text-anchor="middle" font-size="14" font-weight="bold" fill="white">Q</text></svg>'
  }
};

function _getProviderLogo(p) {
  // Use DB logo_svg first, then builtin (bg + svg), then fallback to initial letter
  if (p.logo_svg) return '<div class="oauth-provider-logo">' + p.logo_svg + '</div>';
  var builtin = BUILTIN_LOGOS[p.id];
  if (builtin) {
    return '<div class="oauth-provider-logo" style="background:' + builtin.bg + '">' + builtin.svg + '</div>';
  }
  var initial = (p.name || p.id || '?').charAt(0).toUpperCase();
  return '<div class="oauth-provider-logo oauth-provider-logo-fallback">' + esc(initial) + '</div>';
}

function _catalogRequiredFieldsMet(p) {
  var defs = p.credential_fields;
  if (!Array.isArray(defs) || !defs.length) return null;
  for (var i = 0; i < defs.length; i++) {
    var f = defs[i];
    if (!f || !f.required) continue;
    if (f.secret) {
      if (!p.has_client_secret) return false;
      continue;
    }
    if (f.scope === 'tenant') {
      var tc = p.tenant_config || {};
      if (!tc[f.key]) return false;
      continue;
    }
    if (f.db_column === 'client_id' || f.key === 'client_id') {
      if (!p.has_client_id) return false;
    }
  }
  return true;
}

function _getConfigStatus(p) {
  if (p.has_token) {
    return { ok: true, label: (t('oauth_status_authenticated') || 'Authenticated'), cls: 'oauth-connected' };
  }
  var catalogOk = _catalogRequiredFieldsMet(p);
  if (catalogOk === true) {
    return { ok: true, label: (t('oauth_status_configured') || 'Configured'), cls: 'oauth-connected' };
  }
  if (catalogOk === false) {
    return { ok: false, label: (t('oauth_status_not_configured') || 'Not configured'), cls: 'oauth-disconnected' };
  }
  // Check if the provider has minimum required configuration
  var flow = p.auth_flow || 'pkce_loopback';
  var hasClientId = !!(p.has_client_id);
  if (flow === 'trusted_header') {
    return { ok: true, label: (t('oauth_status_configured') || 'Configured'), cls: 'oauth-connected' };
  }
  if (flow === 'pkce_loopback') {
    var hasEndpoints = !!(p.authorize_url && p.token_url);
    if (hasClientId && hasEndpoints) return { ok: true, label: (t('oauth_status_configured') || 'Configured'), cls: 'oauth-connected' };
    if (hasClientId) return { ok: false, label: (t('oauth_status_partial') || 'Missing endpoints'), cls: 'oauth-disconnected' };
    return { ok: false, label: (t('oauth_status_not_configured') || 'Not configured'), cls: 'oauth-disconnected' };
  }
  if (flow === 'oidc_discovery') {
    if (hasClientId && p.oidc_discovery_url) return { ok: true, label: (t('oauth_status_configured') || 'Configured'), cls: 'oauth-connected' };
    if (hasClientId) return { ok: false, label: (t('oauth_status_partial') || 'Missing discovery URL'), cls: 'oauth-disconnected' };
    return { ok: false, label: (t('oauth_status_not_configured') || 'Not configured'), cls: 'oauth-disconnected' };
  }
  if (flow === 'device_code') {
    if (hasClientId && p.device_code_url) return { ok: true, label: (t('oauth_status_configured') || 'Configured'), cls: 'oauth-connected' };
    if (hasClientId) return { ok: false, label: (t('oauth_status_partial') || 'Missing device URL'), cls: 'oauth-disconnected' };
    return { ok: false, label: (t('oauth_status_not_configured') || 'Not configured'), cls: 'oauth-disconnected' };
  }
  return { ok: false, label: (t('oauth_status_not_configured') || 'Not configured'), cls: 'oauth-disconnected' };
}

async function fetchOAuthProviders() {
  try {
    var res = await api('/api/oauth/providers');
    _memberOAuthProvidersCache = (res && res.providers) ? res.providers : [];
    _oauthYamlMigration = (res && res.yaml_migration) ? res.yaml_migration : null;
    return _memberOAuthProvidersCache;
  } catch (e) {
    _memberOAuthProvidersCache = [];
    _oauthYamlMigration = null;
    return [];
  }
}

function _oauthYamlMigrationBannerHTML() {
  var m = _oauthYamlMigration;
  if (!m || !m.deprecated) return '';
  var count = m.yaml_provider_count || 0;
  var title = t('oauth_yaml_migration_title') || 'Legacy config.yaml OAuth providers';
  var bodyTpl = t('oauth_yaml_migration_body') || (
    '{count} provider(s) are still defined under members.oauth.providers in config.yaml. '
    + 'Provider settings now live in state.db (this panel). Migrate once on the server:'
  );
  var body = bodyTpl.replace(/\{count\}/g, String(count));
  var cmd = m.cli_command || 'intellect oauth migrate-from-config --write-config';
  var markerNote = '';
  if (m.migration_marker) {
    markerNote = '<div style="margin-top:6px;font-size:11px;opacity:.85">'
      + esc(t('oauth_yaml_migration_marker_note') || 'A prior migration ran; clear the YAML list when ready.')
      + '</div>';
  }
  return '<div class="teams-banner oauth-migration-banner" role="status">'
    + '<strong>' + esc(title) + '</strong>'
    + '<div style="margin-top:6px;line-height:1.45">' + esc(body) + '</div>'
    + '<code style="display:block;margin-top:8px;font-size:11px;word-break:break-all">' + esc(cmd) + '</code>'
    + markerNote
    + '</div>';
}

function loadOAuthProvidersPanel() {
  var container = document.getElementById('oauthProvidersList');
  if (!container) return;
  container.innerHTML = '<div style="text-align:center;padding:24px;color:var(--muted)">' + esc(t('loading') || 'Loading...') + '</div>';
  fetchOAuthProviders().then(function () {
    renderOAuthProvidersPanel();
  });
}

function renderOAuthProvidersPanel() {
  var container = document.getElementById('oauthProvidersList');
  if (!container) return;

  var allProviders = _memberOAuthProvidersCache || [];

  var html = _oauthYamlMigrationBannerHTML();

  // Tab bar
  html += '<div class="oauth-tabs" style="display:flex;gap:4px;margin-bottom:12px;border-bottom:1px solid var(--border2);padding-bottom:0">';
  var tabs = [
    { key: 'login',  label: t('oauth_tab_login')  || 'Login Auth' },
    { key: 'model',  label: t('oauth_tab_model')  || 'Model Auth' },
    { key: 'server', label: t('oauth_tab_server') || 'Server Auth' }
  ];
  for (var i = 0; i < tabs.length; i++) {
    var active = tabs[i].key === _oauthPanelActiveTab ? ' active' : '';
    html += '<button class="oauth-tab' + active + '" onclick="_switchOAuthTab(\'' + tabs[i].key + '\')">' + esc(tabs[i].label) + '</button>';
  }
  html += '</div>';

  // Filtered providers
  var providers = [];
  for (var j = 0; j < allProviders.length; j++) {
    if (allProviders[j].usage === _oauthPanelActiveTab) {
      providers.push(allProviders[j]);
    }
  }

  if (!providers.length) {
    html += '<div style="text-align:center;padding:32px 0;color:var(--muted);font-size:13px">' + esc(t('oauth_providers_empty') || 'No OAuth providers configured.') + '</div>';
  } else {
    for (var k = 0; k < providers.length; k++) {
      html += _buildProviderCardHTML(providers[k]);
    }
  }
  container.innerHTML = html;
}

function _switchOAuthTab(tab) {
  _oauthPanelActiveTab = tab;
  renderOAuthProvidersPanel();
}

// ── Provider Card ──────────────────────────────────────────────────────────

function _buildProviderCardHTML(p) {
  var flowLabel = _flowLabel(p.auth_flow);
  var typeLabel = p.is_builtin ? (t('oauth_providers_builtin') || 'Built-in') : (t('oauth_providers_custom') || 'Custom');
  var configStatus = _getConfigStatus(p);
  var enabledLabel = p.enabled ? (t('oauth_providers_enabled') || 'Enabled') : (t('oauth_providers_disabled') || 'Disabled');
  var enabledClass = p.enabled ? 'oauth-connected' : 'oauth-disconnected';

  var html = '<div class="oauth-provider-card" id="opcard-' + esc(p.id) + '">';
  html += '<div class="oauth-card-header">';
  html += '<div style="display:flex;align-items:center;gap:10px">';
  html += _getProviderLogo(p);
  html += '<span class="oauth-card-name">' + esc(p.name || p.id) + '</span>';
  html += '</div>';
  html += '<div style="display:flex;align-items:center;gap:8px">';
  html += '<span class="oauth-badge" style="font-size:10px;opacity:.7">' + esc(flowLabel) + '</span>';
  html += '<span class="oauth-badge ' + configStatus.cls + '" title="' + esc(configStatus.label) + '">' + esc(configStatus.label) + '</span>';
  html += '<span class="oauth-badge ' + enabledClass + '">' + esc(enabledLabel) + '</span>';
  html += '</div></div>';

  html += '<div class="oauth-card-body">';
  html += _providerDetailFields(p);
  html += '<div class="oauth-detail" style="font-size:10px;color:var(--muted);margin-top:2px">' + esc(typeLabel) + '</div>';
  html += '</div>';

  html += '<div class="oauth-card-actions">';
  html += '<button class="sm-btn" onclick="toggleOAuthProvider(\'' + esc(p.id) + '\',' + (p.enabled ? 'false' : 'true') + ')" style="color:' + (p.enabled ? 'var(--warning)' : 'var(--accent)') + '">' + (p.enabled ? (t('oauth_providers_disable') || 'Disable') : (t('oauth_providers_enable') || 'Enable')) + '</button>';
  html += '<button class="sm-btn" onclick="showOAuthEditForm(\'' + esc(p.id) + '\')">' + esc(t('edit') || 'Edit') + '</button>';
  if (!p.is_builtin) {
    html += '<button class="sm-btn provider-card-btn-danger" onclick="deleteOAuthProvider(\'' + esc(p.id) + '\')">' + esc(t('delete') || 'Delete') + '</button>';
  }
  html += '</div>';

  html += '<div id="opedit-' + esc(p.id) + '" class="oauth-edit-form" style="display:none"></div>';
  html += '</div>';
  return html;
}

function _providerDetailFields(p) {
  var parts = [];
  if (p.client_id) parts.push('<span style="color:var(--muted)">client_id:</span> ' + esc(p.client_id));
  if (p.scopes && p.scopes.length) parts.push('<span style="color:var(--muted)">scopes:</span> ' + esc(p.scopes.join(', ')));
  if (p.oidc_discovery_url) parts.push('<span style="color:var(--muted)">discovery:</span> ' + esc(p.oidc_discovery_url));
  if (p.device_code_url) parts.push('<span style="color:var(--muted)">device_code_url:</span> ' + esc(p.device_code_url));
  if (p.token_storage && p.token_storage !== 'identities') parts.push('<span style="color:var(--muted)">storage:</span> ' + esc(p.token_storage));
  if (!parts.length) parts.push('<span style="color:var(--muted)">No endpoints configured</span>');
  var html = '';
  for (var i = 0; i < parts.length; i++) {
    html += '<div class="oauth-detail">' + parts[i] + '</div>';
  }
  return html;
}

// ── Enable/Disable ─────────────────────────────────────────────────────────

async function toggleOAuthProvider(id, enabled) {
  try {
    await api('/api/oauth/providers/' + encodeURIComponent(id), { method: 'PUT', body: JSON.stringify({ enabled: enabled }) });
    if (typeof showToast === 'function') showToast((t('oauth_providers_saved') || 'Saved'), 2000);
    loadOAuthProvidersPanel();
  } catch (e) {
    if (typeof showToast === 'function') showToast(e.message || String(e), 3000);
  }
}

// ── Edit ───────────────────────────────────────────────────────────────────

function showOAuthEditForm(id) {
  var container = document.getElementById('opedit-' + id);
  if (!container) return;
  if (container.style.display === 'block') {
    container.style.display = 'none'; container.innerHTML = ''; return;
  }
  var p = null;
  if (_memberOAuthProvidersCache) {
    for (var i = 0; i < _memberOAuthProvidersCache.length; i++) {
      if (_memberOAuthProvidersCache[i].id === id) { p = _memberOAuthProvidersCache[i]; break; }
    }
  }
  if (!p) return;

  var fields = _editFieldsForProvider(p);

  var html = '<div style="border-top:1px solid var(--border2);margin-top:8px;padding-top:8px">';
  html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">';

  // Usage — readonly (cannot change after creation)
  var usageLabel = p.usage === 'model' ? (t('oauth_tab_model') || 'Model Auth') : (p.usage === 'server' ? (t('oauth_tab_server') || 'Server Auth') : (t('oauth_tab_login') || 'Login Auth'));
  html += '<div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:2px">' + esc(t('oauth_field_usage') || 'Usage') + '</label><input type="text" value="' + esc(usageLabel) + '" disabled style="width:100%;padding:6px 8px;background:var(--border2);color:var(--muted);border:1px solid var(--border2);border-radius:6px;font-size:12px;cursor:not-allowed"></div>';
  // Auth Flow — readonly (cannot change after creation)
  var flowLabel = _flowLabel(p.auth_flow);
  html += '<div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:2px">' + esc(t('oauth_field_auth_flow') || 'Auth Flow') + '</label><input type="text" value="' + esc(flowLabel) + '" disabled style="width:100%;padding:6px 8px;background:var(--border2);color:var(--muted);border:1px solid var(--border2);border-radius:6px;font-size:12px;cursor:not-allowed"></div>';

  for (var f = 0; f < fields.length; f++) {
    html += _editFieldForProvider(p, fields[f]);
  }

  html += '</div>';
  html += '<div style="margin-top:8px;display:flex;gap:8px;justify-content:flex-end">';
  html += '<button class="sm-btn" onclick="cancelOAuthEdit(\'' + esc(id) + '\')">' + esc(t('cancel') || 'Cancel') + '</button>';
  html += '<button class="sm-btn" style="background:var(--accent);color:#fff" onclick="saveOAuthEdit(\'' + esc(id) + '\')">' + esc(t('save') || 'Save') + '</button>';
  html += '</div></div>';
  container.innerHTML = html;
  container.style.display = 'block';
}

function _collectCredentialBody(p, fieldDefs) {
  var body = {};
  var tenant = {};
  for (var i = 0; i < fieldDefs.length; i++) {
    var def = fieldDefs[i];
    var key = _fieldDefKey(def);
    if (!key || key === 'logo') continue;
    if (key === 'scopes') {
      var se = document.getElementById('opfield-scopes');
      if (se && se.value) body.scopes = se.value.split(/\s+/).filter(Boolean);
      continue;
    }
    if (key === 'tenant_config' && def.legacy) {
      var th = document.getElementById('opfield-tenant-header');
      var tm = document.getElementById('opfield-tenant-map');
      if (th || tm) body.tenant_config = { header: th ? th.value : 'X-Forwarded-User', map: tm ? tm.value : 'email' };
      continue;
    }
    var el = document.getElementById('opfield-' + key);
    if (!el || !el.value) continue;
    var value = el.value;
    if (typeof def === 'object' && def.scope === 'tenant') {
      tenant[key] = value;
    } else if (key === 'client_secret' || (typeof def === 'object' && def.db_column === 'client_secret_encrypted')) {
      body.client_secret = value;
    } else if (typeof def === 'object' && def.db_column && def.db_column !== 'client_secret_encrypted') {
      body[def.db_column] = value;
    } else {
      body[key] = value;
    }
  }
  if (Object.keys(tenant).length) {
    body.tenant_config = Object.assign({}, (p && p.tenant_config) || {}, tenant);
  }
  return body;
}

function _editFieldForProvider(p, field) {
  var key = _fieldDefKey(field);
  if (!key) return '';

  if (typeof field === 'object' && !field.legacy) {
    if (key === 'scopes') {
      return '<div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:2px">' + esc(t('oauth_field_scopes') || 'Scopes') + '</label><input type="text" id="opfield-scopes" value="' + esc((p.scopes || []).join(' ')) + '" style="width:100%;padding:6px 8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px;font-size:12px"></div>';
    }
    var label = field.label || key.replace(/_/g, ' ');
    var isSecret = !!field.secret;
    var val = '';
    var placeholder = label;
    if (field.scope === 'tenant') {
      var tc = p.tenant_config || {};
      val = tc[key] != null ? String(tc[key]) : '';
    } else if (field.db_column === 'client_id' || key === 'client_id') {
      val = '';
      if (p.has_client_id) placeholder = (t('oauth_field_configured') || 'Configured — enter to replace');
    } else if (isSecret) {
      val = '';
      if (p.has_client_secret) placeholder = (t('oauth_field_secret_keep') || 'Leave blank to keep current secret');
    } else {
      val = p[key] || '';
    }
    var reqMark = field.required ? ' *' : '';
    return '<div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:2px">' + esc(label) + esc(reqMark) + '</label><input type="' + (isSecret ? 'password' : 'text') + '" id="opfield-' + esc(key) + '" value="' + esc(val) + '" placeholder="' + esc(placeholder) + '" style="width:100%;padding:6px 8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px;font-size:12px"></div>';
  }

  if (key === 'scopes') {
    return '<div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:2px">' + esc(t('oauth_field_scopes') || 'Scopes') + '</label><input type="text" id="opfield-scopes" value="' + esc((p.scopes || []).join(' ')) + '" style="width:100%;padding:6px 8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px;font-size:12px"></div>';
  }
  if (key === 'tenant_config') {
    var tc = p.tenant_config || {};
    var headerName = tc.header || 'X-Forwarded-User';
    var mapMode = tc.map || 'email';
    return '<div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:2px">' + esc(t('oauth_field_header_name') || 'Header Name') + '</label><input type="text" id="opfield-tenant-header" value="' + esc(headerName) + '" style="width:100%;padding:6px 8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px;font-size:12px"></div>'
      + '<div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:2px">' + esc(t('oauth_field_map_mode') || 'Map Mode') + '</label><select id="opfield-tenant-map" style="width:100%;padding:6px 8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px;font-size:12px"><option value="email"' + (mapMode === 'email' ? ' selected' : '') + '>email</option><option value="username"' + (mapMode === 'username' ? ' selected' : '') + '>username</option></select></div>';
  }
  if (key === 'logo') {
    var logoPreview = p.logo_svg ? '<div class="oauth-provider-logo" style="margin-bottom:4px">' + p.logo_svg + '</div>' : '';
    var logoHint = (t('oauth_field_logo_hint') || 'Upload SVG or PNG file (optional)');
    return logoPreview + '<div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:2px">' + esc(t('oauth_field_logo') || 'Logo') + '</label><input type="file" id="opfield-logo_file" accept=".svg,.png" onchange="_readLogoFile(this)" style="width:100%;padding:6px 8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px;font-size:12px" title="' + esc(logoHint) + '"><input type="hidden" id="opfield-logo_svg" value="' + esc(p.logo_svg || '') + '"></div>';
  }

  var label = key.replace(/_/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); });
  var isSecret = key.indexOf('secret') >= 0;
  var val = p[key] || '';
  return '<div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:2px">' + esc(label) + '</label><input type="' + (isSecret ? 'password' : 'text') + '" id="opfield-' + key + '" value="' + esc(val) + '" placeholder="' + esc(label) + '" style="width:100%;padding:6px 8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px;font-size:12px"></div>';
}

function _readLogoFile(input) {
  var hidden = document.getElementById('opfield-logo_svg');
  var file = input.files && input.files[0];
  if (!file || !hidden) return;
  var reader = new FileReader();
  reader.onload = function () {
    hidden.value = reader.result;
  };
  // SVG: read as text; PNG: read as base64 data URL
  if (file.type === 'image/png') {
    reader.readAsDataURL(file);
  } else {
    reader.readAsText(file);
  }
}

function cancelOAuthEdit(id) {
  var container = document.getElementById('opedit-' + id);
  if (container) { container.style.display = 'none'; container.innerHTML = ''; }
}

async function saveOAuthEdit(id) {
  var p = null;
  if (_memberOAuthProvidersCache) {
    for (var i = 0; i < _memberOAuthProvidersCache.length; i++) {
      if (_memberOAuthProvidersCache[i].id === id) { p = _memberOAuthProvidersCache[i]; break; }
    }
  }
  var fields = p ? _editFieldsForProvider(p) : _editFieldsForFlow('pkce_loopback');
  var body = _collectCredentialBody(p, fields);
  // Always send logo update separately if modified
  var le2 = document.getElementById('opfield-logo_svg');
  if (le2 && le2.value) {
    try {
      await api('/api/oauth/providers/' + encodeURIComponent(id) + '/logo', { method: 'PUT', body: JSON.stringify({ logo_svg: le2.value }) });
    } catch (e) { /* non-fatal */ }
  }

  try {
    await api('/api/oauth/providers/' + encodeURIComponent(id), { method: 'PUT', body: JSON.stringify(body) });
    if (typeof showToast === 'function') showToast((t('oauth_providers_saved') || 'Saved'), 2000);
    cancelOAuthEdit(id);
    loadOAuthProvidersPanel();
  } catch (e) {
    if (typeof showToast === 'function') showToast(e.message || String(e), 3000);
  }
}

// ── Add ────────────────────────────────────────────────────────────────────

function showOAuthAddForm() {
  var container = document.getElementById('oauthProvidersList');
  if (!container) return;
  if (document.getElementById('opadd-form')) return;

  var html = '<div id="opadd-form" class="oauth-provider-card" style="border:2px solid var(--accent)" onchange="_onAddFormFlowChange()">';
  html += '<div class="oauth-card-header"><span class="oauth-card-name" style="color:var(--accent)">' + esc(t('oauth_providers_add_title') || 'Add OAuth Provider') + '</span></div>';
  html += '<div class="oauth-card-body">';
  html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">';

  // Common fields
  html += _editFieldHTML('id', (t('oauth_field_id') || 'Provider ID') + ' *', 'text', '');
  html += _editFieldHTML('name', (t('oauth_field_name') || 'Display Name') + ' *', 'text', '');
  html += '<div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:2px">' + esc(t('oauth_field_auth_flow') || 'Auth Flow') + '</label><select id="opfield-auth_flow" style="width:100%;padding:6px 8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px;font-size:12px"><option value="pkce_loopback">' + esc(_flowLabel('pkce_loopback')) + '</option><option value="oidc_discovery">' + esc(_flowLabel('oidc_discovery')) + '</option><option value="device_code">' + esc(_flowLabel('device_code')) + '</option><option value="trusted_header">' + esc(_flowLabel('trusted_header')) + '</option></select></div>';
  html += '<div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:2px">' + esc(t('oauth_field_usage') || 'Usage') + '</label><select id="opfield-usage" style="width:100%;padding:6px 8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px;font-size:12px"><option value="login"' + (_oauthPanelActiveTab === 'login' ? ' selected' : '') + '>' + esc(t('oauth_tab_login') || 'Login Auth') + '</option><option value="model"' + (_oauthPanelActiveTab === 'model' ? ' selected' : '') + '>' + esc(t('oauth_tab_model') || 'Model Auth') + '</option><option value="server"' + (_oauthPanelActiveTab === 'server' ? ' selected' : '') + '>' + esc(t('oauth_tab_server') || 'Server Auth') + '</option></select></div>';

  // Flow-specific fields (initially pkce_loopback) — display:contents makes children grid items
  html += '<div id="opadd-flow-fields" style="display:contents"></div>';

  html += '</div>';
  html += '<div style="margin-top:8px;display:flex;gap:8px;justify-content:flex-end">';
  html += '<button class="sm-btn" onclick="cancelOAuthAdd()">' + esc(t('cancel') || 'Cancel') + '</button>';
  html += '<button class="sm-btn" style="background:var(--accent);color:#fff" onclick="createOAuthProvider()">' + esc(t('oauth_providers_create') || 'Create') + '</button>';
  html += '</div></div></div>';

  container.insertAdjacentHTML('afterbegin', html);
  _renderAddFlowFields('pkce_loopback');
}

function _onAddFormFlowChange() {
  var sel = document.getElementById('opfield-auth_flow');
  if (sel) _renderAddFlowFields(sel.value);
}

function _renderAddFlowFields(flow) {
  var container = document.getElementById('opadd-flow-fields');
  if (!container) return;
  var fields = _editFieldsForFlow(flow);
  var html = '';
  for (var i = 0; i < fields.length; i++) {
    var key = _fieldDefKey(fields[i]);
    if (key === 'tenant_config') {
      html += '<div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:2px">' + esc(t('oauth_field_header_name') || 'Header Name') + '</label><input type="text" id="opfield-tenant-header" value="X-Forwarded-User" style="width:100%;padding:6px 8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px;font-size:12px"></div>';
      html += '<div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:2px">' + esc(t('oauth_field_map_mode') || 'Map Mode') + '</label><select id="opfield-tenant-map" style="width:100%;padding:6px 8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px;font-size:12px"><option value="email">email</option><option value="username">username</option></select></div>';
    } else if (key === 'scopes') {
      html += _editFieldHTML('scopes', 'oauth_field_scopes', 'text', 'openid profile email');
    } else if (key === 'logo') {
      var logoHint2 = (t('oauth_field_logo_hint') || 'Upload SVG or PNG file (optional)');
      html += '<div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:2px">' + esc(t('oauth_field_logo') || 'Logo') + '</label><input type="file" id="opfield-logo_file" accept=".svg,.png" onchange="_readLogoFile(this)" style="width:100%;padding:6px 8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px;font-size:12px" title="' + esc(logoHint2) + '"><input type="hidden" id="opfield-logo_svg" value=""></div>';
    } else {
      var i18nKey = 'oauth_field_' + key;
      var isSecret = key.indexOf('secret') >= 0;
      html += _editFieldHTML(key, i18nKey, isSecret ? 'password' : 'text', '');
    }
  }
  container.innerHTML = html;
}

function _editFieldHTML(id, labelKey, type, value) {
  var label = t(labelKey) || labelKey;
  var labelHtml = type === 'password' ? label + ' *' : label;
  return '<div><label style="font-size:11px;color:var(--muted);display:block;margin-bottom:2px">' + esc(labelHtml) + '</label><input type="' + type + '" id="opfield-' + id + '" value="' + esc(value) + '" placeholder="' + esc(label) + '" style="width:100%;padding:6px 8px;background:var(--code-bg);color:var(--text);border:1px solid var(--border2);border-radius:6px;font-size:12px"></div>';
}

function cancelOAuthAdd() {
  var form = document.getElementById('opadd-form');
  if (form) form.remove();
}

async function createOAuthProvider() {
  var id = (document.getElementById('opfield-id') || {}).value || '';
  var name = (document.getElementById('opfield-name') || {}).value || '';
  if (!id.trim() || !name.trim()) {
    if (typeof showToast === 'function') showToast((t('oauth_providers_id_name_required') || 'Provider ID and Name are required'), 3000);
    return;
  }
  var flow = (document.getElementById('opfield-auth_flow') || {}).value || 'pkce_loopback';
  var usage = (document.getElementById('opfield-usage') || {}).value || _oauthPanelActiveTab || 'login';
  var body = { id: id.trim(), name: name.trim(), auth_flow: flow, usage: usage, enabled: true };

  var merged = _collectCredentialBody(null, _editFieldsForFlow(flow));
  Object.assign(body, merged);
  var le = document.getElementById('opfield-logo_svg');
  if (le && le.value) body.logo_svg = le.value;

  try {
    await api('/api/oauth/providers', { method: 'POST', body: JSON.stringify(body) });
    if (typeof showToast === 'function') showToast((t('oauth_providers_created') || 'Provider created'), 2000);
    cancelOAuthAdd();
    loadOAuthProvidersPanel();
  } catch (e) {
    if (typeof showToast === 'function') showToast(e.message || String(e), 3000);
  }
}

// ── Delete ─────────────────────────────────────────────────────────────────

async function deleteOAuthProvider(id) {
  var ok = typeof showConfirmDialog === 'function'
    ? await showConfirmDialog({
        title: (t('oauth_providers_delete_title') || 'Delete OAuth Provider'),
        message: (t('oauth_providers_delete_msg') || 'Remove this OAuth provider? This cannot be undone.'),
        confirmLabel: (t('delete') || 'Delete'), danger: true, focusCancel: true
      })
    : confirm('Delete OAuth provider ' + id + '?');
  if (!ok) return;
  try {
    await api('/api/oauth/providers/' + encodeURIComponent(id), { method: 'DELETE' });
    if (typeof showToast === 'function') showToast((t('oauth_providers_deleted') || 'Provider deleted'), 2000);
    loadOAuthProvidersPanel();
  } catch (e) {
    if (typeof showToast === 'function') showToast(e.message || String(e), 3000);
  }
}
