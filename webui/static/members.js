/**
 * Members panel: password, OAuth identities, API tokens, member management.
 * Layout: side-menu (left) + content panes (right), matching Settings panel style.
 */

var _membersCurrentSection = 'password';
var _membersStatus = null;

const MEMBERS_PANE_IDS = {
  password: 'membersPanePassword',
  identities: 'membersPaneIdentities',
  tokens: 'membersPaneTokens',
  invites: 'membersPaneInvites',
  membersList: 'membersPaneMembersList',
  activity: 'membersPaneActivity',
};

const MEMBERS_SECTIONS = [
  { key: 'password',    icon: 'lock',      labelKey: 'members_section_password',    group: 'personal' },
  { key: 'identities',  icon: 'globe',     labelKey: 'members_section_identities',  group: 'personal' },
  { key: 'tokens',      icon: 'file-text', labelKey: 'members_section_tokens',      group: 'personal' },
  { key: 'invites',     icon: 'users',     labelKey: 'members_section_invites',     group: 'management', requireCap: 'can_invite' },
  { key: 'membersList', icon: 'clipboard-list', labelKey: 'members_section_list', group: 'management', requireCap: 'can_invite' },
  { key: 'activity',    icon: 'activity',  labelKey: 'members_section_activity',    group: 'management', requireCap: 'can_view_audit' },
];

function membersRegisterUrl() {
  try { return new URL('register', document.baseURI || window.location.href).href; }
  catch (e) { return '/register'; }
}

function membersLoginUrl() {
  try { return new URL('login', document.baseURI || window.location.href).href; }
  catch (e) { return '/login'; }
}

function membersOAuthReturnPath(extra) {
  var q = new URLSearchParams({ panel: 'members', membersSection: 'identities' });
  if (extra && extra.oauth) q.set('oauth', extra.oauth);
  if (extra && extra.oauth_error) q.set('oauth_error', extra.oauth_error);
  return '/?' + q.toString();
}

function membersOAuthLinkUrl(providerId) {
  var returnTo = membersOAuthReturnPath();
  var q = new URLSearchParams({
    provider: String(providerId || ''),
    link: '1',
    return_to: returnTo,
  });
  try {
    return new URL('api/members/oauth/authorize?' + q.toString(), document.baseURI || window.location.href).href;
  } catch (e2) {
    return '/api/members/oauth/authorize?' + q.toString();
  }
}

function membersOAuthLinkFormAction() {
  try {
    return new URL('api/members/me/identities/link', document.baseURI || window.location.href).href;
  } catch (e) {
    return '/api/members/me/identities/link';
  }
}

async function membersStartOAuthLink(providerId) {
  if (!providerId) return;
  var url = membersOAuthLinkUrl(providerId);
  // Native top-level navigation is the most reliable way to send HttpOnly cookies
  // to /api/members/oauth/authorize (fetch + redirect:'manual' yields an opaque
  // redirect for external IdPs and can fail to navigate in some browsers).
  window.location.assign(url);
}

function membersApi(path, opts) {
  opts = opts || {};
  opts.noAuthRedirect = true;
  return api(path, opts);
}

function membersPaneDomId(key) {
  return MEMBERS_PANE_IDS[key] || ('membersPane' + key.charAt(0).toUpperCase() + key.slice(1));
}

function membersEnsureMainPanes() {
  var main = document.getElementById('mainMembers');
  if (!main || document.getElementById('membersPanePassword')) return;
  main.innerHTML =
    '<div class="members-pane active" id="membersPanePassword"></div>'
    + '<div class="members-pane" id="membersPaneIdentities"></div>'
    + '<div class="members-pane" id="membersPaneTokens"></div>'
    + '<div class="members-pane" id="membersPaneInvites"></div>'
    + '<div class="members-pane" id="membersPaneMembersList"></div>'
    + '<div class="members-pane" id="membersPaneActivity"></div>';
}

function membersRenderSignInPrompt() {
  return '<div class="members-guest-panel">'
    + '<p class="panel-empty">' + esc(t('members_sign_in_or_register') || 'Sign in or create an account to continue.') + '</p>'
    + '<div class="members-token-row">'
    + '<a class="sm-btn provider-card-btn-primary" href="' + esc(membersRegisterUrl()) + '">' + esc(t('members_open_register') || 'Create account') + '</a>'
    + '<a class="sm-btn" href="' + esc(membersLoginUrl()) + '">' + esc(t('members_open_login') || 'Sign in') + '</a>'
    + '</div></div>';
}

// ── Panel entry ────────────────────────────────────────────────────────────

async function loadMembersPanel() {
  var menu = document.getElementById('membersSideMenu');
  if (!menu) return;

  try {
    var status = typeof fetchMembersStatus === 'function' ? await fetchMembersStatus() : null;
    _membersStatus = status;

    if (!status || !status.enabled) {
      menu.innerHTML = '';
      var mainMembers = document.getElementById('mainMembers');
      if (mainMembers) mainMembers.innerHTML = '<div class="members-pane active"><p class="panel-empty" style="padding:20px">' + esc(t('members_disabled') || 'Members are not enabled for this profile.') + '</p></div>';
      return;
    }
    if (!status.actor_member_id) {
      menu.innerHTML = '';
      var mm = document.getElementById('mainMembers');
      if (mm) mm.innerHTML = '<div class="members-pane active">' + membersRenderSignInPrompt() + '</div>';
      return;
    }

    membersEnsureMainPanes();

    var caps = status.capabilities || {};

    // Build sidebar menu
    var menuHtml = '';
    var lastGroup = '';
    for (var i = 0; i < MEMBERS_SECTIONS.length; i++) {
      var s = MEMBERS_SECTIONS[i];
      if (s.requireCap && !caps[s.requireCap]) continue;
      // Group separator
      if (s.group !== lastGroup && lastGroup !== '') {
        menuHtml += '<div class="members-menu-separator" style="height:1px;background:var(--border2);margin:4px 8px"></div>';
      }
      lastGroup = s.group;
      menuHtml += '<button type="button" class="side-menu-item' + (_membersCurrentSection === s.key ? ' active' : '') + '" data-members-section="' + s.key + '" onclick="switchMembersSection(\'' + s.key + '\')">'
        + (typeof li === 'function' ? li(s.icon, 16) : '')
        + '<span>' + esc(t(s.labelKey) || s.key) + '</span></button>';
    }

    // Teams shortcut
    if (status.teams_enabled) {
      menuHtml += '<div class="members-menu-separator" style="height:1px;background:var(--border2);margin:4px 8px"></div>';
      menuHtml += '<button type="button" class="side-menu-item" onclick="switchPanel(\'teams\')">'
        + (typeof li === 'function' ? li('users', 16) : '')
        + '<span>' + esc(t('teams_open_panel') || 'Teams') + ' →</span></button>';
    }
    if (status.projects_enabled) {
      menuHtml += '<div class="members-menu-separator" style="height:1px;background:var(--border2);margin:4px 8px"></div>';
      menuHtml += '<button type="button" class="side-menu-item" onclick="openProjectsPanel()">'
        + (typeof li === 'function' ? li('briefcase', 16) : '')
        + '<span>' + esc(t('projects_open_panel') || t('tab_projects') || 'Projects') + ' →</span></button>';
    }

    menu.innerHTML = menuHtml;

    // Render current section
    switchMembersSection(_membersCurrentSection);
  } catch (e) {
    menu.innerHTML = '<p class="members-error" style="padding:12px">' + esc(e.message || String(e)) + '</p>';
    var mainErr = document.getElementById('mainMembers');
    if (mainErr) {
      membersEnsureMainPanes();
      switchMembersSection(_membersCurrentSection);
    }
  }
}

// ── Section switching ──────────────────────────────────────────────────────

function switchMembersSection(name) {
  _membersCurrentSection = name;
  // Toggle active on menu items
  var menu = document.getElementById('membersSideMenu');
  if (menu) {
    menu.querySelectorAll('.side-menu-item').forEach(function (el) {
      el.classList.toggle('active', el.dataset.membersSection === name);
    });
  }
  membersEnsureMainPanes();

  var paneId = membersPaneDomId(name);
  // Toggle active on main-view panes
  var panes = document.querySelectorAll('#mainMembers .members-pane');
  for (var i = 0; i < panes.length; i++) {
    panes[i].classList.toggle('active', panes[i].id === paneId);
  }

  // Render into the corresponding pane
  var pane = document.getElementById(paneId);
  if (!pane) return;
  pane.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px">' + esc(t('loading') || 'Loading…') + '</div>';

  switch (name) {
    case 'password':    renderPasswordPane(pane); break;
    case 'identities':  renderIdentitiesPane(pane); break;
    case 'tokens':      renderTokensPane(pane); break;
    case 'invites':     renderInvitesPane(pane); break;
    case 'membersList': renderMembersListPane(pane); break;
    case 'activity':    renderActivityPane(pane); break;
  }
}

// ── Password pane ──────────────────────────────────────────────────────────

function renderPasswordPane(content) {
  var s = _membersStatus || {};
  var hasPw = Boolean(s.member_has_password);
  var html = '<section class="members-section"><h3 class="members-section-title">' + esc(t('members_section_password') || 'Password') + '</h3>';
  html += '<p class="members-hint" id="membersPasswordHint">'
    + (hasPw ? esc(t('member_password_change_sub') || 'Update your member sign-in password.') : esc(t('member_password_set_sub') || 'Set a password to sign in with member id + password.'))
    + '</p>';
  html += '<p class="members-error" id="membersPasswordError" hidden></p>';
  html += '<form id="membersPasswordForm" class="member-password-form">';
  html += '<label class="member-login-label" id="membersPasswordCurrentLabel" for="membersPasswordCurrent"' + (hasPw ? '' : ' hidden') + '>' + esc(t('member_password_current') || 'Current password') + '</label>';
  html += '<input type="password" id="membersPasswordCurrent" class="input" data-member-password-current autocomplete="current-password"' + (hasPw ? ' required' : ' hidden') + '>';
  html += '<label class="member-login-label" for="membersPasswordNew">' + esc(t('member_password_new') || 'New password') + '</label>';
  html += '<input type="password" id="membersPasswordNew" class="input" data-member-password-new autocomplete="new-password" required>';
  html += '<label class="member-login-label" for="membersPasswordConfirm">' + esc(t('member_password_confirm') || 'Confirm password') + '</label>';
  html += '<input type="password" id="membersPasswordConfirm" class="input" data-member-password-confirm autocomplete="new-password" required>';
  html += '<div class="member-password-actions"><button type="submit" class="sm-btn provider-card-btn-primary">' + esc(t('member_password_save') || 'Save password') + '</button></div>';
  html += '</form></section>';
  content.innerHTML = html;
  _bindPasswordForm();
}

function _bindPasswordForm() {
  var form = document.getElementById('membersPasswordForm');
  if (!form || form.dataset.bound) return;
  form.dataset.bound = '1';
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    void (async function () {
      var errEl = document.getElementById('membersPasswordError');
      if (errEl) errEl.hidden = true;
      var ok = await submitMemberPasswordChange(form, errEl);
      if (!ok) return;
      if (typeof showToast === 'function') showToast(t('member_password_saved') || 'Password saved', 3000);
      var fresh = typeof fetchMembersStatus === 'function' ? await fetchMembersStatus() : null;
      _membersStatus = fresh || _membersStatus;
      var pane = document.getElementById('membersPanePassword');
      if (pane) renderPasswordPane(pane);
      if (typeof refreshMemberChrome === 'function') await refreshMemberChrome();
    })();
  });
}

// ── Identities pane ────────────────────────────────────────────────────────

async function renderIdentitiesPane(content) {
  if (typeof invalidateMembersStatusCache === 'function') invalidateMembersStatusCache();
  var s = typeof fetchMembersStatus === 'function' ? await fetchMembersStatus() : (_membersStatus || {});
  _membersStatus = s || _membersStatus;
  if (!s || !s.actor_member_id) {
    content.innerHTML = '<div class="members-pane active">' + membersRenderSignInPrompt() + '</div>';
    return;
  }
  var html = '<section class="members-section"><h3 class="members-section-title">' + esc(t('members_section_identities') || 'Linked Accounts') + '</h3>';
  html += '<div id="membersLinkProviders"></div>';
  html += '<div id="membersIdentitiesList"></div></section>';
  content.innerHTML = html;
  await membersLoadLinkProviders(s);
  await membersLoadIdentities();
}

async function membersLoadLinkProviders(status) {
  var host = document.getElementById('membersLinkProviders');
  if (!host) return;
  if (!status || !status.oauth_enabled) { host.innerHTML = ''; return; }
  var providers = status.oauth_providers || [];
  if (!providers.length) {
    try { var listed = await membersApi('/api/members/oauth/providers'); providers = listed.providers || []; } catch (e) { providers = []; }
  }
  var linked = new Set();
  try {
    var data = await membersApi('/api/members/me/identities');
    for (var i = 0; i < (data.identities || []).length; i++) {
      var row = data.identities[i];
      var slug = String(row.provider || '').replace(/^oauth:/, '');
      if (slug) linked.add(slug);
    }
  } catch (e) { /* ignore */ }
  var linkable = providers.filter(function (p) { return p.id && !linked.has(p.id); });
  if (!linkable.length) {
    host.innerHTML = '<p class="members-hint">' + esc(t('members_all_providers_linked') || 'All configured providers are already linked.') + '</p>';
    return;
  }
  var returnTo = membersOAuthReturnPath();
  host.innerHTML = '<p class="members-hint" style="margin-bottom:8px">' + esc(t('members_link_provider_hint') || 'Link another sign-in method to this member:') + '</p>'
    + '<div class="members-link-providers" id="membersLinkProvidersGrid"></div>';
  var row = document.getElementById('membersLinkProvidersGrid');
  if (row && typeof renderMemberOAuthProviders === 'function') {
    renderMemberOAuthProviders(row, linkable, {
      mode: 'link',
      authorizeUrl: membersOAuthLinkUrl,
      groupLabel: t('members_link_provider_group') || t('oauth_choose_provider') || 'Link a provider',
    });
    return;
  }
  if (row) {
    var action = membersOAuthLinkFormAction();
    for (var k = 0; k < linkable.length; k++) {
      var prov = linkable[k];
      var form = document.createElement('form');
      form.method = 'POST';
      form.action = action;
      form.className = 'members-oauth-link-form';
      var hp = document.createElement('input');
      hp.type = 'hidden';
      hp.name = 'provider';
      hp.value = prov.id;
      form.appendChild(hp);
      var hr = document.createElement('input');
      hr.type = 'hidden';
      hr.name = 'return_to';
      hr.value = returnTo;
      form.appendChild(hr);
      var btn = document.createElement('button');
      btn.type = 'submit';
      btn.className = 'sm-btn provider-card-btn-primary members-oauth-text-link';
      btn.textContent = (t('members_link_provider') || 'Link') + ' ' + (prov.display_name || prov.id);
      form.appendChild(btn);
      row.appendChild(form);
    }
  }
}

async function membersLinkProvider(providerId) {
  return membersStartOAuthLink(providerId);
}

async function membersLoadIdentities() {
  var host = document.getElementById('membersIdentitiesList');
  if (!host) return;
  try {
    var data = await membersApi('/api/members/me/identities');
    var ids = data && data.identities ? data.identities : [];
    if (!ids.length) { host.innerHTML = '<p class="members-hint">' + esc(t('members_no_identities') || 'No linked OAuth identities.') + '</p>'; return; }
    host.innerHTML = '';
    for (var i = 0; i < ids.length; i++) {
      var row = ids[i];
      var div = document.createElement('div'); div.className = 'members-identity-row';
      var label = (row.display_name || row.provider_id || '') + (row.email ? ' · ' + row.email : '');
      var sub = row.provider_id ? esc(row.provider_id) + ' / ' + esc(row.external_id || '') : esc(row.platform || '') + ' / ' + esc(row.external_id || '');
      div.innerHTML = '<div class="members-identity-meta"><span class="members-identity-label">' + esc(label || sub) + '</span>'
        + (label ? '<span class="members-identity-sub">' + sub + '</span>' : '') + '</div>';
      var pid = row.provider_id || (String(row.platform || '').replace(/^oauth:/, ''));
      var btn = document.createElement('button'); btn.type = 'button';
      btn.className = 'sm-btn provider-card-btn-danger';
      btn.textContent = t('unlink') || 'Unlink';
      btn.onclick = (function (p, eid) { return function () { void membersUnlinkIdentity(p, eid); }; })(pid, row.external_id);
      div.appendChild(btn); host.appendChild(div);
    }
  } catch (e) {
    if (e && e.status === 401) {
      host.innerHTML = '<p class="members-hint">' + esc(t('members_sign_in_or_register') || 'Sign in or create an account to continue.') + '</p>';
      return;
    }
    host.innerHTML = '<p class="members-error">' + esc(e.message) + '</p>';
  }
}

async function membersUnlinkIdentity(providerId, externalId) {
  var enc = encodeURIComponent(providerId) + '/' + encodeURIComponent(externalId);
  await api('/api/members/me/identities/' + enc, { method: 'DELETE' });
  if (typeof showToast === 'function') showToast(t('members_unlinked') || 'Identity unlinked', 3000);
  var status = await fetchMembersStatus();
  _membersStatus = status;
  var pane = document.getElementById('membersPaneIdentities');
  if (pane) await renderIdentitiesPane(pane);
}

// ── Tokens pane ─────────────────────────────────────────────────────────────

function renderTokensPane(content) {
  var html = '<section class="members-section"><h3 class="members-section-title">' + esc(t('members_section_tokens') || 'API Tokens') + '</h3>';
  html += '<div class="members-token-row"><input type="text" id="membersTokenLabel" placeholder="' + esc(t('members_token_label') || 'Label') + '" class="input">';
  html += '<button type="button" class="sm-btn" id="membersCreateTokenBtn">' + esc(t('members_create_token') || 'Create token') + '</button></div>';
  html += '<div id="membersTokensList"></div></section>';
  content.innerHTML = html;
  var btn = document.getElementById('membersCreateTokenBtn');
  if (btn) btn.onclick = function () { void membersCreateToken(); };
  void membersLoadTokens();
}

async function membersLoadTokens() {
  var host = document.getElementById('membersTokensList');
  if (!host) return;
  try {
    var data = await api('/api/members/tokens');
    var tokens = data.tokens || [];
    if (!tokens.length) { host.innerHTML = '<p class="members-hint">' + esc(t('members_no_tokens') || 'No API tokens.') + '</p>'; return; }
    host.innerHTML = '<ul class="members-token-list"></ul>';
    var ul = host.querySelector('ul');
    for (var i = 0; i < tokens.length; i++) {
      var tok = tokens[i];
      var li = document.createElement('li'); li.className = 'members-token-item';
      li.innerHTML = '<code>' + esc(tok.id) + '</code> ' + esc(tok.label || '') + ' <span class="oauth-badge ' + (tok.status === 'active' ? 'oauth-connected' : 'oauth-disconnected') + '">' + esc(tok.status) + '</span>';
      if (tok.status === 'active') {
        var btn = document.createElement('button'); btn.type = 'button'; btn.className = 'sm-btn';
        btn.textContent = t('revoke') || 'Revoke';
        btn.onclick = (function (tid) { return function () { void membersRevokeToken(tid); }; })(tok.id);
        li.appendChild(btn);
      }
      ul.appendChild(li);
    }
  } catch (e) { host.innerHTML = '<p class="members-error">' + esc(e.message) + '</p>'; }
}

async function membersCreateToken() {
  var label = (document.getElementById('membersTokenLabel') || {}).value || '';
  var data = await api('/api/members/tokens', { method: 'POST', body: JSON.stringify({ label: label }) });
  if (data.bearer) {
    if (typeof showToast === 'function') showToast(t('members_token_created') || 'Token created (copy now): ' + data.bearer, 5000);
    try { await copyToClipboard(data.bearer); } catch (e) { /* ignore */ }
  }
  await membersLoadTokens();
}

async function membersRevokeToken(tokenId) {
  await api('/api/members/tokens/' + encodeURIComponent(tokenId), { method: 'DELETE' });
  await membersLoadTokens();
}

// ── Member management pane (invites + pending registrations) ───────────────

function renderInvitesPane(content) {
  var s = _membersStatus || {};
  var caps = s.capabilities || {};
  var html = '<section class="members-section"><h3 class="members-section-title">' + esc(t('members_section_members') || 'Member Management') + '</h3>';

  // Invites
  if (caps.can_invite) {
    html += '<h4 style="font-size:12px;font-weight:600;margin:12px 0 6px">' + esc(t('members_invites') || 'Invite Member') + '</h4>';
    html += '<div class="members-token-row"><button type="button" class="sm-btn" id="membersInviteBtn">' + esc(t('members_create_invite') || 'Create invite') + '</button></div>';
    html += '<pre id="membersInviteCode" class="members-invite-code" hidden></pre>';
  }

  // Pending registrations
  if (caps.can_approve_registrations && s.local_registration_requires_approval) {
    html += '<h4 style="font-size:12px;font-weight:600;margin:12px 0 6px">' + esc(t('members_pending_registrations') || 'Pending Registrations') + '</h4>';
    html += '<div id="membersPendingRegistrations"><span class="members-hint">' + esc(t('loading') || 'Loading…') + '</span></div>';
  }

  html += '</section>';
  content.innerHTML = html;

  var inviteBtn = document.getElementById('membersInviteBtn');
  if (inviteBtn) inviteBtn.onclick = function () { void membersCreateInvite(); };
  if (caps.can_approve_registrations && s.local_registration_requires_approval) {
    void membersLoadPendingRegistrations();
  }
}

async function membersCreateInvite() {
  // M1=A: member_id is always auto-generated by the agent (12-char hex).
  // The user-facing form no longer accepts a custom id.
  var data = await api('/api/members/invites', { method: 'POST', body: '{}' });
  var pre = document.getElementById('membersInviteCode');
  if (pre && data.code) {
    pre.hidden = false; pre.textContent = data.code;
    try { await copyToClipboard(data.code); } catch (e) { /* ignore */ }
    if (typeof showToast === 'function') showToast(t('members_invite_copied') || 'Invite code created (copied)', 3000);
  }
}

async function membersLoadPendingRegistrations() {
  var host = document.getElementById('membersPendingRegistrations');
  if (!host) return;
  try {
    var data = await api('/api/members/registrations/pending');
    var rows = data.registrations || [];
    if (!rows.length) { host.innerHTML = '<p class="members-hint">' + esc(t('members_pending_registrations_empty') || 'No pending registrations.') + '</p>'; return; }
    host.innerHTML = '';
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      var div = document.createElement('div'); div.className = 'members-pending-row';
      var label = (row.display_name || row.id || '').trim();
      div.innerHTML = '<div class="members-identity-meta"><span class="members-identity-label">' + esc(label) + '</span>'
        + '<span class="members-identity-sub"><code>' + esc(row.id || '') + '</code></span></div>'
        + '<div class="members-token-row">'
        + '<button type="button" class="sm-btn approve-btn">' + esc(t('members_approve_registration') || 'Approve') + '</button>'
        + '<button type="button" class="sm-btn reject-btn">' + esc(t('members_reject_registration') || 'Reject') + '</button>'
        + '</div>';
      var buttons = div.querySelectorAll('button');
      if (buttons[0]) buttons[0].onclick = (function (mid) { return function () { void membersApproveRegistration(mid); }; })(row.id);
      if (buttons[1]) buttons[1].onclick = (function (mid) { return function () { void membersRejectRegistration(mid); }; })(row.id);
      host.appendChild(div);
    }
  } catch (e) { host.innerHTML = '<p class="members-error">' + esc(e.message) + '</p>'; }
}

async function membersApproveRegistration(memberId) {
  if (!memberId) return;
  try {
    await api('/api/members/registrations/' + encodeURIComponent(memberId) + '/approve', { method: 'POST', body: '{}' });
    if (typeof showToast === 'function') showToast(t('members_registration_approved') || 'Registration approved', 3000);
    await membersLoadPendingRegistrations();
  } catch (e) { if (typeof showToast === 'function') showToast(e.message || String(e), 3000); }
}

async function membersRejectRegistration(memberId) {
  if (!memberId) return;
  try {
    await api('/api/members/registrations/' + encodeURIComponent(memberId) + '/reject', { method: 'POST', body: '{}' });
    if (typeof showToast === 'function') showToast(t('members_registration_rejected') || 'Registration rejected', 3000);
    await membersLoadPendingRegistrations();
  } catch (e) { if (typeof showToast === 'function') showToast(e.message || String(e), 3000); }
}

// ── Admin activity / audit pane (v1.5) ───────────────────────────────────

function renderActivityPane(content) {
  content.innerHTML = '<section class="members-section"><h3 class="members-section-title">'
    + esc(t('members_section_activity') || 'Admin Activity') + '</h3>'
    + '<p class="members-hint">' + esc(t('members_activity_hint') || 'Recent owner/admin actions (approvals, invites, deletions).') + '</p>'
    + '<div id="membersAuditList"><span class="members-hint">' + esc(t('loading') || 'Loading…') + '</span></div>'
    + '<button type="button" class="sm-btn" id="membersAuditRefresh" style="margin-top:8px">'
    + esc(t('refresh') || 'Refresh') + '</button></section>';
  var btn = document.getElementById('membersAuditRefresh');
  if (btn) btn.onclick = function () { void membersLoadAuditList(); };
  void membersLoadAuditList();
}

function _formatAuditTime(ts) {
  if (!ts) return '—';
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch (e) {
    return String(ts);
  }
}

function _formatAuditAction(action) {
  var labels = {
    registration_approve: t('audit_action_approve') || 'Approved registration',
    registration_reject: t('audit_action_reject') || 'Rejected registration',
    invite_create: t('audit_action_invite') || 'Created invite',
    invite_redeem: t('audit_action_redeem') || 'Redeemed invite',
    member_delete: t('audit_action_delete') || 'Deleted member',
    member_deactivate: t('audit_action_deactivate') || 'Deactivated member',
    member_activate: t('audit_action_activate') || 'Activated member',
  };
  return labels[action] || action || '—';
}

async function membersLoadAuditList() {
  var host = document.getElementById('membersAuditList');
  if (!host) return;
  host.innerHTML = '<span class="members-hint">' + esc(t('loading') || 'Loading…') + '</span>';
  try {
    var data = await api('/api/members/audit?limit=80');
    var entries = data.entries || [];
    if (!entries.length) {
      host.innerHTML = '<p class="members-hint">' + esc(t('members_activity_empty') || 'No admin activity recorded yet.') + '</p>';
      return;
    }
    var html = '<div style="overflow-x:auto"><table class="members-table"><thead><tr>'
      + '<th>' + esc(t('audit_col_time') || 'Time') + '</th>'
      + '<th>' + esc(t('audit_col_action') || 'Action') + '</th>'
      + '<th>' + esc(t('audit_col_actor') || 'Actor') + '</th>'
      + '<th>' + esc(t('audit_col_target') || 'Target') + '</th>'
      + '<th>' + esc(t('audit_col_source') || 'Source') + '</th>'
      + '</tr></thead><tbody>';
    for (var i = 0; i < entries.length; i++) {
      var e = entries[i];
      html += '<tr>'
        + '<td style="white-space:nowrap;font-size:11px">' + esc(_formatAuditTime(e.timestamp)) + '</td>'
        + '<td>' + esc(_formatAuditAction(e.action)) + '</td>'
        + '<td>' + esc(e.actor_display_name || e.actor_member_id || '—') + '</td>'
        + '<td>' + esc(e.target_display_name || e.target_member_id || '—') + '</td>'
        + '<td><code style="font-size:10px">' + esc(e.source || '') + '</code></td>'
        + '</tr>';
    }
    html += '</tbody></table></div>';
    host.innerHTML = html;
  } catch (err) {
    host.innerHTML = '<p class="members-error">' + esc(err.message || String(err)) + '</p>';
  }
}

// ── Member list pane ───────────────────────────────────────────────────────

function membersFormatScopeList(items) {
  if (!items || !items.length) return '';
  return items.map(function (item) {
    if (item && typeof item === 'object') {
      var id = item.id || item.slug || '';
      var role = item.role || '';
      return role ? (id + ' (' + role + ')') : id;
    }
    return String(item);
  }).join(', ');
}

function renderMembersListPane(content) {
  content.innerHTML = '<div class="members-pane-inner"><h3 class="members-section-title">' + esc(t('members_section_list') || 'Member List') + '</h3>'
    + '<div id="membersListTable"><span class="members-hint">' + esc(t('loading') || 'Loading…') + '</span></div></div>';
  void loadMembersList();
}

async function loadMembersList() {
  var host = document.getElementById('membersListTable');
  if (!host) return;
  try {
    var data = await api('/api/members');
    var members = data.members || [];
    if (!members.length) {
      host.innerHTML = '<p class="members-hint">' + esc(t('members_no_members') || 'No members found.') + '</p>';
      return;
    }
    var html = '<div style="overflow-x:auto"><table class="members-table"><thead><tr>'
      + '<th>' + esc(t('member_display_name') || 'Name') + '</th>'
      + '<th>' + esc(t('member_role') || 'Role') + '</th>'
      + '<th>' + esc(t('member_teams') || 'Teams') + '</th>'
      + '<th>' + esc(t('member_projects') || 'Projects') + '</th>'
      + '<th>' + esc(t('member_status') || 'Status') + '</th>'
      + '<th>' + esc(t('member_actions') || 'Actions') + '</th>'
      + '</tr></thead><tbody>';
    var currentActorId = (_membersStatus && _membersStatus.actor_member_id) || '';
    var caps = (_membersStatus && _membersStatus.capabilities) || {};
    var canGrant = caps.can_grant_admin || caps.can_grant_owner;
    var canLifecycleOwners = Boolean(caps.can_lifecycle_manage_owners);
    var canDeleteMembers = Boolean(caps.can_delete_members);
    var canInvite = Boolean(caps.can_invite);
    for (var i = 0; i < members.length; i++) {
      var m = members[i];
      var isSelf = m.id === currentActorId;
      var targetIsOwner = (m.role || '') === 'owner';
      var canLifecycle = !isSelf && canInvite && (!targetIsOwner || canLifecycleOwners);
      var statusLabel = m.enabled ? (esc(t('member_active') || 'Active')) : (esc(t('member_inactive') || 'Inactive'));
      var statusClass = m.enabled ? 'oauth-connected' : 'oauth-disconnected';
      var teamsStr = membersFormatScopeList(m.teams) || '—';
      var projectsStr = membersFormatScopeList(m.projects) || '—';
      html += '<tr>'
        + '<td>' + esc(m.display_name || m.login_name || '') + '</td>'
        + '<td><span class="oauth-badge">' + esc(m.role || 'member') + '</span></td>'
        + '<td style="font-size:11px">' + esc(teamsStr) + '</td>'
        + '<td style="font-size:11px">' + esc(projectsStr) + '</td>'
        + '<td><span class="oauth-badge ' + statusClass + '">' + statusLabel + '</span></td>'
        + '<td><div class="members-actions" data-member-id="' + esc(m.id) + '">'
        + (isSelf ? '<span class="members-hint">' + esc(t('member_self') || 'You') + '</span>'
            : (canLifecycle
                ? (m.enabled
                    ? '<button class="sm-btn" onclick="membersAction(\'' + esc(m.id) + '\',\'deactivate\')">' + esc(t('member_deactivate') || 'Deactivate') + '</button>'
                    : '<button class="sm-btn" onclick="membersAction(\'' + esc(m.id) + '\',\'activate\')">' + esc(t('member_activate') || 'Activate') + '</button>')
                  + '<button class="sm-btn" onclick="membersAction(\'' + esc(m.id) + '\',\'reset-password\')">' + esc(t('member_reset_pw') || 'Reset PW') + '</button>'
                : (targetIsOwner && !canLifecycleOwners
                    ? '<span class="members-hint">' + esc(t('member_owner_protected') || 'Owner — owner only') + '</span>'
                    : ''))
              + (canGrant && m.role !== 'owner' && m.role !== 'admin'
                  ? '<button class="sm-btn" onclick="membersAction(\'' + esc(m.id) + '\',\'set-admin\')">' + esc(t('member_set_admin') || 'Set as Admin') + '</button>'
                  : '')
              + (canGrant && m.role !== 'owner'
                  ? '<button class="sm-btn provider-card-btn-danger" onclick="membersAction(\'' + esc(m.id) + '\',\'set-owner\')">' + esc(t('member_set_owner') || 'Set as Owner') + '</button>'
                  : '')
              + (canDeleteMembers
                  ? '<button class="sm-btn provider-card-btn-danger" onclick="membersAction(\'' + esc(m.id) + '\',\'delete\')">' + esc(t('member_delete') || 'Delete') + '</button>'
                  : ''))
        + '</div></td>'
        + '</tr>';
    }
    html += '</tbody></table></div>';
    host.innerHTML = html;
  } catch (e) {
    host.innerHTML = '<p class="members-error">' + esc(e.message) + '</p>';
  }
}

async function membersAction(memberId, action) {
  if (!memberId || !action) return;
  var labels = { activate: 'Activate', deactivate: 'Deactivate', 'reset-password': 'Reset password', delete: 'Delete',
    'set-admin': (t('member_set_admin') || 'Set as Admin'),
    'set-owner': (t('member_set_owner') || 'Set as Owner') };
  var label = labels[action] || action;
  if (action === 'delete') {
    var ok = typeof showConfirmDialog === 'function'
      ? await showConfirmDialog({ title: (t('member_delete_confirm_title') || 'Delete Member'), message: (t('member_delete_confirm_msg') || 'Permanently delete this member? This cannot be undone.'), confirmLabel: 'Delete', danger: true, focusCancel: true })
      : confirm('Delete member ' + memberId + '?');
    if (!ok) return;
  }
  if (action === 'set-admin') {
    var okAdmin = typeof showConfirmDialog === 'function'
      ? await showConfirmDialog({ title: (t('member_set_admin_confirm_title') || 'Set as Admin'), message: (t('member_set_admin_confirm_msg') || 'Grant admin role to this member?'), confirmLabel: (t('member_set_admin') || 'Set as Admin'), danger: false, focusCancel: true })
      : confirm('Grant admin role to ' + memberId + '?');
    if (!okAdmin) return;
  }
  if (action === 'set-owner') {
    var okOwner = typeof showConfirmDialog === 'function'
      ? await showConfirmDialog({ title: (t('member_set_owner_confirm_title') || 'Transfer Ownership'), message: (t('member_set_owner_confirm_msg') || 'Transfer ownership to this member?'), confirmLabel: (t('member_set_owner') || 'Set as Owner'), danger: true, focusCancel: true })
      : confirm('Transfer ownership to ' + memberId + '?');
    if (!okOwner) return;
  }
  if (action === 'reset-password') {
    var okReset = typeof showConfirmDialog === 'function'
      ? await showConfirmDialog({ title: (t('member_reset_pw_confirm_title') || 'Reset Password'), message: (t('member_reset_pw_confirm_msg') || 'Generate a temporary password? The member will be required to change it on next login.'), confirmLabel: (t('member_reset_pw') || 'Reset Password'), danger: false, focusCancel: true })
      : confirm('Reset password for ' + memberId + '?');
    if (!okReset) return;
  }
  try {
    var method = action === 'delete' ? 'DELETE' : 'POST';
    var url = '/api/members/' + encodeURIComponent(memberId) + '/' + action;
    var res = await api(url, { method: method, body: '{}' });
    var data = (res && typeof res === 'object') ? res : {};
    if (action === 'reset-password' && data.temp_password) {
      var copied = typeof showConfirmDialog === 'function'
        ? await showConfirmDialog({
            title: (t('member_reset_pw_done_title') || 'Password Reset'),
            message: (t('member_reset_pw_temp_label') || 'Temporary password:') + '\n\n' + data.temp_password + '\n\n' + (t('member_reset_pw_temp_hint') || 'Share this with the member. They must change it on next login.'),
            confirmLabel: (t('copy_and_close') || 'Copy & Close'),
            cancelLabel: (t('close') || 'Close'),
            danger: false, focusCancel: false
          })
        : false;
      if (copied && navigator && navigator.clipboard) {
        try { await navigator.clipboard.writeText(data.temp_password); } catch (_) {}
      }
      if (typeof showToast === 'function') showToast((t('member_reset_pw_ok') || 'Password reset'), 3000);
    } else {
      var toastLabels = { activate: 'Activate', deactivate: 'Deactivate',
        delete: 'Delete', 'set-admin': (t('member_set_admin_ok') || 'Admin role granted'),
        'set-owner': (t('member_set_owner_ok') || 'Ownership transferred') };
      if (typeof showToast === 'function') showToast((toastLabels[action] || (label + ' OK')), 3000);
    }
    await loadMembersList();
  } catch (e) { if (typeof showToast === 'function') showToast(e.message || String(e), 3000); }
}

function membersOAuthErrorMessage(code) {
  var key = 'members_oauth_err_' + String(code || '').replace(/[^a-z0-9_]/gi, '_').toLowerCase();
  var msg = t(key);
  if (msg && msg !== key) return msg;
  if (code === 'token_exchange_failed') {
    return t('members_oauth_err_token_exchange') || 'OAuth token exchange failed. Try linking again.';
  }
  if (code === 'bind_failed') {
    return t('members_oauth_err_bind_failed') || 'Could not bind this OAuth account.';
  }
  if (code === 'missing_claims') {
    return t('members_oauth_err_missing_claims') || 'OAuth provider did not return identity details.';
  }
  return t('members_oauth_err_generic') || 'OAuth link failed. Try again.';
}

async function membersApplyStartupQuery() {
  try {
    var params = new URLSearchParams(window.location.search);
    if (params.get('panel') !== 'members') return;
    var section = params.get('membersSection') || 'identities';
    var oauth = params.get('oauth');
    var oauthErr = params.get('oauth_error');
    if (typeof switchPanel === 'function') await switchPanel('members');
    if (typeof switchMembersSection === 'function') switchMembersSection(section);
    if (oauth === 'linked' && typeof showToast === 'function') {
      showToast(t('members_identity_linked') || 'OAuth account linked.', 4000);
    }
    if (oauthErr && typeof showToast === 'function') {
      showToast(membersOAuthErrorMessage(oauthErr), 5000);
    }
    ['panel', 'membersSection', 'oauth', 'oauth_error'].forEach(function (k) { params.delete(k); });
    var clean = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
    window.history.replaceState({}, '', clean);
  } catch (e) { /* ignore */ }
}

if (typeof window !== 'undefined') {
  window.membersApplyStartupQuery = membersApplyStartupQuery;
}
