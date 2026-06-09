"""Single-user stub — replaced bootstrap setup."""

def run_members_bootstrap(config, admin_login=None, team_slug=None, project_slug=None, write_cli_session=False):
    from collections import namedtuple
    Result = namedtuple("BootstrapResult", ["admin_member_id", "admin_login", "created_member", "created_team", "created_project"])
    return Result(admin_member_id=None, admin_login=None, created_member=False, created_team=False, created_project=False)
