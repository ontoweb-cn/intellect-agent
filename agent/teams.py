"""Single-user stub — replaced team management."""

def is_teams_enabled(config=None):
    return False


def ensure_team_dirs(*a, **kw):
    from intellect_constants import get_intellect_home
    d = get_intellect_home() / "teams" / "_default"
    d.mkdir(parents=True, exist_ok=True)
    return d


class TeamDB:
    def __init__(self, *a, **kw):
        pass

    def close(self):
        pass

    def __getattr__(self, name):
        return lambda *a, **kw: None

    conn = None
    _conn = None
