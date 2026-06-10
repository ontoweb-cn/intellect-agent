/**
 * Title-bar user profile menu — avatar, personal info, teams, auth actions.
 */

let _userProfileCache = null;
let _userProfileDropdownOpen = false;

function userProfileInitials(label) {
    const source = String(label || '').trim();
    if (!source) return '?';
    const parts = source.split(/[\s._@-]+/).filter(Boolean);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return source.slice(0, 2).toUpperCase();
}

function renderUserProfileAvatar(el, profile, sizeClass) {
    if (!el) return;
    el.className = 'user-profile-avatar' + (sizeClass ? ' ' + sizeClass : '');
    el.textContent = '';
    el.style.backgroundImage = '';
    const label = profile.display_name || profile.member_id || 'User';
    if (profile.avatar_url) {
        const img = document.createElement('img');
        img.src = profile.avatar_url + (profile.avatar_url.indexOf('?') >= 0 ? '&' : '?') + 't=' + Date.now();
        img.alt = '';
        el.appendChild(img);
        return;
    }
    el.textContent = userProfileInitials(label);
}

function closeUserProfileDropdown() {
    const dropdown = document.getElementById('userProfileDropdown');
    const trigger = document.getElementById('userProfileTrigger');
    _userProfileDropdownOpen = false;
    if (dropdown) dropdown.hidden = true;
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
}

function toggleUserProfileDropdown() {
    const dropdown = document.getElementById('userProfileDropdown');
    const trigger = document.getElementById('userProfileTrigger');
    if (!dropdown || !trigger) return;
    _userProfileDropdownOpen = !_userProfileDropdownOpen;
    dropdown.hidden = !_userProfileDropdownOpen;
    trigger.setAttribute('aria-expanded', _userProfileDropdownOpen ? 'true' : 'false');
    if (_userProfileDropdownOpen){
      void refreshUserProfileChrome();
      void refreshUserProfileTeams();
      void refreshUserProfileProjects();
    }
}

async function fetchUserProfile() {
    try {
        const data = await api('/api/user/profile');
        _userProfileCache = data;
        return data;
    } catch (e) {
        _userProfileCache = null;
        return null;
    }
}

function updateMemberTeamBanner(status, teamsRes) {
    const banner = document.getElementById('memberTeamBanner');
    if (!banner) return;
    const teamsEnabled = status && status.teams_enabled;
    if (!teamsEnabled || !status || !status.enabled) {
        banner.hidden = true;
        return;
    }
    const activeId = (teamsRes && teamsRes.active_team_id)
        || (status && status.active_team_id)
        || (typeof getWebuiActiveTeamId === 'function' ? getWebuiActiveTeamId() : '');
    const need = status.requires_team_selection && !activeId;
    banner.hidden = !need;
}

function updateMemberProjectBanner(status, projectsRes) {
    const banner = document.getElementById('memberProjectBanner');
    if (!banner) return;
    const projectsEnabled = status && status.projects_enabled;
    if (!projectsEnabled || !status || !status.enabled) {
        banner.hidden = true;
        return;
    }
    const activeId = (projectsRes && projectsRes.active_project_id)
        || (status && status.active_project_id)
        || (typeof getWebuiActiveProjectId === 'function' ? getWebuiActiveProjectId() : '');
    const need = status.requires_project_selection && !activeId;
    banner.hidden = !need;
}

async function refreshUserProfileProjects() {
    const section = document.getElementById('userProfileProjectsSection');
    const list = document.getElementById('userProfileProjectList');
    if (!section || !list) return;

    const status = typeof fetchMembersStatus === 'function' ? await fetchMembersStatus() : null;
    if (!status || !status.enabled || !status.projects_enabled || !status.actor_member_id) {
        section.hidden = true;
        list.innerHTML = '';
        updateMemberProjectBanner(status, null);
        return;
    }

    try {
        const projectsRes = await api('/api/members/me/projects');
        section.hidden = false;
        updateMemberProjectBanner(status, projectsRes);
        const projects = projectsRes.projects || [];
        const activeId = projectsRes.active_project_id
            || (typeof getWebuiActiveProjectId === 'function' ? getWebuiActiveProjectId() : '');
        if (!projects.length) {
            list.innerHTML = '<div class="user-profile-hint">' + esc(t('projects_no_memberships') || 'No project memberships yet.') + '</div>';
            return;
        }
        list.innerHTML = '';
        for (const pm of projects) {
            if (pm.status !== 'active' || pm.project_status === 'archived') continue;
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'user-profile-team-item' + (activeId === pm.id ? ' is-active' : '');
            btn.setAttribute('role', 'menuitem');
            const name = (pm.display_name || pm.id);
            btn.innerHTML = '<span class="user-profile-team-name">' + esc(name) + '</span>' +
                (activeId === pm.id ? '<span class="user-profile-team-check" aria-hidden="true">✓</span>' : '');
            btn.addEventListener('click', function () {
                void (async function () {
                    if (typeof setActiveProject === 'function') await setActiveProject(pm.id);
                    closeUserProfileDropdown();
                    await refreshUserProfileProjects();
                })();
            });
            list.appendChild(btn);
        }
    } catch (e) {
        section.hidden = true;
        list.innerHTML = '';
        updateMemberProjectBanner(status, null);
    }
}

async function refreshUserProfileTeams() {
    const section = document.getElementById('userProfileTeamsSection');
    const list = document.getElementById('userProfileTeamList');
    if (!section || !list) return;

    const status = typeof fetchMembersStatus === 'function' ? await fetchMembersStatus() : null;
    if (!status || !status.enabled || !status.teams_enabled || !status.actor_member_id) {
        section.hidden = true;
        list.innerHTML = '';
        updateMemberTeamBanner(status, null);
        return;
    }

    try {
        const teamsRes = await api('/api/members/me/teams');
        section.hidden = false;
        updateMemberTeamBanner(status, teamsRes);
        const teams = teamsRes.teams || [];
        const activeId = teamsRes.active_team_id || (typeof getWebuiActiveTeamId === 'function' ? getWebuiActiveTeamId() : '');
        if (!teams.length) {
            list.innerHTML = '<div class="user-profile-hint">' + esc(t('teams_no_memberships') || 'No team memberships yet.') + '</div>';
            return;
        }
        list.innerHTML = '';
        for (const tm of teams) {
            if (tm.status !== 'active') continue;
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'user-profile-team-item' + (activeId === tm.id ? ' is-active' : '');
            btn.setAttribute('role', 'menuitem');
            const name = (tm.display_name || tm.id);
            btn.innerHTML = '<span class="user-profile-team-name">' + esc(name) + '</span>' +
                (activeId === tm.id ? '<span class="user-profile-team-check" aria-hidden="true">✓</span>' : '');
            btn.addEventListener('click', function () {
                void (async function () {
                    if (typeof setActiveTeam === 'function') await setActiveTeam(tm.id);
                    closeUserProfileDropdown();
                    await refreshUserProfileTeams();
                })();
            });
            list.appendChild(btn);
        }
    } catch (e) {
        section.hidden = true;
        list.innerHTML = '';
        updateMemberTeamBanner(status, null);
    }
}

function profileFromMembersStatus(status) {
    if (!status || !status.enabled || !status.actor_member_id) return null;
    return {
        mode: 'multi_user',
        member_id: status.actor_member_id,
        display_name: status.actor_display_name || status.actor_member_id,
        has_avatar: Boolean(status.actor_has_avatar),
        avatar_url: status.actor_avatar_url || null,
        auth_enabled: Boolean(status.webui_auth_enabled),
        logged_in: true,
        username_editable: false,
        show_sign_out: true,
        show_disable_auth: false,
        password_env_var: false,
    };
}

function applyUserProfileUi(profile) {
    const menu = document.getElementById('userProfileMenu');
    if (!menu || !profile) {
        if (menu) menu.hidden = true;
        return;
    }

    const _webuiOk = !profile.auth_enabled || profile.logged_in;

    const showMenu = profile.mode === 'legacy'
        || (profile.mode === 'multi_user' && profile.member_id)
        || (profile.mode === 'multi_user' && !profile.member_id && _webuiOk);
    if (profile.mode === 'legacy' && profile.auth_enabled && !profile.logged_in) {
        menu.hidden = true;
        return;
    }
    menu.hidden = !showMenu;

    renderUserProfileAvatar(document.getElementById('userProfileAvatar'), profile);
    renderUserProfileAvatar(document.getElementById('userProfileAvatarLg'), profile, 'user-profile-avatar--lg');
    renderUserProfileAvatar(document.getElementById('userProfileAvatarPreview'), profile, 'user-profile-avatar--md');

    const displayNameEl = document.getElementById('userProfileDisplayName');
    const subEl = document.getElementById('userProfileSub');
    if (displayNameEl) displayNameEl.textContent = profile.display_name || profile.member_id || 'User';
    if (subEl) {
        if (profile.mode === 'multi_user' && profile.member_id) {
            subEl.textContent = profile.member_id;
        } else {
            subEl.textContent = t('profile_local_user') || 'Local user';
        }
    }

    const usernameInput = document.getElementById('userProfileUsername');
    const saveUsernameBtn = document.getElementById('userProfileSaveUsername');
    if (usernameInput) {
        usernameInput.value = profile.mode === 'legacy'
            ? (profile.stored_display_name || profile.display_name || '')
            : (profile.display_name || profile.member_id || '');
        usernameInput.readOnly = !profile.username_editable;
        usernameInput.classList.toggle('is-readonly', !profile.username_editable);
    }
    if (saveUsernameBtn) saveUsernameBtn.hidden = !profile.username_editable;

    const pwBlock = document.getElementById('userProfilePasswordBlock');
    const pwInput = document.getElementById('userProfilePassword');
    const pwLock = document.getElementById('userProfilePasswordEnvLock');
    const savePwBtn = document.getElementById('userProfileSavePassword');
    const showPassword = profile.mode === 'legacy';
    if (pwBlock) pwBlock.hidden = !showPassword;
    if (pwInput) {
        pwInput.value = '';
        pwInput.disabled = !!profile.password_env_var;
    }
    if (pwLock) pwLock.hidden = !profile.password_env_var;
    if (savePwBtn) savePwBtn.disabled = !!profile.password_env_var;

    const disableBtn = document.getElementById('userProfileDisableAuth');
    const signOutBtn = document.getElementById('userProfileSignOut');
    if (disableBtn) disableBtn.hidden = !profile.show_disable_auth;
    if (signOutBtn) signOutBtn.hidden = !profile.show_sign_out;
}

async function refreshUserProfileChrome(prefetchedStatus) {
    const status = prefetchedStatus
        || (typeof fetchMembersStatus === 'function' ? await fetchMembersStatus() : null);

    const optimistic = profileFromMembersStatus(status);
    if (optimistic) applyUserProfileUi(optimistic);

    if (status && typeof _syncMemberTabsVisibility === 'function') {
        _syncMemberTabsVisibility(status);
    }

    const profile = await fetchUserProfile();
    applyUserProfileUi(profile);

    // When members are enabled but no member session exists, only redirect
    // to member login if the user also lacks a valid webui session.
    const _webuiAuthenticated = !profile || !profile.auth_enabled || profile.logged_in;

    if (status && status.enabled && !status.actor_member_id && !_webuiAuthenticated) {
        if (typeof redirectToMemberLogin === 'function') redirectToMemberLogin();
        return;
    }
    if (profile && profile.mode === 'multi_user' && !profile.member_id && !_webuiAuthenticated) {
        if (typeof redirectToMemberLogin === 'function') redirectToMemberLogin();
        return;
    }

    if (status && status.enabled && status.teams_enabled) {
        await refreshUserProfileTeams();
    } else {
        updateMemberTeamBanner(status, null);
        const section = document.getElementById('userProfileTeamsSection');
        if (section) section.hidden = true;
    }

    if (status && status.enabled && status.projects_enabled) {
        await refreshUserProfileProjects();
    } else {
        updateMemberProjectBanner(status, null);
        const projSection = document.getElementById('userProfileProjectsSection');
        if (projSection) projSection.hidden = true;
    }
}

async function saveUserProfileUsername() {
    const input = document.getElementById('userProfileUsername');
    if (!input) return;
    const display_name = (input.value || '').trim();
    if (!display_name) {
        showToast(t('profile_username_required') || 'Username is required');
        return;
    }
    try {
        const saved = await api('/api/user/profile', {
            method: 'POST',
            body: JSON.stringify({ display_name: display_name }),
        });
        _userProfileCache = saved;
        applyUserProfileUi(saved);
        showToast(t('profile_username_saved') || 'Username saved');
    } catch (e) {
        showToast((t('profile_save_failed') || 'Save failed: ') + e.message);
    }
}

async function saveUserProfilePassword() {
    const input = document.getElementById('userProfilePassword');
    if (!input) return;
    const pw = (input.value || '').trim();
    if (!pw) {
        showToast(t('profile_password_required') || 'Enter a password');
        return;
    }
    try {
        await api('/api/settings', {
            method: 'POST',
            body: JSON.stringify({ _set_password: pw }),
        });
        input.value = '';
        showToast(t('settings_saved_pw_updated') || 'Password updated');
        await refreshUserProfileChrome();
    } catch (e) {
        showToast((t('settings_save_failed') || 'Save failed: ') + e.message);
    }
}

async function removeUserProfileAvatar() {
    try {
        const saved = await api('/api/user/profile', {
            method: 'POST',
            body: JSON.stringify({ remove_avatar: true }),
        });
        _userProfileCache = saved;
        applyUserProfileUi(saved);
        showToast(t('profile_avatar_removed') || 'Avatar removed');
    } catch (e) {
        showToast((t('profile_save_failed') || 'Save failed: ') + e.message);
    }
}

async function uploadUserProfileAvatar(file) {
    if (!file) return;
    const allowed = ['image/jpeg', 'image/png', 'image/webp'];
    if (allowed.indexOf(file.type) < 0) {
        showToast(t('profile_avatar_type_error') || 'Use JPEG, PNG, or WebP');
        return;
    }
    if (file.size > 512 * 1024) {
        showToast(t('profile_avatar_size_error') || 'Avatar must be 512KB or smaller');
        return;
    }
    const form = new FormData();
    form.append('file', file, file.name);
    try {
        const res = await fetch('/api/user/profile/avatar', { method: 'POST', body: form, credentials: 'same-origin' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || res.statusText);
        _userProfileCache = data;
        applyUserProfileUi(data);
        showToast(t('profile_avatar_saved') || 'Avatar updated');
    } catch (e) {
        showToast((t('profile_save_failed') || 'Save failed: ') + e.message);
    }
}

async function userProfileSignOut() {
    closeUserProfileDropdown();
    const profile = _userProfileCache || await fetchUserProfile();
    if (profile && profile.mode === 'multi_user') {
        if (typeof memberSignOut === 'function') return memberSignOut();
    }
    if (typeof signOut === 'function') return signOut();
}

async function userProfileDisableAuth() {
    closeUserProfileDropdown();
    if (typeof disableAuth === 'function') return disableAuth();
}

function initUserProfileMenu() {
    const trigger = document.getElementById('userProfileTrigger');
    const dropdown = document.getElementById('userProfileDropdown');
    if (!trigger || !dropdown) return;

    trigger.addEventListener('click', function (e) {
        e.stopPropagation();
        toggleUserProfileDropdown();
    });

    document.addEventListener('click', function (e) {
        if (!_userProfileDropdownOpen) return;
        const menu = document.getElementById('userProfileMenu');
        if (menu && !menu.contains(e.target)) closeUserProfileDropdown();
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeUserProfileDropdown();
    });

    const saveUsernameBtn = document.getElementById('userProfileSaveUsername');
    if (saveUsernameBtn) saveUsernameBtn.addEventListener('click', function () { void saveUserProfileUsername(); });

    const savePwBtn = document.getElementById('userProfileSavePassword');
    if (savePwBtn) savePwBtn.addEventListener('click', function () { void saveUserProfilePassword(); });

    const removeBtn = document.getElementById('userProfileRemoveAvatar');
    if (removeBtn) removeBtn.addEventListener('click', function () { void removeUserProfileAvatar(); });

    const fileInput = document.getElementById('userProfileAvatarInput');
    if (fileInput) {
        fileInput.addEventListener('change', function () {
            const file = fileInput.files && fileInput.files[0];
            fileInput.value = '';
            if (file) void uploadUserProfileAvatar(file);
        });
    }

    const signOutBtn = document.getElementById('userProfileSignOut');
    if (signOutBtn) signOutBtn.addEventListener('click', function () { void userProfileSignOut(); });

    const disableBtn = document.getElementById('userProfileDisableAuth');
    if (disableBtn) disableBtn.addEventListener('click', function () { void userProfileDisableAuth(); });
}
