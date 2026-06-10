/**
 * Members Projects panel — side menu + main panes (mirrors teams.js).
 */

var _projectsCurrentSection = 'mine';
var _projectsStatus = null;
var _projectsMe = null;

const PROJECTS_SECTIONS = [
    { key: 'mine', icon: 'briefcase', labelKey: 'projects_section_mine', group: 'mine' },
    {
        key: 'manage',
        icon: 'building-2',
        labelKey: 'projects_section_manage',
        group: 'manage',
        requireAnyCap: ['can_create_project', 'can_archive_project', 'can_manage_project_memberships'],
    },
    {
        key: 'approvals',
        icon: 'user-check',
        labelKey: 'projects_section_approvals',
        group: 'manage',
        requireCap: 'can_manage_project_memberships',
    },
];

const PROJECTS_GROUP_LABELS = {
    mine: 'projects_group_mine',
    manage: 'projects_group_manage',
};

function projectsPaneDomId(key) {
    return 'projectsPane' + key.charAt(0).toUpperCase() + key.slice(1);
}

function projectsSectionVisible(section, caps) {
    if (section.requireCap) return !!caps[section.requireCap];
    if (section.requireAnyCap) {
        return section.requireAnyCap.some(function (k) { return !!caps[k]; });
    }
    return true;
}

function projectsVisibleSections(caps) {
    return PROJECTS_SECTIONS.filter(function (s) { return projectsSectionVisible(s, caps); });
}

function projectsEnsureMainPanes() {
    var main = document.getElementById('mainProjects');
    if (!main) return;
    if (main.querySelector('.projects-pane')) return;
    main.innerHTML =
        '<div class="projects-pane active" id="projectsPaneMine"></div>'
        + '<div class="projects-pane" id="projectsPaneManage"></div>'
        + '<div class="projects-pane" id="projectsPaneApprovals"></div>';
}

function projectsShowDisabledInMain(message) {
    var main = document.getElementById('mainProjects');
    if (!main) return;
    main.innerHTML = '<div class="projects-pane active"><p class="panel-empty" style="padding:20px">' + esc(message) + '</p></div>';
}

function projectsResetMainPanes() {
    var main = document.getElementById('mainProjects');
    if (!main) return;
    main.innerHTML =
        '<div class="projects-pane active" id="projectsPaneMine"></div>'
        + '<div class="projects-pane" id="projectsPaneManage"></div>'
        + '<div class="projects-pane" id="projectsPaneApprovals"></div>';
}

var _projectsLoadInflight = null;

function _projectsStatusWithTimeout(ms) {
    ms = ms || 15000;
    if (typeof fetchMembersStatus !== 'function') {
        return Promise.resolve(null);
    }
    return Promise.race([
        fetchMembersStatus(),
        new Promise(function (_resolve, reject) {
            setTimeout(function () {
                reject(new Error('Members status timed out (' + Math.round(ms / 1000) + 's)'));
            }, ms);
        }),
    ]).then(function (data) {
        if (!data || typeof data !== 'object') return null;
        return data;
    });
}

async function _loadProjectsPanelInner() {
    var menu = document.getElementById('projectsSideMenu');
    if (!menu) {
        console.warn('[projects] #projectsSideMenu not found — is index.html up to date?');
        return;
    }

    var loadingLabel = (typeof t === 'function' ? t('loading') : null) || 'Loading…';
    menu.innerHTML = '<p class="members-hint" style="padding:12px;color:var(--muted);font-size:12px">'
        + (typeof esc === 'function' ? esc(loadingLabel) : loadingLabel) + '</p>';

    try {
        var status = await _projectsStatusWithTimeout(15000);
        _projectsStatus = status;
        _projectsMe = null;

        if (!status || !status.enabled) {
            menu.innerHTML = '';
            projectsShowDisabledInMain(t('projects_disabled_members') || 'Enable members in config.yaml first.');
            return;
        }
        if (!status.projects_enabled) {
            menu.innerHTML = '';
            projectsShowDisabledInMain(t('projects_disabled_projects') || 'Enable members.projects in config.yaml.');
            return;
        }
        if (!status.actor_member_id) {
            menu.innerHTML = '';
            projectsShowDisabledInMain(t('projects_sign_in_first') || 'Sign in to manage projects.');
            return;
        }

        projectsResetMainPanes();
        projectsEnsureMainPanes();

        var caps = status.capabilities || {};
        var visible = projectsVisibleSections(caps);
        if (!visible.length) {
            menu.innerHTML = '<p class="members-hint" style="padding:12px">'
                + esc(t('projects_manage_empty') || 'No project sections available.') + '</p>';
            projectsShowDisabledInMain(t('projects_manage_empty') || 'No project sections available.');
            return;
        }
        if (!visible.some(function (s) { return s.key === _projectsCurrentSection; })) {
            _projectsCurrentSection = visible[0].key;
        }

        var menuHtml = '';
        var lastGroup = '';

        for (var gi = 0; gi < PROJECTS_SECTIONS.length; gi++) {
            var section = PROJECTS_SECTIONS[gi];
            if (!projectsSectionVisible(section, caps)) continue;

            if (section.group !== lastGroup) {
                var groupKey = PROJECTS_GROUP_LABELS[section.group];
                if (groupKey) {
                    menuHtml += '<div class="side-menu-group-title">' + esc(t(groupKey) || section.group) + '</div>';
                }
                lastGroup = section.group;
            }

            menuHtml += '<button type="button" class="side-menu-item'
                + (_projectsCurrentSection === section.key ? ' active' : '')
                + '" data-projects-section="' + section.key + '" onclick="switchProjectsSection(\'' + section.key + '\')">'
                + (typeof li === 'function' ? li(section.icon, 16) : '')
                + '<span>' + esc(t(section.labelKey) || section.key) + '</span></button>';
        }

        menu.innerHTML = menuHtml || (
            '<p class="members-hint" style="padding:12px">'
            + esc(t('projects_section_mine') || 'My projects') + '</p>'
        );
        switchProjectsSection(_projectsCurrentSection);
    } catch (e) {
        menu.innerHTML = '<p class="members-error" style="padding:12px">'
            + esc(e && e.message ? e.message : String(e)) + '</p>';
        projectsShowDisabledInMain(
            (e && e.message) || (t('projects_load_failed') || 'Could not load projects panel.')
        );
    }
}

async function loadProjectsPanel() {
    if (_projectsLoadInflight) return _projectsLoadInflight;
    _projectsLoadInflight = _loadProjectsPanelInner().finally(function () {
        _projectsLoadInflight = null;
    });
    return _projectsLoadInflight;
}

function openProjectsPanel(opts) {
    opts = opts || { fromRailClick: true };
    if (typeof switchPanel === 'function') {
        void switchPanel('projects', opts);
    }
    void loadProjectsPanel();
}
// panels.js defines a fallback; overwrite when this script loads successfully.
if (typeof window !== 'undefined') {
    window.openProjectsPanel = openProjectsPanel;
}

function _bindProjectsPanelTriggers() {
    if (window.__projectsPanelTriggersBound) return;
    window.__projectsPanelTriggersBound = true;

    document.addEventListener('click', function (ev) {
        var btn = ev.target && ev.target.closest
            ? ev.target.closest('[data-panel="projects"]')
            : null;
        if (!btn || btn.classList.contains('nav-tab-hidden')) return;
        setTimeout(function () { void loadProjectsPanel(); }, 0);
    }, true);

    var panel = document.getElementById('panelProjects');
    if (panel && typeof MutationObserver !== 'undefined') {
        var obs = new MutationObserver(function () {
            if (panel.classList.contains('active')) void loadProjectsPanel();
        });
        obs.observe(panel, { attributes: true, attributeFilter: ['class'] });
    }
}

function switchProjectsSection(name) {
    _projectsCurrentSection = name;

    projectsEnsureMainPanes();

    var menu = document.getElementById('projectsSideMenu');
    if (menu) {
        menu.querySelectorAll('.side-menu-item').forEach(function (el) {
            el.classList.toggle('active', el.dataset.projectsSection === name);
        });
    }

    var panes = document.querySelectorAll('#mainProjects .projects-pane');
    for (var i = 0; i < panes.length; i++) {
        panes[i].classList.toggle('active', panes[i].id === projectsPaneDomId(name));
    }

    var pane = document.getElementById(projectsPaneDomId(name));
    if (!pane) return;
    pane.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px">' + esc(t('loading') || 'Loading…') + '</div>';

    switch (name) {
        case 'mine':
            void renderProjectsPaneMine(pane);
            break;
        case 'manage':
            void renderProjectsPaneManage(pane);
            break;
        case 'approvals':
            void renderProjectsPaneApprovals(pane);
            break;
    }
}

async function projectsFetchMe() {
    if (_projectsMe) return _projectsMe;
    _projectsMe = await api('/api/members/me/projects');
    return _projectsMe;
}

function projectsInvalidateMe() {
    _projectsMe = null;
}

async function renderProjectsPaneMine(pane) {
    var status = _projectsStatus;
    if (!status || !status.actor_member_id) {
        pane.innerHTML = '<p class="panel-empty">' + esc(t('projects_sign_in_first') || 'Sign in to manage projects.') + '</p>';
        return;
    }

    var me;
    try {
        me = await projectsFetchMe();
    } catch (e) {
        pane.innerHTML = '<p class="members-error">' + esc(e.message) + '</p>';
        return;
    }

    var projects = me.projects || [];
    var html = '<p class="members-hint" style="margin-top:0">' + esc(t('projects_panel_desc') || '') + '</p>';

    if (me.requires_project_selection && !me.active_project_id && typeof getWebuiActiveProjectId === 'function' && !getWebuiActiveProjectId()) {
        html += '<div class="teams-banner">' + esc(t('projects_multi_banner') || 'Select an active project from the account menu.') + '</div>';
    }

    html += '<section class="members-section"><h3 class="members-section-title">' + esc(t('projects_my_projects') || 'My projects') + '</h3>';
    if (!projects.length) {
        html += '<p class="members-hint">' + esc(t('projects_no_memberships') || 'No project memberships yet.') + '</p>';
    } else {
        html += '<ul class="teams-action-list">';
        for (var i = 0; i < projects.length; i++) {
            var pm = projects[i];
            var active = (me.active_project_id || (typeof getWebuiActiveProjectId === 'function' ? getWebuiActiveProjectId() : '')) === pm.id;
            html += '<li class="teams-action-row">';
            html += '<div><span class="font-mono">' + esc(pm.id) + '</span>';
            if (pm.display_name) html += ' <span class="teams-muted">— ' + esc(pm.display_name) + '</span>';
            if (pm.role === 'project_admin' || pm.is_project_admin) {
                html += ' <span class="oauth-badge oauth-connected">' + esc(t('projects_role_admin') || 'Project admin') + '</span>';
            }
            html += ' <span class="oauth-badge ' + (pm.status === 'active' ? 'oauth-connected' : 'oauth-disconnected') + '">' + esc(pm.status) + '</span></div>';
            if (pm.status === 'active') {
                html += '<button type="button" class="sm-btn' + (active ? ' provider-card-btn-primary' : '') + '" data-project-use="' + esc(pm.id) + '">'
                    + esc(active ? (t('projects_active') || 'Active') : (t('projects_use') || 'Use')) + '</button>';
                html += ' <button type="button" class="sm-btn" data-project-leave="' + esc(pm.id) + '">'
                    + esc(t('projects_leave') || 'Leave') + '</button>';
            }
            html += '</li>';
        }
        html += '</ul>';
    }
    html += '</section>';

    html += '<section class="members-section"><h3 class="members-section-title">' + esc(t('projects_join') || 'Join a project') + '</h3>';
    html += '<div class="members-token-row"><input type="text" id="projectsJoinId" class="input" placeholder="' + esc(t('members_project_id') || 'project id') + '">';
    html += '<button type="button" class="sm-btn" onclick="projectsJoinProject()">' + esc(t('members_join_project') || 'Request join') + '</button></div></section>';

    pane.innerHTML = html;

    pane.querySelectorAll('button[data-project-use]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var projectId = btn.getAttribute('data-project-use');
            if (projectId) void projectsUseProject(projectId);
        });
    });
    pane.querySelectorAll('button[data-project-leave]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var projectId = btn.getAttribute('data-project-leave');
            if (projectId) void projectsLeaveProject(projectId);
        });
    });
}

async function renderProjectsPaneManage(pane) {
    var status = _projectsStatus;
    var caps = (status && status.capabilities) || {};
    var html = '';

    if (caps.can_create_project) {
        html += '<section class="members-section"><h3 class="members-section-title">' + esc(t('projects_create') || 'Create project') + '</h3>';
        html += '<div class="members-token-row"><input type="text" id="projectsNewId" class="input" placeholder="' + esc(t('members_project_id') || 'project id') + '">';
        html += '<input type="text" id="projectsNewName" class="input" placeholder="' + esc(t('projects_display_name') || 'Display name') + '">';
        html += '<button type="button" class="sm-btn" onclick="projectsCreateProject()">' + esc(t('create') || 'Create') + '</button></div></section>';
    }

    if (caps.can_archive_project) {
        html += '<section class="members-section"><h3 class="members-section-title">' + esc(t('projects_tools') || 'Project tools') + '</h3>';
        html += '<div class="members-token-row"><input type="text" id="projectsToolId" class="input" placeholder="' + esc(t('members_project_id') || 'project id') + '">';
        html += '<button type="button" class="sm-btn provider-card-btn-danger" onclick="projectsArchiveProject()">' + esc(t('projects_archive') || 'Archive') + '</button>';
        html += '</div></section>';
    }

    if (caps.can_manage_project_memberships) {
        html += '<section class="members-section"><h3 class="members-section-title">' + esc(t('projects_soul_edit') || 'Project SOUL') + '</h3>';
        html += '<div class="members-token-row"><input type="text" id="projectsSoulId" class="input" placeholder="' + esc(t('members_project_id') || 'project id') + '">';
        html += '<button type="button" class="sm-btn" onclick="projectsLoadSoulEditor()">' + esc(t('projects_soul_load') || 'Load') + '</button></div>';
        html += '<div id="projectsSoulEditor" style="margin-top:10px"></div></section>';

        html += '<section class="members-section"><h3 class="members-section-title">' + esc(t('projects_link_teams') || 'Linked teams') + '</h3>';
        html += '<div class="members-token-row"><input type="text" id="projectsLinkProjectId" class="input" placeholder="' + esc(t('members_project_id') || 'project id') + '">';
        html += '<input type="text" id="projectsLinkTeamId" class="input" placeholder="' + esc(t('members_team_id') || 'team id') + '">';
        html += '<button type="button" class="sm-btn" onclick="projectsLinkTeam()">' + esc(t('projects_link_team') || 'Link') + '</button></div>';
        html += '<div id="projectsLinkedTeams" style="margin-top:10px"></div></section>';

        html += '<section class="members-section"><h3 class="members-section-title">' + esc(t('projects_members_manage') || 'Project members') + '</h3>';
        html += '<div id="projectsMembersManage"></div></section>';
    }

    if (!html) {
        html = '<p class="members-hint">' + esc(t('projects_manage_empty') || 'No project management actions available.') + '</p>';
    }

    pane.innerHTML = html;
    if (caps.can_manage_project_memberships) {
        void projectsLoadMembersManage();
        var linkPid = document.getElementById('projectsLinkProjectId');
        if (linkPid) {
            linkPid.addEventListener('change', function () {
                void projectsRenderLinkedTeams(linkPid.value);
            });
            if (linkPid.value) void projectsRenderLinkedTeams(linkPid.value);
        }
    }
}

async function projectsLoadMembersManage() {
    var host = document.getElementById('projectsMembersManage');
    if (!host) return;
    try {
        var me = await projectsFetchMe();
        var adminProjects = (me.projects || []).filter(function (pm) {
            return (pm.is_project_admin || pm.role === 'project_admin') && pm.status === 'active';
        });
        if (!adminProjects.length) {
            host.innerHTML = '<p class="members-hint">' + esc(t('projects_no_admin_projects') || 'You are not a project admin on any project.') + '</p>';
            return;
        }
        var html = '<div class="members-token-row"><select id="projectsMembersProjectSelect" class="input">';
        for (var i = 0; i < adminProjects.length; i++) {
            html += '<option value="' + esc(adminProjects[i].id) + '">' + esc(adminProjects[i].id) + '</option>';
        }
        html += '</select>';
        html += '<button type="button" class="sm-btn" onclick="projectsLoadMembersManage()">' + esc(t('refresh') || 'Refresh') + '</button></div>';
        html += '<div id="projectsMembersRoster" style="margin-top:10px"></div>';
        host.innerHTML = html;
        var sel = document.getElementById('projectsMembersProjectSelect');
        if (sel) {
            sel.addEventListener('change', function () { void projectsRenderMembersRoster(sel.value); });
            void projectsRenderMembersRoster(sel.value);
        }
    } catch (e) {
        host.innerHTML = '<p class="members-error">' + esc(e.message) + '</p>';
    }
}

async function projectsRenderMembersRoster(projectId) {
    var host = document.getElementById('projectsMembersRoster');
    if (!host || !projectId) return;
    try {
        var detail = await api('/api/member-projects/' + encodeURIComponent(projectId));
        var rows = detail.memberships || [];
        if (!rows.length) {
            host.innerHTML = '<p class="members-hint">' + esc(t('projects_no_members') || 'No members.') + '</p>';
            return;
        }
        var actorId = (_projectsStatus && _projectsStatus.actor_member_id) || '';
        var html = '<table class="members-table"><thead><tr><th>Member</th><th>Role</th><th></th></tr></thead><tbody>';
        for (var i = 0; i < rows.length; i++) {
            var row = rows[i];
            var mid = row.member_id || row.id || '';
            var role = row.role || 'member';
            var status = row.status || (row.joined_at ? 'active' : 'pending');
            if (status !== 'active') continue;
            html += '<tr><td class="font-mono">' + esc(mid) + '</td><td>' + esc(role) + '</td><td>';
            if (role === 'project_admin') {
                html += '<button type="button" class="sm-btn" data-project-demote="' + esc(projectId) + '" data-member="' + esc(mid) + '">'
                    + esc(t('projects_demote_admin') || 'Remove admin') + '</button> ';
            } else {
                html += '<button type="button" class="sm-btn" data-project-promote="' + esc(projectId) + '" data-member="' + esc(mid) + '">'
                    + esc(t('projects_promote_admin') || 'Make admin') + '</button> ';
            }
            if (mid && mid !== actorId) {
                html += '<button type="button" class="sm-btn provider-card-btn-danger" data-project-remove="' + esc(projectId) + '" data-member="' + esc(mid) + '">'
                    + esc(t('projects_remove_member') || 'Remove') + '</button>';
            }
            html += '</td></tr>';
        }
        html += '</tbody></table>';
        host.innerHTML = html;
        host.querySelectorAll('button[data-project-promote]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                void projectsSetAdmin(btn.getAttribute('data-project-promote'), btn.getAttribute('data-member'), 'add');
            });
        });
        host.querySelectorAll('button[data-project-demote]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                void projectsSetAdmin(btn.getAttribute('data-project-demote'), btn.getAttribute('data-member'), 'remove');
            });
        });
        host.querySelectorAll('button[data-project-remove]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                void projectsRemoveMember(btn.getAttribute('data-project-remove'), btn.getAttribute('data-member'));
            });
        });
    } catch (e) {
        host.innerHTML = '<p class="members-error">' + esc(e.message) + '</p>';
    }
}

async function projectsSetAdmin(projectId, memberId, action) {
    await api('/api/member-projects/' + encodeURIComponent(projectId) + '/admin', {
        method: 'POST',
        body: JSON.stringify({ member_id: memberId, action: action }),
    });
    toast(t('projects_admin_updated') || 'Project role updated');
    void projectsRenderMembersRoster(projectId);
}

async function projectsRemoveMember(projectId, memberId) {
    await api('/api/member-projects/' + encodeURIComponent(projectId) + '/remove', {
        method: 'POST',
        body: JSON.stringify({ member_id: memberId }),
    });
    toast(t('projects_member_removed') || 'Member removed');
    void projectsRenderMembersRoster(projectId);
}

async function projectsLoadSoulEditor() {
    var host = document.getElementById('projectsSoulEditor');
    var pid = (document.getElementById('projectsSoulId') || {}).value || '';
    if (!host || !pid) return;
    host.innerHTML = '<p class="members-hint">' + esc(t('loading') || 'Loading…') + '</p>';
    try {
        var data = await api('/api/member-projects/' + encodeURIComponent(pid) + '/soul');
        host.innerHTML = ''
            + '<textarea id="projectsSoulText" class="input" rows="8" style="width:100%;font-family:var(--mono)">'
            + esc(data.content || '') + '</textarea>'
            + '<div style="margin-top:8px"><button type="button" class="sm-btn" onclick="projectsSaveSoul()">'
            + esc(t('projects_soul_save') || 'Save SOUL') + '</button></div>';
    } catch (e) {
        host.innerHTML = '<p class="members-error">' + esc(e.message) + '</p>';
    }
}

async function projectsSaveSoul() {
    var pid = (document.getElementById('projectsSoulId') || {}).value || '';
    var text = (document.getElementById('projectsSoulText') || {}).value || '';
    if (!pid) return;
    await api('/api/member-projects/' + encodeURIComponent(pid) + '/soul', {
        method: 'POST',
        body: JSON.stringify({ content: text }),
    });
    toast(t('projects_soul_saved') || 'SOUL saved');
}

async function projectsRenderLinkedTeams(projectId) {
    var host = document.getElementById('projectsLinkedTeams');
    if (!host) return;
    if (!projectId) {
        host.innerHTML = '';
        return;
    }
    try {
        var detail = await api('/api/member-projects/' + encodeURIComponent(projectId));
        var teams = detail.linked_teams || [];
        if (!teams.length) {
            host.innerHTML = '<p class="members-hint">' + esc(t('projects_no_linked_teams') || 'No linked teams.') + '</p>';
            return;
        }
        var html = '<ul class="teams-action-list">';
        for (var i = 0; i < teams.length; i++) {
            var tm = teams[i];
            html += '<li class="teams-action-row"><span class="font-mono">' + esc(tm.id) + '</span>';
            if (tm.display_name) html += ' <span class="teams-muted">— ' + esc(tm.display_name) + '</span>';
            html += '<button type="button" class="sm-btn provider-card-btn-danger" data-unlink-project="'
                + esc(projectId) + '" data-unlink-team="' + esc(tm.id) + '">'
                + esc(t('projects_unlink_team') || 'Unlink') + '</button></li>';
        }
        html += '</ul>';
        host.innerHTML = html;
        host.querySelectorAll('button[data-unlink-project]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                void projectsUnlinkTeam(
                    btn.getAttribute('data-unlink-project'),
                    btn.getAttribute('data-unlink-team')
                );
            });
        });
    } catch (e) {
        host.innerHTML = '<p class="members-error">' + esc(e.message) + '</p>';
    }
}

async function projectsLinkTeam() {
    var pid = (document.getElementById('projectsLinkProjectId') || {}).value || '';
    var tid = (document.getElementById('projectsLinkTeamId') || {}).value || '';
    if (!pid || !tid) return;
    await api('/api/member-projects/' + encodeURIComponent(pid) + '/link-team', {
        method: 'POST',
        body: JSON.stringify({ team_id: tid }),
    });
    toast(t('projects_team_linked') || 'Team linked');
    void projectsRenderLinkedTeams(pid);
}

async function projectsUnlinkTeam(projectId, teamId) {
    await api('/api/member-projects/' + encodeURIComponent(projectId) + '/unlink-team', {
        method: 'POST',
        body: JSON.stringify({ team_id: teamId }),
    });
    toast(t('projects_team_unlinked') || 'Team unlinked');
    void projectsRenderLinkedTeams(projectId);
}

async function renderProjectsPaneApprovals(pane) {
    pane.innerHTML = '<section class="members-section"><h3 class="members-section-title">'
        + esc(t('projects_section_approvals') || 'Member approvals')
        + '</h3><div id="projectsAdminPending"></div></section>';
    await projectsLoadAdminPending();
}

async function projectsUseProject(projectId) {
    if (typeof setActiveProject === 'function') {
        await setActiveProject(projectId, { silent: false });
    }
    if (typeof showToast === 'function') {
        showToast(t('projects_active_set') || 'Active project updated');
    } else if (typeof toast === 'function') {
        toast(t('projects_active_set') || 'Active project updated');
    }
    projectsInvalidateMe();
    switchProjectsSection('mine');
}

async function projectsJoinProject() {
    var pid = (document.getElementById('projectsJoinId') || {}).value || '';
    if (!pid) return;
    await api('/api/member-projects/' + encodeURIComponent(pid) + '/join', { method: 'POST', body: '{}' });
    toast(t('members_join_requested') || 'Join requested');
    projectsInvalidateMe();
    switchProjectsSection('mine');
}

async function projectsCreateProject() {
    var pid = (document.getElementById('projectsNewId') || {}).value || '';
    var name = (document.getElementById('projectsNewName') || {}).value || '';
    if (!pid) return;
    await api('/api/member-projects', {
        method: 'POST',
        body: JSON.stringify({ project_id: pid, display_name: name || undefined }),
    });
    toast(t('projects_created') || 'Project created');
    projectsInvalidateMe();
    switchProjectsSection('mine');
}

async function projectsLoadAdminPending() {
    var host = document.getElementById('projectsAdminPending');
    if (!host) return;
    try {
        var me = await projectsFetchMe();
        var adminProjects = (me.projects || []).filter(function (pm) {
            return pm.is_project_admin || pm.role === 'project_admin';
        });
        var details = await Promise.all(adminProjects.map(function (pm) {
            return api('/api/member-projects/' + encodeURIComponent(pm.id));
        }));
        var blocks = details.filter(function (d) { return d.can_approve && (d.pending || []).length > 0; });
        if (!blocks.length) {
            host.innerHTML = '<p class="members-hint">' + esc(t('projects_no_pending') || 'No pending join requests.') + '</p>';
            return;
        }
        host.innerHTML = '';
        for (var bi = 0; bi < blocks.length; bi++) {
            var block = blocks[bi];
            var sec = document.createElement('div');
            sec.className = 'teams-pending-block';
            sec.innerHTML = '<div class="teams-pending-title font-mono">' + esc(block.project.id) + '</div>';
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
                approve.textContent = t('projects_approve') || 'Approve';
                approve.onclick = function (projectId, memberId) {
                    return function () { void projectsApprove(projectId, memberId); };
                }(block.project.id, mid);
                var reject = document.createElement('button');
                reject.type = 'button';
                reject.className = 'sm-btn provider-card-btn-danger';
                reject.textContent = t('projects_reject') || 'Reject';
                reject.onclick = function (projectId, memberId) {
                    return function () { void projectsReject(projectId, memberId); };
                }(block.project.id, mid);
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

async function projectsApprove(projectId, memberId) {
    await api('/api/member-projects/' + encodeURIComponent(projectId) + '/approve', {
        method: 'POST',
        body: JSON.stringify({ member_id: memberId }),
    });
    toast(t('projects_approved') || 'Approved');
    projectsInvalidateMe();
    await projectsLoadAdminPending();
}

async function projectsReject(projectId, memberId) {
    await api('/api/member-projects/' + encodeURIComponent(projectId) + '/reject', {
        method: 'POST',
        body: JSON.stringify({ member_id: memberId }),
    });
    toast(t('projects_rejected') || 'Rejected');
    await projectsLoadAdminPending();
}

async function projectsLeaveProject(projectId) {
    await api('/api/member-projects/' + encodeURIComponent(projectId) + '/leave', { method: 'POST', body: '{}' });
    if (typeof showToast === 'function') {
        showToast(t('projects_left') || 'Left project');
    } else if (typeof toast === 'function') {
        toast(t('projects_left') || 'Left project');
    }
    if (typeof setActiveProject === 'function') {
        var active = typeof getWebuiActiveProjectId === 'function' ? getWebuiActiveProjectId() : '';
        if (active === projectId) await setActiveProject(null, { silent: true });
    }
    projectsInvalidateMe();
    switchProjectsSection('mine');
}

async function projectsArchiveProject() {
    var pid = (document.getElementById('projectsToolId') || {}).value || '';
    if (!pid) return;
    await api('/api/member-projects/' + encodeURIComponent(pid) + '/archive', { method: 'POST', body: '{}' });
    toast(t('projects_archived') || 'Project archived');
    projectsInvalidateMe();
    switchProjectsSection('manage');
}

if (typeof window !== 'undefined') {
    window.loadProjectsPanel = loadProjectsPanel;
    window.switchProjectsSection = switchProjectsSection;
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _bindProjectsPanelTriggers);
    } else {
        _bindProjectsPanelTriggers();
    }
}
