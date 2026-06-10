/**
 * Teams panel — side menu + main panes (mirrors members.js layout).
 */

var _teamsCurrentSection = 'mine';
var _teamsStatus = null;
var _teamsMe = null;

const TEAMS_SECTIONS = [
    { key: 'mine', icon: 'users', labelKey: 'teams_section_mine', group: 'mine' },
    {
        key: 'manage',
        icon: 'building-2',
        labelKey: 'teams_section_manage',
        group: 'manage',
        requireAnyCap: ['can_create_team', 'can_archive_team', 'can_manage_team_memberships'],
    },
    {
        key: 'approvals',
        icon: 'user-check',
        labelKey: 'teams_section_approvals',
        group: 'manage',
        requireCap: 'can_manage_team_memberships',
    },
];

const TEAMS_GROUP_LABELS = {
    mine: 'teams_group_mine',
    manage: 'teams_group_manage',
};

function teamsPaneDomId(key) {
    return 'teamsPane' + key.charAt(0).toUpperCase() + key.slice(1);
}

function teamsSectionVisible(section, caps) {
    if (section.requireCap) return !!caps[section.requireCap];
    if (section.requireAnyCap) {
        return section.requireAnyCap.some(function (k) { return !!caps[k]; });
    }
    return true;
}

function teamsVisibleSections(caps) {
    return TEAMS_SECTIONS.filter(function (s) { return teamsSectionVisible(s, caps); });
}

function teamsEnsureMainPanes() {
    var main = document.getElementById('mainTeams');
    if (!main) return;
    if (main.querySelector('.teams-pane')) return;
    main.innerHTML =
        '<div class="teams-pane active" id="teamsPaneMine"></div>'
        + '<div class="teams-pane" id="teamsPaneManage"></div>'
        + '<div class="teams-pane" id="teamsPaneApprovals"></div>';
}

function teamsShowDisabledInMain(message) {
    var main = document.getElementById('mainTeams');
    if (!main) return;
    main.innerHTML = '<div class="teams-pane active"><p class="panel-empty" style="padding:20px">' + esc(message) + '</p></div>';
}

function teamsResetMainPanes() {
    var main = document.getElementById('mainTeams');
    if (!main) return;
    main.innerHTML =
        '<div class="teams-pane active" id="teamsPaneMine"></div>'
        + '<div class="teams-pane" id="teamsPaneManage"></div>'
        + '<div class="teams-pane" id="teamsPaneApprovals"></div>';
}

async function loadTeamsPanel() {
    var menu = document.getElementById('teamsSideMenu');
    if (!menu) return;

    var status = typeof fetchMembersStatus === 'function' ? await fetchMembersStatus() : null;
    _teamsStatus = status;
    _teamsMe = null;

    if (!status || !status.enabled) {
        menu.innerHTML = '';
        teamsShowDisabledInMain(t('teams_disabled_members') || 'Enable members in config.yaml first.');
        return;
    }
    if (!status.teams_enabled) {
        menu.innerHTML = '';
        teamsShowDisabledInMain(t('teams_disabled_teams') || 'Enable members.teams in config.yaml.');
        return;
    }
    if (!status.actor_member_id) {
        menu.innerHTML = '';
        teamsShowDisabledInMain(t('teams_sign_in_first') || 'Sign in to manage teams.');
        return;
    }

    teamsResetMainPanes();
    teamsEnsureMainPanes();

    var caps = status.capabilities || {};
    var visible = teamsVisibleSections(caps);
    if (!visible.some(function (s) { return s.key === _teamsCurrentSection; })) {
        _teamsCurrentSection = 'mine';
    }

    var menuHtml = '';
    var lastGroup = '';

    for (var gi = 0; gi < TEAMS_SECTIONS.length; gi++) {
        var section = TEAMS_SECTIONS[gi];
        if (!teamsSectionVisible(section, caps)) continue;

        if (section.group !== lastGroup) {
            var groupKey = TEAMS_GROUP_LABELS[section.group];
            if (groupKey) {
                menuHtml += '<div class="side-menu-group-title">' + esc(t(groupKey) || section.group) + '</div>';
            }
            lastGroup = section.group;
        }

        menuHtml += '<button type="button" class="side-menu-item'
            + (_teamsCurrentSection === section.key ? ' active' : '')
            + '" data-teams-section="' + section.key + '" onclick="switchTeamsSection(\'' + section.key + '\')">'
            + (typeof li === 'function' ? li(section.icon, 16) : '')
            + '<span>' + esc(t(section.labelKey) || section.key) + '</span></button>';
    }

    menu.innerHTML = menuHtml;
    switchTeamsSection(_teamsCurrentSection);
}

function switchTeamsSection(name) {
    _teamsCurrentSection = name;

    teamsEnsureMainPanes();

    var menu = document.getElementById('teamsSideMenu');
    if (menu) {
        menu.querySelectorAll('.side-menu-item').forEach(function (el) {
            el.classList.toggle('active', el.dataset.teamsSection === name);
        });
    }

    var panes = document.querySelectorAll('#mainTeams .teams-pane');
    for (var i = 0; i < panes.length; i++) {
        panes[i].classList.toggle('active', panes[i].id === teamsPaneDomId(name));
    }

    var pane = document.getElementById(teamsPaneDomId(name));
    if (!pane) return;
    pane.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px">' + esc(t('loading') || 'Loading…') + '</div>';

    switch (name) {
        case 'mine':
            void renderTeamsPaneMine(pane);
            break;
        case 'manage':
            void renderTeamsPaneManage(pane);
            break;
        case 'approvals':
            void renderTeamsPaneApprovals(pane);
            break;
    }
}

async function teamsFetchMe() {
    if (_teamsMe) return _teamsMe;
    _teamsMe = await api('/api/members/me/teams');
    return _teamsMe;
}

function teamsInvalidateMe() {
    _teamsMe = null;
}

async function renderTeamsPaneMine(pane) {
    var status = _teamsStatus;
    if (!status || !status.actor_member_id) {
        pane.innerHTML = '<p class="panel-empty">' + esc(t('teams_sign_in_first') || 'Sign in to manage teams.') + '</p>';
        return;
    }

    var me;
    try {
        me = await teamsFetchMe();
    } catch (e) {
        pane.innerHTML = '<p class="members-error">' + esc(e.message) + '</p>';
        return;
    }

    var teams = me.teams || [];
    var html = '<p class="members-hint" style="margin-top:0">' + esc(t('teams_panel_desc') || '') + '</p>';

    if (me.requires_team_selection && !me.active_team_id && typeof getWebuiActiveTeamId === 'function' && !getWebuiActiveTeamId()) {
        html += '<div class="teams-banner">' + esc(t('teams_multi_banner') || 'Select an active team from the account menu.') + '</div>';
    }

    html += '<section class="members-section"><h3 class="members-section-title">' + esc(t('teams_my_teams') || 'My teams') + '</h3>';
    if (!teams.length) {
        html += '<p class="members-hint">' + esc(t('teams_no_memberships') || 'No team memberships yet.') + '</p>';
    } else {
        html += '<ul class="teams-action-list">';
        for (var i = 0; i < teams.length; i++) {
            var tm = teams[i];
            var active = (me.active_team_id || (typeof getWebuiActiveTeamId === 'function' ? getWebuiActiveTeamId() : '')) === tm.id;
            html += '<li class="teams-action-row">';
            html += '<div><span class="font-mono">' + esc(tm.id) + '</span>';
            if (tm.display_name) html += ' <span class="teams-muted">— ' + esc(tm.display_name) + '</span>';
            if (tm.role === 'admin') {
                html += ' <span class="oauth-badge oauth-connected">' + esc(t('teams_role_admin') || 'Team admin') + '</span>';
            }
            html += ' <span class="oauth-badge ' + (tm.status === 'active' ? 'oauth-connected' : 'oauth-disconnected') + '">' + esc(tm.status) + '</span></div>';
            if (tm.status === 'active') {
                html += '<button type="button" class="sm-btn' + (active ? ' provider-card-btn-primary' : '') + '" data-team-use="' + esc(tm.id) + '">'
                    + esc(active ? (t('teams_active') || 'Active') : (t('teams_use') || 'Use')) + '</button>';
                html += ' <button type="button" class="sm-btn" data-team-leave="' + esc(tm.id) + '">'
                    + esc(t('teams_leave') || 'Leave') + '</button>';
            }
            html += '</li>';
        }
        html += '</ul>';
    }
    html += '</section>';

    html += '<section class="members-section"><h3 class="members-section-title">' + esc(t('teams_join') || 'Join a team') + '</h3>';
    html += '<div class="members-token-row"><input type="text" id="teamsJoinId" class="input" placeholder="' + esc(t('members_team_id') || 'team id') + '">';
    html += '<button type="button" class="sm-btn" onclick="teamsJoinTeam()">' + esc(t('members_join_team') || 'Request join') + '</button></div></section>';

    pane.innerHTML = html;

    pane.querySelectorAll('button[data-team-use]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var teamId = btn.getAttribute('data-team-use');
            if (teamId) void teamsUseTeam(teamId);
        });
    });
    pane.querySelectorAll('button[data-team-leave]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var teamId = btn.getAttribute('data-team-leave');
            if (teamId) void teamsLeaveTeam(teamId);
        });
    });
}

async function renderTeamsPaneManage(pane) {
    var status = _teamsStatus;
    var caps = (status && status.capabilities) || {};
    var html = '';

    if (caps.can_create_team) {
        html += '<section class="members-section"><h3 class="members-section-title">' + esc(t('teams_create') || 'Create team') + '</h3>';
        html += '<div class="members-token-row"><input type="text" id="teamsNewId" class="input" placeholder="' + esc(t('members_team_id') || 'team id') + '">';
        html += '<input type="text" id="teamsNewName" class="input" placeholder="' + esc(t('teams_display_name') || 'Display name') + '">';
        html += '<button type="button" class="sm-btn" onclick="teamsCreateTeam()">' + esc(t('create') || 'Create') + '</button></div></section>';
    }

    if (caps.can_archive_team || caps.can_manage_team_memberships) {
        html += '<section class="members-section"><h3 class="members-section-title">' + esc(t('teams_tools') || 'Team tools') + '</h3>';
        html += '<div class="members-token-row"><input type="text" id="teamsToolId" class="input" placeholder="' + esc(t('members_team_id') || 'team id') + '">';
        if (caps.can_manage_team_memberships) {
            html += '<button type="button" class="sm-btn" onclick="teamsRefreshSoul()">' + esc(t('teams_soul_refresh') || 'Refresh SOUL') + '</button>';
        }
        if (caps.can_archive_team) {
            html += '<button type="button" class="sm-btn provider-card-btn-danger" onclick="teamsArchiveTeam()">' + esc(t('teams_archive') || 'Archive') + '</button>';
        }
        html += '</div></section>';
    }

    if (caps.can_manage_team_memberships) {
        html += '<section class="members-section"><h3 class="members-section-title">' + esc(t('teams_members_manage') || 'Team members') + '</h3>';
        html += '<div id="teamsMembersManage"></div></section>';
    }

    if (!html) {
        html = '<p class="members-hint">' + esc(t('teams_manage_empty') || 'No team management actions available.') + '</p>';
    }

    pane.innerHTML = html;
    if (caps.can_manage_team_memberships) {
        void teamsLoadMembersManage();
    }
}

async function teamsLoadMembersManage() {
    var host = document.getElementById('teamsMembersManage');
    if (!host) return;
    try {
        var me = await teamsFetchMe();
        var adminTeams = (me.teams || []).filter(function (tm) {
            return (tm.is_team_admin || tm.role === 'admin') && tm.status === 'active';
        });
        if (!adminTeams.length) {
            host.innerHTML = '<p class="members-hint">' + esc(t('teams_no_admin_teams') || 'You are not a team admin on any team.') + '</p>';
            return;
        }
        var html = '<div class="members-token-row"><select id="teamsMembersTeamSelect" class="input">';
        for (var i = 0; i < adminTeams.length; i++) {
            html += '<option value="' + esc(adminTeams[i].id) + '">' + esc(adminTeams[i].id) + '</option>';
        }
        html += '</select>';
        html += '<button type="button" class="sm-btn" onclick="teamsLoadMembersManage()">' + esc(t('refresh') || 'Refresh') + '</button></div>';
        html += '<div id="teamsMembersRoster" style="margin-top:10px"></div>';
        host.innerHTML = html;
        var sel = document.getElementById('teamsMembersTeamSelect');
        if (sel) {
            sel.addEventListener('change', function () { void teamsRenderMembersRoster(sel.value); });
            void teamsRenderMembersRoster(sel.value);
        }
    } catch (e) {
        host.innerHTML = '<p class="members-error">' + esc(e.message) + '</p>';
    }
}

async function teamsRenderMembersRoster(teamId) {
    var host = document.getElementById('teamsMembersRoster');
    if (!host || !teamId) return;
    try {
        var detail = await api('/api/teams/' + encodeURIComponent(teamId));
        var rows = detail.memberships || [];
        if (!rows.length) {
            host.innerHTML = '<p class="members-hint">' + esc(t('teams_no_members') || 'No members.') + '</p>';
            return;
        }
        var actorId = (_teamsStatus && _teamsStatus.actor_member_id) || '';
        var html = '<table class="members-table"><thead><tr><th>Member</th><th>Role</th><th></th></tr></thead><tbody>';
        for (var i = 0; i < rows.length; i++) {
            var row = rows[i];
            var mid = row.member_id || row.id || '';
            var role = row.role || 'member';
            var status = row.status || (row.joined_at ? 'active' : 'pending');
            if (status !== 'active') continue;
            html += '<tr><td class="font-mono">' + esc(mid) + '</td><td>' + esc(role) + '</td><td>';
            if (role === 'admin') {
                html += '<button type="button" class="sm-btn" data-team-demote="' + esc(teamId) + '" data-member="' + esc(mid) + '">'
                    + esc(t('teams_demote_admin') || 'Remove admin') + '</button> ';
            } else {
                html += '<button type="button" class="sm-btn" data-team-promote="' + esc(teamId) + '" data-member="' + esc(mid) + '">'
                    + esc(t('teams_promote_admin') || 'Make admin') + '</button> ';
            }
            if (mid && mid !== actorId) {
                html += '<button type="button" class="sm-btn provider-card-btn-danger" data-team-remove="' + esc(teamId) + '" data-member="' + esc(mid) + '">'
                    + esc(t('teams_remove_member') || 'Remove') + '</button>';
            }
            html += '</td></tr>';
        }
        html += '</tbody></table>';
        host.innerHTML = html;
        host.querySelectorAll('button[data-team-promote]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                void teamsSetAdmin(btn.getAttribute('data-team-promote'), btn.getAttribute('data-member'), 'add');
            });
        });
        host.querySelectorAll('button[data-team-demote]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                void teamsSetAdmin(btn.getAttribute('data-team-demote'), btn.getAttribute('data-member'), 'remove');
            });
        });
        host.querySelectorAll('button[data-team-remove]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                void teamsRemoveMember(btn.getAttribute('data-team-remove'), btn.getAttribute('data-member'));
            });
        });
    } catch (e) {
        host.innerHTML = '<p class="members-error">' + esc(e.message) + '</p>';
    }
}

async function teamsSetAdmin(teamId, memberId, action) {
    await api('/api/teams/' + encodeURIComponent(teamId) + '/admin', {
        method: 'POST',
        body: JSON.stringify({ member_id: memberId, action: action }),
    });
    toast(t('teams_admin_updated') || 'Team role updated');
    void teamsRenderMembersRoster(teamId);
}

async function teamsRemoveMember(teamId, memberId) {
    await api('/api/teams/' + encodeURIComponent(teamId) + '/remove', {
        method: 'POST',
        body: JSON.stringify({ member_id: memberId }),
    });
    toast(t('teams_member_removed') || 'Member removed');
    void teamsRenderMembersRoster(teamId);
}

async function renderTeamsPaneApprovals(pane) {
    pane.innerHTML = '<section class="members-section"><h3 class="members-section-title">'
        + esc(t('teams_section_approvals') || 'Member approvals')
        + '</h3><div id="teamsAdminPending"></div></section>';
    await teamsLoadAdminPending();
}

async function teamsUseTeam(teamId) {
    if (typeof setActiveTeam === 'function') {
        await setActiveTeam(teamId, { silent: true });
    }
    if (typeof showToast === 'function') {
        showToast(t('teams_active_set') || 'Active team updated');
    } else if (typeof toast === 'function') {
        toast(t('teams_active_set') || 'Active team updated');
    }
    teamsInvalidateMe();
    switchTeamsSection('mine');
}

async function teamsJoinTeam() {
    var tid = (document.getElementById('teamsJoinId') || {}).value || '';
    if (!tid) return;
    await api('/api/teams/' + encodeURIComponent(tid) + '/join', { method: 'POST', body: '{}' });
    toast(t('members_join_requested') || 'Join requested');
    teamsInvalidateMe();
    switchTeamsSection('mine');
}

async function teamsCreateTeam() {
    var tid = (document.getElementById('teamsNewId') || {}).value || '';
    var name = (document.getElementById('teamsNewName') || {}).value || '';
    if (!tid) return;
    await api('/api/teams', {
        method: 'POST',
        body: JSON.stringify({ team_id: tid, display_name: name || undefined }),
    });
    toast(t('teams_created') || 'Team created');
    teamsInvalidateMe();
    switchTeamsSection('mine');
}

async function teamsLoadAdminPending() {
    var host = document.getElementById('teamsAdminPending');
    if (!host) return;
    try {
        var me = await teamsFetchMe();
        var adminTeams = (me.teams || []).filter(function (tm) {
            return tm.is_team_admin || tm.role === 'admin';
        });
        var details = await Promise.all(adminTeams.map(function (tm) {
            return api('/api/teams/' + encodeURIComponent(tm.id));
        }));
        var blocks = details.filter(function (d) { return d.can_approve && (d.pending || []).length > 0; });
        if (!blocks.length) {
            host.innerHTML = '<p class="members-hint">' + esc(t('teams_no_pending') || 'No pending join requests.') + '</p>';
            return;
        }
        host.innerHTML = '';
        for (var bi = 0; bi < blocks.length; bi++) {
            var block = blocks[bi];
            var sec = document.createElement('div');
            sec.className = 'teams-pending-block';
            sec.innerHTML = '<div class="teams-pending-title font-mono">' + esc(block.team.id) + '</div>';
            var ul = document.createElement('ul');
            ul.className = 'teams-pending-list';
            for (var pi = 0; pi < block.pending.length; pi++) {
                var p = block.pending[pi];
                var row = document.createElement('li');
                row.className = 'teams-pending-row';
                var mid = p.member_id || p.id || '';
                row.innerHTML = '<span class="font-mono">' + esc(mid) + '</span>';
                var approve = document.createElement('button');
                approve.type = 'button';
                approve.className = 'sm-btn';
                approve.textContent = t('teams_approve') || 'Approve';
                approve.onclick = function (teamId, memberId) {
                    return function () { void teamsApprove(teamId, memberId); };
                }(block.team.id, mid);
                var reject = document.createElement('button');
                reject.type = 'button';
                reject.className = 'sm-btn provider-card-btn-danger';
                reject.textContent = t('teams_reject') || 'Reject';
                reject.onclick = function (teamId, memberId) {
                    return function () { void teamsReject(teamId, memberId); };
                }(block.team.id, mid);
                row.appendChild(approve);
                row.appendChild(reject);
                ul.appendChild(row);
            }
            sec.appendChild(ul);
            host.appendChild(sec);
        }
    } catch (e) {
        host.innerHTML = '<p class="members-error">' + esc(e.message) + '</p>';
    }
}

async function teamsApprove(teamId, memberId) {
    await api('/api/teams/' + encodeURIComponent(teamId) + '/approve', {
        method: 'POST',
        body: JSON.stringify({ member_id: memberId }),
    });
    toast(t('teams_approved') || 'Approved');
    teamsInvalidateMe();
    await teamsLoadAdminPending();
}

async function teamsReject(teamId, memberId) {
    await api('/api/teams/' + encodeURIComponent(teamId) + '/reject', {
        method: 'POST',
        body: JSON.stringify({ member_id: memberId }),
    });
    toast(t('teams_rejected') || 'Rejected');
    await teamsLoadAdminPending();
}

async function teamsLeaveTeam(teamId) {
    await api('/api/teams/' + encodeURIComponent(teamId) + '/leave', { method: 'POST', body: '{}' });
    if (typeof showToast === 'function') {
        showToast(t('teams_left') || 'Left team');
    } else if (typeof toast === 'function') {
        toast(t('teams_left') || 'Left team');
    }
    if (typeof setActiveTeam === 'function') {
        var active = typeof getWebuiActiveTeamId === 'function' ? getWebuiActiveTeamId() : '';
        if (active === teamId) await setActiveTeam(null, { silent: true });
    }
    teamsInvalidateMe();
    switchTeamsSection('mine');
}

async function teamsArchiveTeam() {
    var tid = (document.getElementById('teamsToolId') || {}).value || '';
    if (!tid) return;
    await api('/api/teams/' + encodeURIComponent(tid) + '/archive', { method: 'POST', body: '{}' });
    toast(t('teams_archived') || 'Team archived');
    teamsInvalidateMe();
    switchTeamsSection('manage');
}

async function teamsRefreshSoul() {
    var tid = (document.getElementById('teamsToolId') || {}).value || '';
    if (!tid) return;
    var res = await api('/api/teams/' + encodeURIComponent(tid) + '/soul/refresh', { method: 'POST', body: '{}' });
    toast((res && res.message) || (t('teams_soul_refreshed') || 'SOUL refreshed'));
}
