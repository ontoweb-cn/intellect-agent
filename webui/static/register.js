/* Register page — local account, OAuth, and invite-code registration.
   lgtm[js/xss]: innerHTML content comes from server-generated I18N/HTML, not user input.
   lgtm[js/client-side-unvalidated-url-redirection]: redirects go to server-controlled paths. */
document.addEventListener('DOMContentLoaded', function () {
  var I18N = window.__REGISTER_I18N__ || {};
  function L(key, fallback) {
    var v = I18N[key];
    return (v && String(v)) || fallback || '';
  }

  var errEl = document.getElementById('err');
  var closedEl = document.getElementById('register-closed');
  var openEl = document.getElementById('register-open');
  var form = document.getElementById('register-form');
  var submitBtn = document.getElementById('register-submit-btn');
  var oauthHost = document.getElementById('register-oauth-host');
  var inviteFields = document.getElementById('register-invite-fields');
  var localHint = document.getElementById('register-local-hint');
  var displayNameInput = document.getElementById('register-display-name');
  var passwordInput = document.getElementById('register-password');
  var passwordConfirmInput = document.getElementById('register-password-confirm');
  var inviteInput = document.getElementById('register-invite-code');
  var displayNameHint = document.getElementById('register-display-name-hint');
  var tabLocal = document.getElementById('register-tab-local');
  var tabOAuth = document.getElementById('register-tab-oauth');
  var tabInvite = document.getElementById('register-tab-invite');

  var connFailed = L('conn_failed', 'Connection failed');
  var activeTab = 'local';
  var localRegistrationEnabled = true;
  var registrationToken = null;
  var checkTimers = { display_name: null };

  function _api(path) {
    var p = String(path || '');
    if (/^https?:\/\//i.test(p)) return p;
    if (p.charAt(0) !== '/') p = '/' + p;
    return new URL(p, window.location.origin).href;
  }

  function showErr(msg) {
    if (!errEl) return;
    errEl.textContent = msg;
    errEl.style.display = 'block';
    errEl.style.color = '#e94560';
  }

  function hideErr() {
    if (errEl) errEl.style.display = 'none';
  }

  function _safeNextPath() {
    try {
      var raw = new URL(window.location.href).searchParams.get('next');
      if (!raw) return './';
      if (raw.charAt(0) !== '/') return './';
      if (raw.charAt(1) === '/' || raw.charAt(1) === '\\') return './';
      if (/[\x00-\x1f\x7f\s]/.test(raw)) return './';
      return raw;
    } catch (_) {
      return './';
    }
  }

  function _safeRedirect(url) {
    // Relative paths (starting with / or ./) are always same-origin.
    if (url && (url.charAt(0) === '/' && url.charAt(1) !== '/' && url.charAt(1) !== '\\')) {
      window.location.href = url;
      return;
    }
    if (url && url.indexOf('./') === 0 && url.indexOf('//') !== 0) {
      window.location.href = url;
      return;
    }
    // Absolute URLs: only allow same-origin https/http.
    try {
      var parsed = new URL(url, window.location.origin);
      if ((parsed.protocol === 'https:' || parsed.protocol === 'http:') &&
          parsed.origin === window.location.origin) {
        window.location.href = parsed.href;
        return;
      }
    } catch (_) {}
    // Fallback: navigate to current directory.
    window.location.href = './';
  }

  function _safeExternalRedirect(url) {
    // Only allow HTTPS external redirects (e.g. OAuth provider pages).
    try {
      var parsed = new URL(url, window.location.origin);
      if (parsed.protocol === 'https:') {
        window.location.href = parsed.href;
        return;
      }
    } catch (_) {}
    window.location.href = './';
  }

  function setFieldHint(el, msg, isErr) {
    if (!el) return;
    el.textContent = msg || '';
    el.classList.toggle('err', Boolean(isErr && msg));
  }

  function collectFields() {
    return {
      display_name: displayNameInput ? displayNameInput.value.trim() : '',
      password: passwordInput ? passwordInput.value : '',
      password_confirm: passwordConfirmInput ? passwordConfirmInput.value : '',
      code: inviteInput ? inviteInput.value.trim() : '',
    };
  }

  function validateLocalFields(fields) {
    if (!fields.display_name || !fields.password || !fields.password_confirm) {
      return L('register_fill_required', 'Complete all required fields');
    }
    if (fields.password !== fields.password_confirm) {
      return L('register_password_mismatch', 'Passwords do not match');
    }
    if (activeTab === 'invite' && !fields.code) {
      return L('redeem_code_required', 'Enter an invite code');
    }
    return null;
  }

  async function checkAvailability(field, value) {
    if (!value) return;
    var q = new URLSearchParams();
    q.set(field, value);
    try {
      var res = await fetch(_api('api/members/register/check?' + q.toString()), {
        credentials: 'include',
      });
      var data = {};
      try { data = await res.json(); } catch (_) {}
      if (!res.ok) return;
      if (field === 'display_name') {
        if (data.display_name_available === false) {
          setFieldHint(displayNameHint, L('register_display_name_taken', 'Display name is already taken'), true);
        } else if (data.display_name_error) {
          setFieldHint(displayNameHint, data.display_name_error, true);
        } else {
          setFieldHint(displayNameHint, '', false);
        }
      }
    } catch (_) {}
  }

  function scheduleCheck(field, value) {
    if (checkTimers[field]) clearTimeout(checkTimers[field]);
    checkTimers[field] = setTimeout(function () {
      void checkAvailability(field, value);
    }, 350);
  }

  function updateSubmitLabel() {
    if (!submitBtn) return;
    if (activeTab === 'oauth') {
      submitBtn.classList.add('hidden');
      return;
    }
    submitBtn.classList.remove('hidden');
    if (activeTab === 'local') {
      submitBtn.textContent = L('register_submit', 'Create account');
    } else {
      submitBtn.textContent = L('register_submit', 'Create account');
    }
  }

  function setTab(tab) {
    if (tab === 'local' && !localRegistrationEnabled) {
      tab = 'oauth';
    }
    activeTab = tab === 'invite' ? 'invite' : tab === 'oauth' ? 'oauth' : 'local';
    if (tabLocal) tabLocal.classList.toggle('active', activeTab === 'local');
    if (tabOAuth) tabOAuth.classList.toggle('active', activeTab === 'oauth');
    if (tabInvite) tabInvite.classList.toggle('active', activeTab === 'invite');
    if (inviteFields) inviteFields.classList.toggle('hidden', activeTab !== 'invite');
    if (localHint) localHint.classList.toggle('hidden', activeTab !== 'local');
    if (oauthHost) oauthHost.classList.toggle('hidden', activeTab !== 'oauth');
    registrationToken = null;
    if (activeTab === 'oauth') {
      var ctx = window.__REGISTER_CONTEXT__ || null;
      renderOAuthButtons((ctx && ctx.members && ctx.members.oauth_providers) || []);
    } else if (oauthHost) {
      oauthHost.innerHTML = '';
    }
  }

  function applyLocalTabVisibility(ctx) {
    localRegistrationEnabled = Boolean(ctx && ctx.local_registration_requires_approval);
    if (tabLocal) {
      tabLocal.classList.toggle('hidden', !localRegistrationEnabled);
      tabLocal.style.display = localRegistrationEnabled ? '' : 'none';
    }
    var defaultTab = (ctx && ctx.register_default_tab) || (localRegistrationEnabled ? 'local' : 'oauth');
    setTab(defaultTab);
  }

  function renderOAuthButtons(providers) {
    if (!oauthHost) return;
    if (typeof renderMemberOAuthProviders !== 'function') {
      oauthHost.innerHTML = '';
      return;
    }
    if (activeTab !== 'oauth') {
      oauthHost.innerHTML = '';
      return;
    }
    renderMemberOAuthProviders(oauthHost, providers, {
      mode: 'register',
      i18n: I18N,
      hintMessage: L(
        'register_oauth_hint',
        'Fill in display name and password, then pick a provider icon.'
      ),
      onSelect: function (providerId) {
        void startOAuthRegistration(providerId);
      },
    });
  }

  function applyContext(ctx) {
    hideErr();
    window.__REGISTER_CONTEXT__ = ctx;
    var open = ctx && ctx.registration_open;
    if (closedEl) closedEl.classList.toggle('hidden', open);
    if (openEl) openEl.classList.toggle('hidden', !open);
    if (!open) {
      if (ctx && ctx.login_complete) {
        _safeRedirect(_safeNextPath());
      }
      return;
    }
    if (ctx && ctx.login_complete) {
      _safeRedirect(_safeNextPath());
      return;
    }
    renderOAuthButtons((ctx.members && ctx.members.oauth_providers) || []);
    applyLocalTabVisibility(ctx);
    if (tabOAuth) {
      var oauthProviders = (ctx.members && ctx.members.oauth_providers) || [];
      var hasOAuth = oauthProviders.length > 0;
      tabOAuth.classList.toggle('hidden', !hasOAuth);
      tabOAuth.style.display = hasOAuth ? '' : 'none';
    }
  }

  async function loadContext() {
    var embedded = window.__REGISTER_CONTEXT__;
    if (embedded && typeof embedded === 'object') {
      applyContext(embedded);
      return embedded;
    }
    try {
      var res = await fetch(_api('api/auth/register-context'), { credentials: 'include' });
      var data = {};
      try { data = await res.json(); } catch (_) {}
      if (!res.ok) throw new Error(data.error || connFailed);
      applyContext(data);
      return data;
    } catch (ex) {
      showErr(ex.message || connFailed);
      return null;
    }
  }

  async function prepareRegistration() {
    var fields = collectFields();
    var localErr = validateLocalFields(fields);
    if (localErr) {
      showErr(localErr);
      return null;
    }
    hideErr();
    try {
      var res = await fetch(_api('api/members/register/pending'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fields),
        credentials: 'include',
      });
      var data = {};
      try { data = await res.json(); } catch (_) {}
      if (!res.ok) {
        showErr(data.error || connFailed);
        return null;
      }
      registrationToken = data.registration_token || null;
      return registrationToken;
    } catch (_) {
      showErr(connFailed);
      return null;
    }
  }

  async function startOAuthRegistration(providerId) {
    var token = registrationToken || await prepareRegistration();
    if (!token) return;
    var q = new URLSearchParams({
      provider: providerId,
      return_to: _safeNextPath(),
      registration_token: token,
    });
    _safeRedirect(_api('api/members/oauth/authorize?' + q.toString()));
  }

  async function submitLocalRegistration(e) {
    e.preventDefault();
    var fields = collectFields();
    var localErr = validateLocalFields(fields);
    if (localErr) {
      showErr(localErr);
      return;
    }
    hideErr();
    try {
      var res = await fetch(_api('api/members/register/local'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fields),
        credentials: 'include',
      });
      var data = {};
      try { data = await res.json(); } catch (_) {}
      if (!res.ok) {
        showErr(data.error || connFailed);
        return;
      }
      if (errEl) errEl.style.color = '#7cb9ff';
      showErr(L('register_submitted_pending', 'Registration submitted. Sign in after an admin approves your account.'));
      if (form) form.reset();
      registrationToken = null;
    } catch (_) {
      showErr(connFailed);
    }
  }

  async function submitInviteRegistration(e) {
    e.preventDefault();
    var fields = collectFields();
    var localErr = validateLocalFields(fields);
    if (localErr) {
      showErr(localErr);
      return;
    }
    hideErr();
    try {
      var res = await fetch(_api('api/members/register'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fields),
        credentials: 'include',
      });
      var data = {};
      try { data = await res.json(); } catch (_) {}
      if (!res.ok) {
        showErr(data.error || connFailed);
        return;
      }
      _safeRedirect(_safeNextPath());
    } catch (_) {
      showErr(connFailed);
    }
  }

  async function onSubmit(e) {
    e.preventDefault();
    if (activeTab === 'local') {
      await submitLocalRegistration(e);
      return;
    }
    if (activeTab === 'invite') {
      await submitInviteRegistration(e);
      return;
    }
    showErr(L(
      'register_oauth_pick_provider',
      'Enter display name and password, then choose a provider icon.'
    ));
  }

  if (tabLocal) tabLocal.addEventListener('click', function () { setTab('local'); });
  if (tabOAuth) tabOAuth.addEventListener('click', function () { setTab('oauth'); });
  if (tabInvite) tabInvite.addEventListener('click', function () { setTab('invite'); });
  if (form) form.addEventListener('submit', function (e) { void onSubmit(e); });
  if (displayNameInput) {
    displayNameInput.addEventListener('input', function () {
      registrationToken = null;
      scheduleCheck('display_name', displayNameInput.value.trim());
    });
  }

  try {
    var params = new URLSearchParams(window.location.search);
    var oauthErr = params.get('error');
    if (oauthErr) {
      showErr(oauthErr);
      params.delete('error');
      var clean = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
      window.history.replaceState({}, '', clean);
    }
  } catch (_) {}

  void loadContext();
});
