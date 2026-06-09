"""Single-user stub — replaced project management."""

def is_projects_enabled(config=None):
    return False


def get_project_dir(*a, **kw):
    from intellect_constants import get_intellect_home
    return get_intellect_home() / "projects" / "_default"


def ensure_project_dirs(*a, **kw):
    d = get_project_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_projects_home(*a, **kw):
    from intellect_constants import get_intellect_home
    return get_intellect_home() / "projects"


class ProjectDB:
    def __init__(self, *a, **kw):
        pass

    def close(self):
        pass

    def __getattr__(self, name):
        return lambda *a, **kw: None

    conn = None
    _conn = None
