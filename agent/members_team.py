"""Single-user stub — replaced team resolution."""
class TeamRequiredError(Exception):
    pass


def resolve_member_team_id(*a, **kw):
    return None


def member_requires_team_selection(*a, **kw):
    return False
