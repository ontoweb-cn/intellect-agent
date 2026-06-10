/**
 * Member account chrome (title bar) and login redirects for multi-user mode.
 * Primary sign-in is /login — not the in-app overlay.
 */

const WEBUI_ACTIVE_TEAM_KEY = 'intellect_webui_active_team';
const WEBUI_ACTIVE_PROJECT_KEY = 'intellect_webui_active_project';

let _memberStatusCache = null;
let _memberStatusCacheTime = 0;

function invalidateMembersStatusCache() {
    _memberStatusCache = null;
    _memberStatusCacheTime = 0;
}

function getWebuiActiveTeamId() {
    try { return localStorage.getItem(WEBUI_ACTIVE_TEAM_KEY) || ''; } catch (e) { return ''; }
}

function setWebuiActiveTeamId(teamId) {
    try {
        if (teamId) localStorage.setItem(WEBUI_ACTIVE_TEAM_KEY, teamId);
        else localStorage.removeItem(WEBUI_ACTIVE_TEAM_KEY);
    } catch (e) { /* ignore */ }
}

function getWebuiActiveProjectId() {
    try { return localStorage.getItem(WEBUI_ACTIVE_PROJECT_KEY) || ''; } catch (e) { return ''; }
}

function setWebuiActiveProjectId(projectId) {
    try {
        if (projectId) localStorage.setItem(WEBUI_ACTIVE_PROJECT_KEY, projectId);
        else localStorage.removeItem(WEBUI_ACTIVE_PROJECT_KEY);
    } catch (e) { /* ignore */ }
}

async function fetchMembersStatus() {
    // Return cached result within 30s TTL to avoid repeated agent DB calls
    if (_memberStatusCache && (Date.now() - _memberStatusCacheTime) < 30000) {
        return _memberStatusCache;
    }
    try {
        const data = await api('/api/members/status');
        if (!data || typeof data !== 'object') {
            _memberStatusCache = null;
            _memberStatusCacheTime = 0;
            return null;
        }
        _memberStatusCache = data;
        _memberStatusCacheTime = Date.now();
        return data;
    } catch (e) {
        _memberStatusCache = null;
        _memberStatusCacheTime = 0;
        return null;
    }
}

function _isLoginPagePath() {
    try {
        return /(^|\/)login(?:\?|$)/.test(window.location.pathname || '');
    } catch (e) {
        return false;
    }
}

function _memberLoginNextAfterSignOut() {
    let nextPath = window.location.pathname + window.location.search;
    if (typeof window.isSessionDeeplinkPath === 'function' && window.isSessionDeeplinkPath(nextPath)) {
        try {
            return new URL('.', document.baseURI || window.location.href).pathname;
        } catch (_) {
            return '/';
        }
    }
    return nextPath;
}

function memberLoginRedirectUrl(message) {
    const url = new URL('login', document.baseURI || window.location.href);
    url.searchParams.set('signed_out', '1');
    url.searchParams.set('next', _memberLoginNextAfterSignOut());
    if (message) url.searchParams.set('error', message);
    return url.href;
}

function redirectToMemberLogin(message) {
    if (_isLoginPagePath()) return;
    window.location.replace(memberLoginRedirectUrl(message || ''));
}

function hideMemberLoginOverlay() {
    const el = document.getElementById('memberLoginOverlay');
    if (el) el.hidden = true;
    document.body.classList.remove('member-login-active');
}

function hideMemberPasswordOverlay() {
    const el = document.getElementById('memberPasswordOverlay');
    if (el) el.hidden = true;
    document.body.classList.remove('member-password-active');
}

function showMemberPasswordOverlay(status) {
    const overlay = document.getElementById('memberPasswordOverlay');
    const title = document.getElementById('memberPasswordTitle');
    const subtitle = document.getElementById('memberPasswordSubtitle');
    const currentInput = document.getElementById('memberPasswordOverlayCurrent');
    const currentLabel = currentInput && currentInput.previousElementSibling;
    const signOutBtn = document.getElementById('memberPasswordOverlaySignOut');
    if (!overlay) return;
    const forced = Boolean(status && status.password_change_required);
    if (title) {
        title.textContent = forced
            ? (t('member_password_required_title') || 'Set your password')
            : (t('member_password_change_title') || 'Change password');
    }
    if (subtitle) {
        subtitle.textContent = forced
            ? (t('member_password_required_sub') || 'Choose a password before continuing.')
            : (t('member_password_change_sub') || 'Update your member sign-in password.');
    }
    const showCurrent = Boolean(status && status.member_has_password);
    if (currentInput) {
        currentInput.classList.toggle('hidden', !showCurrent);
        currentInput.hidden = !showCurrent;
        currentInput.required = showCurrent;
        if (!showCurrent) currentInput.value = '';
    }
    if (currentLabel && currentLabel.tagName === 'LABEL') {
        currentLabel.classList.toggle('hidden', !showCurrent);
        currentLabel.hidden = !showCurrent;
    }
    if (signOutBtn) signOutBtn.classList.toggle('hidden', !forced);
    overlay.hidden = false;
    document.body.classList.add('member-password-active');
    const err = document.getElementById('memberPasswordError');
    if (err) err.hidden = true;
    const newInput = document.getElementById('memberPasswordOverlayNew');
    if (newInput) newInput.focus();
}

function memberPasswordPayloadFromForm(form) {
    if (!form) return null;
    const currentEl = form.querySelector('[data-member-password-current]');
    const newEl = form.querySelector('[data-member-password-new]');
    const confirmEl = form.querySelector('[data-member-password-confirm]');
    const body = {
        new_password: newEl ? String(newEl.value || '') : '',
        new_password_confirm: confirmEl ? String(confirmEl.value || '') : '',
    };
    if (currentEl && !currentEl.hidden && currentEl.offsetParent !== null) {
        body.current_password = String(currentEl.value || '');
    }
    return body;
}

function memberPasswordErrorMessage(err, payload) {
    if (payload && payload.error) return payload.error;
    if (err && err.body) {
        try {
            const parsed = JSON.parse(err.body);
            if (parsed && parsed.error) return parsed.error;
        } catch (_) {}
    }
    if (err && err.message) return err.message;
    return t('member_password_save_failed') || 'Could not save password';
}

async function submitMemberPasswordChange(form, errorEl) {
    const body = memberPasswordPayloadFromForm(form);
    if (!body) return false;
    if (errorEl) errorEl.hidden = true;
    try {
        await api('/api/members/me/password', {
            method: 'POST',
            body: JSON.stringify(body),
        });
        _memberStatusCache = null;
        await fetchMembersStatus();
        return true;
    } catch (err) {
        let payload = null;
        try { payload = err.payload || JSON.parse(err.message); } catch (_) {}
        if (errorEl) {
            errorEl.textContent = memberPasswordErrorMessage(err, payload);
            errorEl.hidden = false;
        } else if (typeof showToast === 'function') {
            showToast(memberPasswordErrorMessage(err, payload));
        }
        return false;
    }
}

async function memberSignOut() {
    if (typeof clearChatUiForSignOut === 'function') clearChatUiForSignOut();
    if (typeof rememberWebuiActorMemberId === 'function') rememberWebuiActorMemberId(null);
    setWebuiActiveTeamId(null);
    setWebuiActiveProjectId(null);
    invalidateMembersStatusCache();
    hideMemberPasswordOverlay();
    // Use raw fetch() to avoid api()'s built-in 401 → redirectToMemberLogin()
    // which would race with the explicit redirect below.
    await Promise.allSettled([
        fetch('/api/members/session', { method: 'DELETE', credentials: 'include' }),
        fetch('/api/auth/logout', { method: 'POST', credentials: 'include',
            headers: { 'Content-Type': 'application/json' }, body: '{}' }),
    ]);
    redirectToMemberLogin();
}

async function setActiveTeam(teamId, opts) {
    opts = opts || {};
    if (!teamId) {
        await api('/api/members/active-team', { method: 'DELETE' });
        setWebuiActiveTeamId(null);
    } else {
        await api('/api/members/active-team', { method: 'POST', body: JSON.stringify({ team_id: teamId }) });
        setWebuiActiveTeamId(teamId);
    }
    await refreshMemberChrome();
    const refresh = typeof refreshChatAfterMemberContextChange === 'function'
        ? refreshChatAfterMemberContextChange
        : (typeof window !== 'undefined' && typeof window.refreshChatAfterMemberContextChange === 'function'
            ? window.refreshChatAfterMemberContextChange
            : null);
    if (refresh) {
        await refresh({ showToast: opts.silent !== true });
    }
}

async function setActiveProject(projectId, opts) {
    opts = opts || {};
    if (!projectId) {
        await api('/api/members/active-project', { method: 'DELETE' });
        setWebuiActiveProjectId(null);
    } else {
        await api('/api/members/active-project', { method: 'POST', body: JSON.stringify({ project_id: projectId }) });
        setWebuiActiveProjectId(projectId);
    }
    await refreshMemberChrome();
    const refresh = typeof refreshChatAfterMemberContextChange === 'function'
        ? refreshChatAfterMemberContextChange
        : (typeof window !== 'undefined' && typeof window.refreshChatAfterMemberContextChange === 'function'
            ? window.refreshChatAfterMemberContextChange
            : null);
    if (refresh) {
        await refresh({ showToast: opts.silent !== true });
    }
}

async function refreshMemberChrome() {
    const status = await fetchMembersStatus();
    hideMemberLoginOverlay();

    if (!status || !status.enabled) {
        hideMemberPasswordOverlay();
        if (typeof refreshUserProfileChrome === 'function') await refreshUserProfileChrome(status);
        return;
    }

    if (status.actor_member_id && typeof rememberWebuiActorMemberId === 'function') {
        const prevActor = localStorage.getItem('intellect_webui_actor_member') || '';
        const actorChanged = Boolean(prevActor && prevActor !== status.actor_member_id);
        rememberWebuiActorMemberId(status.actor_member_id);
        if (actorChanged) {
            invalidateMembersStatusCache();
            _memberStatusCache = status;
            _memberStatusCacheTime = Date.now();
            if (typeof refreshChatAfterMemberContextChange === 'function') {
                await refreshChatAfterMemberContextChange({ showToast: false });
            } else if (typeof renderSessionList === 'function') {
                await renderSessionList();
            }
        }
    }

    if (!status.actor_member_id) {
        hideMemberPasswordOverlay();
        // Defer to refreshUserProfileChrome which checks whether the user
        // already has a valid webui session. Only redirect if they are
        // fully unauthenticated (neither member nor webui session).
        if (typeof refreshUserProfileChrome === 'function') await refreshUserProfileChrome(status);
        return;
    }

    if (status.password_change_required) {
        showMemberPasswordOverlay(status);
    } else {
        hideMemberPasswordOverlay();
    }

    if (typeof refreshUserProfileChrome === 'function') await refreshUserProfileChrome(status);
}

function initMemberPasswordOverlay() {
    const form = document.getElementById('memberPasswordOverlayForm');
    const signOutBtn = document.getElementById('memberPasswordOverlaySignOut');
    if (form && !form.dataset.bound) {
        form.dataset.bound = '1';
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            void (async function () {
                const errEl = document.getElementById('memberPasswordError');
                const ok = await submitMemberPasswordChange(form, errEl);
                if (!ok) return;
                if (typeof showToast === 'function') {
                    showToast(t('member_password_saved') || 'Password saved');
                }
                await refreshMemberChrome();
            })();
        });
    }
    if (signOutBtn && !signOutBtn.dataset.bound) {
        signOutBtn.dataset.bound = '1';
        signOutBtn.addEventListener('click', function () {
            void memberSignOut();
        });
    }
}

async function initMemberAuth() {
    initMemberPasswordOverlay();
    if (typeof initUserProfileMenu === 'function') initUserProfileMenu();
    await refreshMemberChrome();
}

if (typeof window !== 'undefined') {
    window.invalidateMembersStatusCache = invalidateMembersStatusCache;
}
