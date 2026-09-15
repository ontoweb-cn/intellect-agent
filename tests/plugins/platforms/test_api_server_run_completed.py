"""``run.completed`` must say how the run ended, not only what it produced.

The runs channel is the containerized deployment's transport. It emits
``run.completed`` for anything that did not set ``failed`` — but "did not
fail" is not "finished". A run stopped by the output ceiling returns
``completed=False, partial=True`` with ``failed`` unset, so without these
fields a truncated answer arrived indistinguishable from a complete one and
was rendered to the user as final. ``chat/completions`` on the same result
already reports them; this pins the parity.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ADAPTER_PATH = (
    Path(__file__).resolve().parents[3] / "plugins" / "platforms" / "api_server" / "adapter.py"
)


def _load_run_completed_payload():
    """Import the helper without executing the platform plugin's registration."""
    spec = importlib.util.spec_from_file_location("_api_server_adapter_probe", _ADAPTER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._run_completed_payload


@pytest.fixture(scope="module")
def payload_fn():
    try:
        return _load_run_completed_payload()
    except Exception as exc:  # pragma: no cover - environment without the plugin deps
        pytest.skip(f"api_server adapter not importable: {exc}")


def test_a_clean_result_reports_a_complete_run(payload_fn) -> None:
    payload = payload_fn(run_id="r1", output="done", usage={}, result={"final_response": "done"})
    assert payload["event"] == "run.completed"
    assert payload["completed"] is True
    assert payload["partial"] is False
    assert "error" not in payload


def test_a_minimal_result_is_treated_as_complete(payload_fn) -> None:
    """Absent keys mean an older/minimal result, i.e. a clean finish."""
    payload = payload_fn(run_id="r1", output="x", usage={}, result={})
    assert payload["completed"] is True
    assert payload["partial"] is False


def test_a_truncated_run_reports_it_and_keeps_the_reason(payload_fn) -> None:
    """The case the channel could not express before."""
    result = {
        "final_response": "partial text",
        "completed": False,
        "partial": True,
        "error": "Response truncated due to output length limit",
    }
    payload = payload_fn(run_id="r1", output="partial text", usage={}, result=result)

    assert payload["completed"] is False
    assert payload["partial"] is True
    assert payload["error"] == "Response truncated due to output length limit"
    # The text still travels — it is worth showing, just not as the answer.
    assert payload["output"] == "partial text"


def test_a_partial_run_without_an_error_message_still_reports_partial(payload_fn) -> None:
    payload = payload_fn(
        run_id="r1", output="", usage={}, result={"completed": False, "partial": True}
    )
    assert payload["completed"] is False
    assert payload["partial"] is True


def test_usage_and_identity_survive(payload_fn) -> None:
    usage = {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
    payload = payload_fn(run_id="run-abc", output="x", usage=usage, result={"final_response": "x"})
    assert payload["run_id"] == "run-abc"
    assert payload["usage"] == usage
    assert isinstance(payload["timestamp"], float)
