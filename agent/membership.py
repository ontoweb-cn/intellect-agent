"""Single-user stub — replaced multi-user membership system."""

# Feature flags (always off)
def is_members_enabled(config=None):
    return False


def is_teams_enabled(config=None):
    return False


def is_projects_enabled(config=None):
    return False


def is_rbac_v2_enabled(config=None):
    return False


def can_manage_registrations(*a, **kw):
    return False


def can_create_invites(*a, **kw):
    return False


def can_view_member_audit(*a, **kw):
    return False


def authorize(*a, **kw):
    return False


def effective_member_role(*a, **kw):
    return None


def get_session_ttl(*a, **kw):
    return 7 * 24 * 3600  # 1 week in seconds


class Action:
    CHAT = "chat"
    READ = "read"
    ADMIN = "admin"
    MEMBER_INVITE = "member_invite"
    MEMBER_KICK = "member_kick"
    API_TOKEN_MANAGE = "api_token_manage"
    PROJECT_CREATE = "project_create"
    PROJECT_MANAGE = "project_manage"
    PROJECT_ARCHIVE = "project_archive"
    PROJECT_DELETE = "project_delete"
    PROJECT_APPROVE_JOIN = "project_approve_join"
    TEAM_CREATE = "team_create"
    TEAM_MANAGE = "team_manage"
    TEAM_ARCHIVE = "team_archive"
    TEAM_DELETE = "team_delete"
    TEAM_APPROVE_JOIN = "team_approve_join"
    TEAM_MEMBER_ADD = "team_member_add"
    TEAM_MEMBER_REMOVE = "team_member_remove"
    TEAM_MEMBER_LIST = "team_member_list"


class Resource:
    def __init__(self, type="", id=None):
        self.type = type
        self.id = id

    @classmethod
    def for_scope(cls, scope_type: str, scope_id: str) -> "Resource":
        """Create a Resource for a given scope type (team, project, etc.)."""
        return cls(type=scope_type, id=scope_id)


def seed_builtin_role_definitions(*a, **kw):
    pass


def members_mode(config=None):
    """Single-user: always 'legacy' (no multi-user mode)."""
    return "legacy"


def get_registration_config(config=None):
    """Single-user: no local registration."""
    return {"local_requires_approval": True}


def validate_member_id(member_id):
    """Single-user: no member IDs are valid."""
    return False


def validate_team_id(team_id):
    """Single-user: no team IDs are valid."""
    return False


def validate_project_id(project_id):
    """Single-user: no project IDs are valid."""
    return False


def normalize_member_lookup(*a, **kw):
    return None


def get_member_dir(*a, **kw):
    from intellect_constants import get_intellect_home
    return get_intellect_home() / "members" / "_default"


def ensure_member_dirs(*a, **kw):
    d = get_member_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


class MembershipDB:
    def __init__(self, *a, **kw):
        pass

    def close(self):
        pass

    def __getattr__(self, name):
        return lambda *a, **kw: None

    conn = None
    _conn = None


class MembershipStore(MembershipDB):
    pass


class TeamDB(MembershipDB):
    pass


class ProjectDB(MembershipDB):
    pass
