"""Single-user stub — replaced wiki contributions."""

class WikiContributionsStore:
    def __init__(self, *a, **kw):
        pass

    def close(self):
        pass

    def __getattr__(self, name):
        return lambda *a, **kw: None
