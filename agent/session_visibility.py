"""Single-user stub — replaced session isolation."""

def resolve_session_list_scope(*a, **kw):
    from collections import namedtuple
    Scope = namedtuple("Scope", ["unrestricted"])
    return Scope(unrestricted=True)


def session_row_visible(*a, **kw):
    return True


def session_visible(*a, **kw):
    return True


def member_session_sql_filter(*a, **kw):
    return ("", [])


def get_session_isolation_settings(*a, **kw):
    return {}


def actor_sees_all_member_sessions(*a, **kw):
    return True
