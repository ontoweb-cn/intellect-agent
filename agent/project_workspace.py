"""Single-user stub — replaced project git workspace."""

def get_workspace_path(*a, **kw):
    from intellect_constants import get_intellect_home
    return str(get_intellect_home() / "workspace")


def resolve_workspace(*a, **kw):
    from intellect_constants import get_intellect_home
    return str(get_intellect_home() / "workspace")


def clone_project_repo(*a, **kw):
    pass
