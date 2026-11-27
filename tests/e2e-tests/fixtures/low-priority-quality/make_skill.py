"""Create an e2e-prefixed skill in a temp SKILLS_DIR."""
from pathlib import Path

def make_skill(root: Path, name: str, description: str = "Old description.") -> Path:
    root = Path(root)
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nBody.\n",
        encoding="utf-8",
    )
    return skill_md
