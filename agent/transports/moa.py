"""MoA transport — converts the moa api_mode into build_kwargs / normalize_response.

Registered via ``register_transport("moa", MoaTransport)`` so the
existing agent loop dispatch (``agent._get_transport()``,
``build_api_kwargs``, ``interruptible_api_call``) routes MoA requests
to the orchestration loop in ``agent.moa_loop``.
"""

from __future__ import annotations

from typing import Any

from agent.transports import register_transport
from agent.transports.base import ProviderTransport
from agent.transports.types import NormalizedResponse


class MoaTransport(ProviderTransport):
    """Transport that routes "moa" api_mode to the MoA orchestration loop."""

    @property
    def api_mode(self) -> str:
        return "moa"

    def convert_messages(self, messages, **kwargs):
        # MoA handles message conversion internally
        return messages

    def convert_tools(self, tools):
        # MoA does not support tool calling (raises earlier if tools present)
        return tools

    def build_kwargs(self, model, messages, tools=None, **params) -> dict[str, Any]:
        """Build kwargs for the MoA call.

        Extracts the preset name from the model string ("moa/default" → "default").
        """
        preset_name = "default"
        if model and "/" in model:
            preset_name = model.split("/", 1)[1] or "default"

        return {
            "model": model,
            "messages": messages,
            "tools": tools,
            "_moa_preset_name": preset_name,
            "_session_id": params.get("session_id", ""),
            **{k: v for k, v in params.items() if not k.startswith("_moa")},
        }

    def normalize_response(self, response, **kwargs) -> NormalizedResponse:
        """Normalize a MoA response into the standard form."""
        content = ""
        finish_reason = "stop"

        try:
            choices = getattr(response, "choices", [])
            if choices:
                msg = getattr(choices[0], "message", None)
                if msg:
                    content = getattr(msg, "content", "") or ""
                    if getattr(msg, "tool_calls", None):
                        finish_reason = "tool_calls"
        except Exception:
            content = str(response) if response else ""

        return NormalizedResponse(
            content=content,
            tool_calls=[],
            finish_reason=finish_reason,
            reasoning="",
            usage=None,
            provider_data={
                "moa_total_ms": getattr(response, "_moa_total_ms", 0),
                "moa_ref_count": len(getattr(response, "_moa_ref_results", [])),
            },
        )


register_transport("moa", MoaTransport)
