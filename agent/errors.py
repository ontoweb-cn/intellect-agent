"""Agent error types extracted from run_agent.py."""

from __future__ import annotations

from typing import Any, Dict, Optional


class _StreamErrorEvent(Exception):
    """Raised when the API stream emits an error event mid-generation."""

    def __init__(
        self,
        message: str,
        code: Optional[str] = None,
        param: Optional[str] = None,
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.param = param
        self.status_code = status_code
        self.body: Dict[str, Any] = {
            "error": {
                "message": message,
                "code": code,
                "param": param,
                "type": "error",
            }
        }
