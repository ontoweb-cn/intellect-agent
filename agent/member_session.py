"""Single-user stub — replaced server-side member sessions."""

def create_member_session(*a, **kw):
    return None


def resolve_member_session(*a, **kw):
    return None


def delete_member_session(*a, **kw):
    pass


def purge_member_file_sessions(*a, **kw):
    pass


def member_session_cookie_name():
    return "intellect_member_session"
