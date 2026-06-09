"""Single-user stub — replaced project .env management."""

def read_project_env(*a, **kw):
    return {}


def write_project_env(*a, **kw):
    pass


def delete_project_env(*a, **kw):
    pass


def list_project_env_keys(*a, **kw):
    return []


def read_project_soul(*a, **kw):
    return ""


def write_project_soul(*a, **kw):
    pass


def read_team_soul(*a, **kw):
    return ""


def write_team_soul(*a, **kw):
    pass


def read_member_soul(*a, **kw):
    return ""


def _env_path(*a, **kw):
    from intellect_constants import get_intellect_home
    return get_intellect_home() / "projects" / "_default" / ".env"


def _soul_path(*a, **kw):
    from intellect_constants import get_intellect_home
    return get_intellect_home() / "projects" / "_default" / "SOUL.md"
