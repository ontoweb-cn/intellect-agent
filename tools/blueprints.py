"""Agent tools for automation blueprints — search, instantiate (HP-304).

These functions are registered under the ``cronjob`` toolset so agents
can discover and instantiate blueprints through normal tool calls.
"""

from __future__ import annotations

from typing import Any


def list_blueprints() -> dict[str, Any]:
    """List all available blueprints."""
    from cron.blueprint_catalog import load_catalog

    catalog = load_catalog()
    return {
        "blueprints": [
            {
                "id": bp["id"],
                "name": bp["name"],
                "description": bp.get("description", ""),
                "category": bp.get("category", "general"),
            }
            for bp in catalog
        ]
    }


def blueprint_detail(blueprint_id: str) -> dict[str, Any]:
    """Get details of a specific blueprint including parameters."""
    from cron.blueprint_catalog import find_blueprint

    bp = find_blueprint(blueprint_id)
    if not bp:
        return {"error": f"Blueprint '{blueprint_id}' not found"}
    return {
        "id": bp["id"],
        "name": bp["name"],
        "description": bp.get("description", ""),
        "category": bp.get("category", "general"),
        "schedule": bp.get("schedule", ""),
        "params": bp.get("params", {}),
        "skills": bp.get("skills", []),
    }


def instantiate_blueprint(
    blueprint_id: str,
    params: dict[str, Any] | None = None,
    schedule_override: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Create a cron job from a blueprint."""
    import re

    from cron.blueprint_catalog import find_blueprint
    from cron.jobs import create_job

    bp = find_blueprint(blueprint_id)
    if not bp:
        return {"error": f"Blueprint '{blueprint_id}' not found"}

    params = params or {}

    # Validate params against schema via Rust validator
    if bp.get("params"):
        import json as _json
        import yaml as _yaml
        try:
            from intellect_rust import rust_validate_blueprint_params
        except (ImportError, AttributeError):
            rust_validate_blueprint_params = None  # type: ignore[assignment]

        if rust_validate_blueprint_params is not None:
            bp_yaml = _yaml.dump(bp)
            err = rust_validate_blueprint_params(bp_yaml, _json.dumps(params))
            if err:
                return {"error": err}

    # Substitute params into prompt template
    prompt = bp["prompt_template"]
    for key, value in params.items():
        prompt = prompt.replace("{{" + key + "}}", str(value))
    for key, param_def in bp.get("params", {}).items():
        if key not in params and param_def.get("default") is not None:
            prompt = prompt.replace("{{" + key + "}}", str(param_def["default"]))

    # Check for unresolved template vars
    unresolved = re.findall(r"\{\{(\w+)\}\}", prompt)
    if unresolved:
        return {
            "error": f"Missing required parameters: {', '.join(unresolved)}"
        }

    schedule = schedule_override or bp.get("schedule", "0 9 * * *")

    job = create_job(
        prompt=prompt,
        schedule=schedule,
        name=name or bp["name"],
        skills=bp.get("skills", []),
        deliver=bp.get("delivery", "origin"),
        blueprint_id=blueprint_id,
    )

    return {"ok": True, "job": job, "blueprint": blueprint_id}
