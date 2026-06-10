/**
 * OAuth provider management panel and login modals.
 *
 * Renders an OAuth section inside the Settings > Providers tab. Supports
 * both PKCE (browser redirect + paste code) and device_code flows.
 */

let _oauthProvidersCache = null;
let _activeOAuthPollTimer = null;

// ── fetch OAuth providers ────────────────────────────────────────────────

async function fetchOAuthProviders() {
    try {
        const data = await api('/api/providers/oauth');
        _oauthProvidersCache = data.providers || [];
        return _oauthProvidersCache;
    } catch (e) {
        console.error('Failed to fetch OAuth providers:', e);
        return [];
    }
}

// ── render OAuth providers panel ─────────────────────────────────────────

function renderOAuthProvidersPanel(container) {
    container.innerHTML = '<div style="padding:12px;text-align:center;font-size:13px;color:var(--muted)">Loading OAuth providers…</div>';
    (async () => {
        const providers = await fetchOAuthProviders();
        if (!providers || providers.length === 0) {
            container.innerHTML = '';
            return;
        }
        container.innerHTML = '';
        for (const p of providers) {
            container.appendChild(_buildOAuthProviderCard(p));
        }
    })().catch(e => {
        container.innerHTML = '<div style="padding:12px;font-size:13px;color:var(--error)">Failed to load OAuth providers.</div>';
    });
}

function _buildOAuthProviderCard(p) {
    const card = document.createElement('div');
    card.className = 'oauth-provider-card';
    const status = p.status || {};
    const loggedIn = status.logged_in === true;
    const isExternal = p.flow === 'external';

    let bodyHtml = '';
    if (loggedIn) {
        if (status.source_label) {
            bodyHtml += '<div class="oauth-detail">Source: ' + esc(status.source_label) + '</div>';
        }
        if (status.token_preview) {
            bodyHtml += '<div class="oauth-detail">Token: <code>' + esc(status.token_preview) + '</code></div>';
        }
    } else if (isExternal) {
        bodyHtml += '<div class="oauth-detail">' + esc(t('oauth_external_hint') || 'Run this command in your terminal:') + '</div>';
        bodyHtml += '<div class="oauth-cli-row"><code>' + esc(p.cli_command) + '</code><button class="sm-btn" onclick="copyToClipboard(' + JSON.stringify(esc(p.cli_command)) + ')" type="button">' + esc(t('copy') || 'Copy') + '</button></div>';
    }

    let actionsHtml = '';
    if (!isExternal && !loggedIn) {
        actionsHtml += '<button class="sm-btn provider-card-btn-primary" onclick="startOAuthLogin(\'' + esc(p.id) + '\',\'' + esc(p.flow) + '\')" type="button">' + esc(t('oauth_login') || 'Login') + '</button>';
    }
    if (loggedIn) {
        actionsHtml += '<button class="sm-btn provider-card-btn-danger" onclick="disconnectOAuthProvider(\'' + esc(p.id) + '\')" type="button">' + esc(t('oauth_disconnect') || 'Disconnect') + '</button>';
    }
    if (p.docs_url) {
        actionsHtml += '<a class="sm-btn" href="' + esc(p.docs_url) + '" target="_blank" rel="noopener">' + esc(t('oauth_docs') || 'Docs') + ' ↗</a>';
    }

    card.innerHTML =
        '<div class="oauth-card-header">' +
          '<span class="oauth-card-name">' + esc(p.name) + '</span>' +
          '<span class="oauth-badge ' + (loggedIn ? 'oauth-connected' : 'oauth-disconnected') + '">' +
            (loggedIn ? (t('oauth_status_connected') || 'Connected') : (t('oauth_status_disconnected') || 'Not connected')) +
          '</span>' +
        '</div>' +
        '<div class="oauth-card-body">' +
          bodyHtml +
          '<div class="oauth-card-actions">' + actionsHtml + '</div>' +
        '</div>';
    return card;
}

// ── OAuth login modal (PKCE + device_code) ───────────────────────────────

async function startOAuthLogin(providerId, flow) {
    try {
        const resp = await api('/api/providers/oauth/' + encodeURIComponent(providerId) + '/start', {
            method: 'POST',
            body: JSON.stringify({}),
        });
        if (resp.error) throw new Error(resp.error);

        if (flow === 'pkce' && resp.auth_url) {
            _renderPKCELoginModal(providerId, resp.session_id, resp.auth_url);
        } else if ((flow === 'device_code' || resp.flow === 'device_code') && resp.user_code) {
            _renderDeviceCodeLoginModal(providerId, resp);
        }
    } catch (e) {
        showToast(e.message || 'Failed to start OAuth login', 'error');
    }
}

function _renderPKCELoginModal(providerId, sessionId, authUrl) {
    _closeOAuthModal();
    const overlay = document.createElement('div');
    overlay.className = 'oauth-modal-overlay';
    overlay.id = 'oauthLoginModal';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.onclick = function (e) { if (e.target === overlay) _closeOAuthModal(); };

    overlay.innerHTML =
        '<div class="oauth-modal">' +
          '<div class="oauth-modal-header">' +
            '<h2>' + esc(t('oauth_login_title') || 'OAuth Login') + '</h2>' +
            '<button class="oauth-modal-close" onclick="_closeOAuthModal()" type="button" aria-label="Close">&times;</button>' +
          '</div>' +
          '<div class="oauth-modal-body">' +
            '<p>' + esc(t('oauth_pkce_step1') || '1. Open the authorization page in a new tab') + '</p>' +
            '<a class="sm-btn provider-card-btn-primary" href="' + esc(authUrl) + '" target="_blank" rel="noopener">' +
              esc(t('oauth_open_auth_page') || 'Open Authorization Page') + ' ↗</a>' +
            '<p style="margin-top:16px">' + esc(t('oauth_pkce_step2') || '2. After authorizing, paste the code from the callback URL below') + '</p>' +
            '<div class="oauth-modal-input-row">' +
              '<input type="text" id="oauthPkceCodeInput" placeholder="' + esc(t('oauth_paste_code') || 'Paste code here...') + '" class="provider-card-input" style="flex:1" autocomplete="off">' +
              '<button class="sm-btn provider-card-btn-primary" onclick="_submitPKCECode(\'' + esc(providerId) + '\',\'' + esc(sessionId) + '\')" type="button">' +
                esc(t('oauth_submit') || 'Submit') +
              '</button>' +
            '</div>' +
            '<div id="oauthPkceStatus" style="margin-top:12px;font-size:13px"></div>' +
          '</div>' +
        '</div>';
    document.body.appendChild(overlay);
}

async function _submitPKCECode(providerId, sessionId) {
    const input = document.getElementById('oauthPkceCodeInput');
    const statusDiv = document.getElementById('oauthPkceStatus');
    const code = (input && input.value || '').trim();
    if (!code) {
        if (statusDiv) statusDiv.innerHTML = '<span style="color:var(--error)">Please paste a code.</span>';
        return;
    }

    if (statusDiv) statusDiv.innerHTML = '<span>Exchanging code for tokens…</span>';
    try {
        const resp = await api('/api/providers/oauth/' + encodeURIComponent(providerId) + '/submit', {
            method: 'POST',
            body: JSON.stringify({ session_id: sessionId, code: code }),
        });
        if (resp && resp.ok) {
            if (statusDiv) statusDiv.innerHTML = '<span style="color:var(--accent)">Login successful!</span>';
            setTimeout(() => _closeOAuthModal(), 1200);
            refreshOAuthProvidersPanel();
            showToast(t('oauth_login_success') || 'OAuth login successful', 'success');
        } else {
            if (statusDiv) statusDiv.innerHTML = '<span style="color:var(--error)">' + esc((resp && resp.message) || 'Login failed') + '</span>';
        }
    } catch (e) {
        if (statusDiv) statusDiv.innerHTML = '<span style="color:var(--error)">' + esc(e.message || 'Error') + '</span>';
    }
}

function _renderDeviceCodeLoginModal(providerId, resp) {
    _closeOAuthModal();
    const { session_id, user_code, verification_url, poll_interval } = resp;

    const overlay = document.createElement('div');
    overlay.className = 'oauth-modal-overlay';
    overlay.id = 'oauthLoginModal';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.onclick = function (e) { if (e.target === overlay) _closeOAuthModal(); };

    overlay.innerHTML =
        '<div class="oauth-modal">' +
          '<div class="oauth-modal-header">' +
            '<h2>' + esc(t('oauth_login_title') || 'Device Code Login') + '</h2>' +
            '<button class="oauth-modal-close" onclick="_closeOAuthModal()" type="button" aria-label="Close">&times;</button>' +
          '</div>' +
          '<div class="oauth-modal-body">' +
            '<p>' + esc(t('oauth_device_step1') || '1. Visit this URL') + '</p>' +
            '<a class="sm-btn provider-card-btn-primary" href="' + esc(verification_url) + '" target="_blank" rel="noopener">' +
              esc(verification_url || '') +
            '</a>' +
            '<p style="margin-top:16px"><strong>' + esc(t('oauth_device_step2') || '2. Enter this code on the page') + '</strong></p>' +
            '<div class="oauth-device-code">' +
              '<code style="font-size:24px;letter-spacing:0.1em;user-select:all">' + esc(user_code) + '</code>' +
              '<button class="sm-btn" onclick="copyToClipboard(' + JSON.stringify(esc(user_code)) + ')" type="button">' +
                esc(t('copy') || 'Copy') +
              '</button>' +
            '</div>' +
            '<div id="oauthDeviceStatus" style="margin-top:16px;font-size:13px;color:var(--muted)">' +
              esc(t('oauth_device_polling') || 'Waiting for authorization...') +
            '</div>' +
          '</div>' +
        '</div>';
    document.body.appendChild(overlay);

    _pollDeviceCodeSession(providerId, session_id, poll_interval || 3);
}

async function _pollDeviceCodeSession(providerId, sessionId, interval) {
    const statusDiv = document.getElementById('oauthDeviceStatus');
    if (!statusDiv) return;

    try {
        const resp = await api('/api/providers/oauth/' + encodeURIComponent(providerId) + '/poll/' + encodeURIComponent(sessionId));
        const status = (resp && resp.status) || 'error';

        if (status === 'pending') {
            _activeOAuthPollTimer = setTimeout(function () {
                _pollDeviceCodeSession(providerId, sessionId, interval);
            }, Math.max(1000, interval * 1000));
            return;
        }

        if (status === 'approved') {
            statusDiv.innerHTML = '<span style="color:var(--accent)">Login successful!</span>';
            setTimeout(() => _closeOAuthModal(), 1200);
            refreshOAuthProvidersPanel();
            showToast(t('oauth_login_success') || 'OAuth login successful', 'success');
        } else if (status === 'expired') {
            statusDiv.innerHTML = '<span style="color:var(--accent-text)">Code expired. Start a new login.</span>';
        } else {
            statusDiv.innerHTML = '<span style="color:var(--error)">' + esc((resp && resp.error_message) || 'Login failed: ' + status) + '</span>';
        }
    } catch (e) {
        statusDiv.innerHTML = '<span style="color:var(--error)">' + esc(e.message || 'Polling error') + '</span>';
    }
}

function _closeOAuthModal() {
    if (_activeOAuthPollTimer) {
        clearTimeout(_activeOAuthPollTimer);
        _activeOAuthPollTimer = null;
    }
    const modal = document.getElementById('oauthLoginModal');
    if (modal) modal.remove();
}

// ── disconnect ────────────────────────────────────────────────────────────

async function disconnectOAuthProvider(providerId) {
    if (!confirm(esc(t('oauth_disconnect_confirm') || 'Disconnect this OAuth provider? This will remove stored credentials.'))) return;
    try {
        const resp = await api('/api/providers/oauth/' + encodeURIComponent(providerId), { method: 'DELETE' });
        if (resp && resp.ok) {
            showToast(t('oauth_disconnected') || 'OAuth provider disconnected', 'success');
            refreshOAuthProvidersPanel();
        } else {
            showToast((resp && resp.error) || t('oauth_disconnect_failed') || 'Failed to disconnect', 'error');
        }
    } catch (e) {
        showToast(e.message || 'Error', 'error');
    }
}

// ── helpers ───────────────────────────────────────────────────────────────

function refreshOAuthProvidersPanel() {
    const container = document.querySelector('.oauth-providers-panel');
    if (container) renderOAuthProvidersPanel(container);
    // Also refresh the main providers list so OAuth badges update
    if (typeof loadProvidersPanel === 'function') {
        try { loadProvidersPanel(); } catch (e) {}
    }
}

function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
            showToast(t('copied') || 'Copied to clipboard');
        }).catch(function () {
            showToast(text);
        });
    } else {
        showToast(text);
    }
}
