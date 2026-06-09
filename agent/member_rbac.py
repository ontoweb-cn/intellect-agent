"""Single-user stub — replaced tool-level RBAC."""

def bind_tool_rbac_context(*a, **kw):
    return None


def reset_tool_rbac_context(*a, **kw):
    pass


def check_member_tool_permission(*a, **kw):
    return None  # No denial


def check_member_chat_permission(*a, **kw):
    return None  # No denial
