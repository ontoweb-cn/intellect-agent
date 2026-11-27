"""Diagnostic probe for _find_all_skills cache invalidation (R6: no browser UI)."""
import tempfile

import tools.skills_tool as st


def main() -> None:
    from pathlib import Path as _Path
    with tempfile.TemporaryDirectory(prefix="e2e-low-priority-skills-") as td:
        root = _Path(td)
        skill_md = root / "e2e-low-priority-skill" / "SKILL.md"
        skill_md.parent.mkdir(parents=True)
        skill_md.write_text("---\nname: e2e-low-priority-skill\ndescription: Old description.\n---\n\nBody.\n")
        old = st.SKILLS_DIR
        st.SKILLS_DIR = root
        st._SKILLS_CACHE["signature"] = None
        try:
            first = st._find_all_skills()
            skill_md.write_text(
                "---\nname: e2e-low-priority-skill\ndescription: Updated in place.\n---\n\nBody.\n"
            )
            second = st._find_all_skills()
        finally:
            st.SKILLS_DIR = old
        print("first", first[0]["description"])
        print("second", second[0]["description"])
        assert second[0]["description"] == "Updated in place."


if __name__ == "__main__":
    main()
