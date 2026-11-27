"""Static check for the advisory coverage workflow triggers."""
from pathlib import Path


def main() -> None:
    text = Path(".github/workflows/tests.yml").read_text()
    print("workflow_dispatch", "workflow_dispatch:" in text)
    print("schedule", "schedule:" in text)
    print("coverage_advisory", "continue-on-error: true" in text)
    assert "workflow_dispatch:" in text
    assert "schedule:" in text
    assert "continue-on-error: true" in text


if __name__ == "__main__":
    main()
