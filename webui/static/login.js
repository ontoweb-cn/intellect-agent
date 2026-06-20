/* Login page — legacy profile (WebUI password) vs multi_user (tabbed member sign-in).
   lgtm[js/xss]: innerHTML content from server-generated I18N/HTML, not user input.
   lgtm[js/client-side-unvalidated-url-redirection]: redirects to server-controlled paths. */
document.addEventListener('DOMContentLoaded', function () {
  var I18N = window.__LOGIN_I18N__ || {};
  function L(key, fallback) {
    var v = I18N[key];
    return (v && String(v)) || fallback || '';
  }

  var errEl = document.getElementById('err');
  var singleEl = document.getElementById('login-single');
  var multiEl = document.getElementById('login-multi');
  var continueEl = document.getElementById('login-continue');
  var continueText = document.getElementById('login-continue-text');
  var continueBtn = document.getElementById('login-continue-btn');
  var form = document.getElementById('login-form');
  var pwInput = document.getElementById('pw');
  var localForm = document.getElementById('login-local-form');
  var oauthHost = document.getElementById('login-oauth-providers');
  var memberForm = document.getElementById('login-member-form');
  var memberIdInput = document.getElementById('login-member-id');
  var memberPwInput = document.getElementById('login-member-pw');
  var localMemberIdInput = document.getElementById('login-local-member-id');
  var localPwInput = document.getElementById('login-local-pw');
  var tabOAuth = document.getElementById('login-tab-oauth');
  var tabPassword = document.getElementById('login-tab-password');
  var tabLocal = document.getElementById('login-tab-local');
  var panelOAuth = document.getElementById('login-panel-oauth');
  var panelPassword = document.getElementById('login-panel-password');
  var panelLocal = document.getElementById('login-panel-local');

  var invalidPw = (form && form.getAttribute('data-invalid-pw')) || L('invalid_pw', 'Invalid password');
  var connFailed = (form && form.getAttribute('data-conn-failed')) || L('conn_failed', 'Connection failed');
  var loginSignedOut = false;
  var activeTab = 'password';
  var localDevAvailable = false;

  function _loginApi(path) {
    var p = String(path || '');
    if (/^https?:\/\//i.test(p)) return p;
    if (p.charAt(0) !== '/') p = '/' + p;
    return new URL(p, window.location.origin).href;
  }

  function showErr(msg) {
    if (!errEl) return;
    errEl.textContent = msg;
    errEl.style.display = 'block';
  }

  function hideErr() {
    if (errEl) errEl.style.display = 'none';
  }

  function _homePath() {
    return document.baseURI ? new URL('.', document.baseURI).pathname : '/';
  }

  function _safeNextPath() {
    try {
      var raw = new URL(window.location.href).searchParams.get('next');
      if (!raw) return _homePath();
      if (raw.charAt(0) !== '/') return _homePath();
      if (raw.charAt(1) === '/' || raw.charAt(1) === '\\') return _homePath();
      if (/[\x00-\x1f\x7f\s]/.test(raw)) return _homePath();
      // After sign-out, never resume another member's /session/<id> deeplink.
      if (loginSignedOut && _isSessionDeeplinkPath(raw)) return _homePath();
      return raw;
    } catch (_) {
      return '/';
    }
  }

  function _isSessionDeeplinkPath(path) {
    if (!path || path.indexOf('/session/') !== 0) return false;
    var tail = path.slice('/session/'.length).split('?')[0].replace(/^\/+|\/+$/g, '');
    if (!tail || tail === 'login' || tail === 'manifest.json' || tail === 'manifest.webmanifest') return false;
    if (tail.indexOf('static/') === 0) return false;
    return tail.indexOf('/') === -1;
  }

  function _clientIsLocalhost() {
    var h = window.location.hostname || '';
    return h === 'localhost' || h === '127.0.0.1' || h === '[::1]';
  }

  function _memberOAuthUrl(providerId) {
    var q = new URLSearchParams({
      provider: providerId,
      return_to: _safeNextPath(),
    });
    return _loginApi('api/members/oauth/authorize?' + q.toString());
  }

  function renderOAuth(providers) {
    if (!oauthHost) return;
    if (typeof renderMemberOAuthProviders !== 'function') {
      oauthHost.innerHTML = '';
      var list = Array.isArray(providers) ? providers : [];
      if (!list.length) return;
      var grid = document.createElement('div');
      grid.className = 'oauth-provider-grid';
      for (var i = 0; i < list.length; i++) {
        var prov = list[i];
        if (!prov || !prov.id) continue;
        var a = document.createElement('a');
        a.className = 'oauth-provider-btn';
        a.href = _memberOAuthUrl(prov.id);
        a.setAttribute('aria-label', prov.display_name || prov.id);
        a.textContent = prov.display_name || prov.id;
        grid.appendChild(a);
      }
      oauthHost.appendChild(grid);
      return;
    }
    renderMemberOAuthProviders(oauthHost, providers, {
      mode: 'login',
      i18n: I18N,
      authorizeUrl: _memberOAuthUrl,
      onSelect: function (providerId) {
        window.location.assign(_memberOAuthUrl(providerId));
      },
    });
  }

  function syncOAuthTabVisibility(providers) {
    var list = providers || [];
    var hasOAuth = list.length > 0;
    if (tabOAuth) {
      tabOAuth.classList.toggle('hidden', !hasOAuth);
      tabOAuth.style.display = hasOAuth ? '' : 'none';
    }
    if (!hasOAuth && activeTab === 'oauth') activeTab = 'password';
  }

  function setTab(tab) {
    if (tab === 'local' && !localDevAvailable) tab = 'password';
    if (tab !== 'oauth' && tab !== 'password' && tab !== 'local') tab = 'password';
    activeTab = tab;
    if (tabOAuth) tabOAuth.classList.toggle('active', tab === 'oauth');
    if (tabPassword) tabPassword.classList.toggle('active', tab === 'password');
    if (tabLocal) tabLocal.classList.toggle('active', tab === 'local');
    if (panelOAuth) panelOAuth.classList.toggle('hidden', tab !== 'oauth');
    if (panelPassword) panelPassword.classList.toggle('hidden', tab !== 'password');
    if (panelLocal) panelLocal.classList.toggle('hidden', tab !== 'local');
    if (tab === 'password' && memberIdInput) memberIdInput.focus();
    if (tab === 'local' && localMemberIdInput) localMemberIdInput.focus();
  }

  function showContinue(ctx) {
    if (!continueEl) return;
    var id = ctx.actor_member_id || '';
    if (continueText) {
      continueText.textContent = L('signed_in_as', 'Signed in as {id}').replace('{id}', id);
    }
    continueEl.classList.remove('hidden');
    if (singleEl) singleEl.classList.add('hidden');
    if (multiEl) multiEl.classList.add('hidden');
    if (continueBtn) {
      continueBtn.onclick = function () {
        window.location.href = _safeNextPath();
      };
    }
  }

  function applyContext(ctx) {
    hideErr();
    window.__LOGIN_CONTEXT__ = ctx;
    if (!ctx) {
      showErr(connFailed);
      return;
    }
    if (ctx.login_complete && !loginSignedOut) {
      showContinue(ctx);
      return;
    }
    if (continueEl) continueEl.classList.add('hidden');

    if (ctx.mode === 'legacy') {
      if (multiEl) multiEl.classList.add('hidden');
      if (ctx.members && ctx.members.config_enabled && ctx.agent_available === false) {
        showErr(L('members_agent_unavailable', 'Members are enabled in config.yaml but intellect-agent is unavailable. Restart WebUI with intellect-agent on PYTHONPATH.'));
      }
      if (!ctx.webui_auth_enabled) {
        if (!loginSignedOut && ctx.login_complete) {
          var nextPath = _safeNextPath();
          if (!_isSessionDeeplinkPath(nextPath)) {
            window.location.href = nextPath;
            return;
          }
        }
      }
      if (singleEl) singleEl.classList.remove('hidden');
      if (pwInput) pwInput.focus();
      return;
    }

    if (singleEl) singleEl.classList.add('hidden');
    if (multiEl) multiEl.classList.remove('hidden');
    var members = ctx.members || {};
    if (members.oauth_host_mismatch && members.oauth_canonical_origin) {
      try {
        if (window.location.origin !== members.oauth_canonical_origin) {
          window.location.replace(members.oauth_canonical_origin + window.location.pathname + window.location.search);
          return;
        }
      } catch (_) {}
    }
    var providers = members.oauth_providers || [];
    syncOAuthTabVisibility(providers);
    if (providers.length > 0 && (members.oauth_required || activeTab === 'oauth')) {
      activeTab = 'oauth';
    }
    renderOAuth(providers);

    // In multi-user mode, hide local dev tab — all members log in via password or OAuth
    localDevAvailable = false;
    if (tabLocal) tabLocal.classList.add('hidden');

    if (activeTab === 'local' && !localDevAvailable) activeTab = 'password';
    setTab(activeTab);
  }

  async function loadContext() {
    try {
      loginSignedOut = new URL(window.location.href).searchParams.has('signed_out');
    } catch (_) {
      loginSignedOut = false;
    }
    // Always prefer the server-embedded context — it was computed during
    // page render and is fresh.  When signed_out=1 we still use it but
    // applyContext() skips showContinue() because loginSignedOut is true.
    var embedded = window.__LOGIN_CONTEXT__;
    if (embedded && typeof embedded === 'object') {
      applyContext(embedded);
      return embedded;
    }
    try {
      var res = await fetch(_loginApi('api/auth/login-context'), { credentials: 'include' });
      var data = {};
      try { data = await res.json(); } catch (_) {}
      if (!res.ok) {
        if (res.status === 404) {
          applyContext({
            mode: 'legacy',
            webui_auth_enabled: true,
            login_complete: false,
            members: { enabled: false },
          });
          return null;
        }
        throw new Error(data.error || connFailed);
      }
      applyContext(data);
      return data;
    } catch (ex) {
      showErr(ex.message || connFailed);
      if (form && singleEl) {
        singleEl.classList.remove('hidden');
      }
      return null;
    }
  }

  async function doSingleLogin(e) {
    e.preventDefault();
    if (!pwInput) return;
    hideErr();
    try {
      var res = await fetch(_loginApi('api/auth/login'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: pwInput.value }),
        credentials: 'include',
      });
      var data = {};
      try { data = await res.json(); } catch (_) {}
      if (res.ok && data.ok) {
        window.location.href = _safeNextPath();
      } else {
        showErr(data.error || invalidPw);
      }
    } catch (ex) {
      showErr(connFailed);
    }
  }

  async function doMemberLogin(e) {
    e.preventDefault();
    hideErr();
    var mid = memberIdInput ? memberIdInput.value.trim() : '';
    var password = memberPwInput ? memberPwInput.value : '';
    if (!mid) {
      showErr(L('member_id_required', 'Enter a member id'));
      return;
    }
    if (!password) {
      showErr(L('invalid_pw', invalidPw));
      return;
    }
    try {
      var res = await fetch(_loginApi('api/members/login'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ member_id: mid, password: password }),
        credentials: 'include',
      });
      var data = {};
      try { data = await res.json(); } catch (_) {}
      if (!res.ok) {
        if (data.error_code === 'pending_approval') {
          showErr(L('login_pending_approval', data.error || 'Your account is pending admin approval.'));
        } else if (data.error_code === 'oauth_host_mismatch' && data.oauth_canonical_origin) {
          showErr(data.error || L('oauth_host_mismatch_login', 'Use the OAuth callback host to sign in.'));
          window.location.href = data.oauth_canonical_origin + '/login';
        } else {
          showErr(data.error || invalidPw);
        }
        return;
      }
      if (typeof rememberWebuiActorMemberId === 'function') rememberWebuiActorMemberId(mid);
      if (typeof window.invalidateMembersStatusCache === 'function') window.invalidateMembersStatusCache();
      window.location.href = _safeNextPath();
    } catch (ex) {
      showErr(connFailed);
    }
  }

  async function doLocalDevLogin(e) {
    e.preventDefault();
    hideErr();
    var mid = localMemberIdInput ? localMemberIdInput.value.trim() : '';
    if (!mid) {
      showErr(L('member_id_required', 'Enter a member id'));
      return;
    }
    var ctx = window.__LOGIN_CONTEXT__ || null;
    if (!ctx) {
      try {
        var res = await fetch(_loginApi('api/auth/login-context'), { credentials: 'include' });
        ctx = await res.json();
      } catch (_) {
        ctx = null;
      }
    }
    var needPw = ctx && ctx.webui_auth_enabled;
    var password = localPwInput ? localPwInput.value : '';
    if (needPw && !password) {
      showErr(L('invalid_pw', invalidPw));
      return;
    }
    try {
      if (needPw) {
        var res = await fetch(_loginApi('api/auth/login'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: password, member_id: mid }),
          credentials: 'include',
        });
        var data = {};
        try { data = await res.json(); } catch (_) {}
        if (!res.ok || !data.ok) {
          showErr(data.error || invalidPw);
          return;
        }
      } else {
        var res2 = await fetch(_loginApi('api/members/session'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ member_id: mid }),
          credentials: 'include',
        });
        var data2 = {};
        try { data2 = await res2.json(); } catch (_) {}
        if (!res2.ok) {
          showErr(data2.error || connFailed);
          return;
        }
      }
      if (typeof rememberWebuiActorMemberId === 'function') rememberWebuiActorMemberId(mid);
      if (typeof window.invalidateMembersStatusCache === 'function') window.invalidateMembersStatusCache();
      window.location.href = _safeNextPath();
    } catch (ex) {
      showErr(connFailed);
    }
  }

  if (tabOAuth) tabOAuth.addEventListener('click', function () { hideErr(); setTab('oauth'); });
  if (tabPassword) tabPassword.addEventListener('click', function () { hideErr(); setTab('password'); });
  if (tabLocal) tabLocal.addEventListener('click', function () { hideErr(); setTab('local'); });
  if (form) form.addEventListener('submit', doSingleLogin);
  if (memberForm) memberForm.addEventListener('submit', doMemberLogin);
  if (localForm) localForm.addEventListener('submit', doLocalDevLogin);

  try {
    var params = new URLSearchParams(window.location.search);
    var oauthErr = params.get('error');
    if (oauthErr) {
      showErr(oauthErr);
      activeTab = 'oauth';
      params.delete('error');
      var clean = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
      window.history.replaceState({}, '', clean);
    }
  } catch (_) {}

  void loadContext();

  (function checkConnectivity() {
    var retryTimer = null;
    function setDisabled(disabled) {
      [pwInput, memberIdInput, memberPwInput, localMemberIdInput, localPwInput].forEach(function (el) {
        if (el) el.disabled = disabled;
      });
      [form, memberForm, localForm, continueBtn].forEach(function (el) {
        if (el) el.disabled = disabled;
      });
    }
    function probe() {
      fetch(_loginApi('health'), { method: 'GET', credentials: 'same-origin' })
        .then(function (r) {
          if (r.ok) {
            if (retryTimer !== null) {
              clearInterval(retryTimer);
              retryTimer = null;
              window.location.reload();
            }
          } else {
            showErr(connFailed + ' (server error ' + r.status + ')');
          }
        })
        .catch(function () {
          showErr('Cannot reach server — check your VPN / Tailscale connection.');
          setDisabled(true);
          if (retryTimer === null) retryTimer = setInterval(probe, 3000);
        });
    }
    probe();
  })();
});
