"""E2E tests for automation blueprints — catalog, instantiation, traceability (HP-304h)."""

from __future__ import annotations

import pytest


class TestBlueprintCatalog:
    """Built-in catalog integrity."""

    def test_builtin_blueprints_loaded(self):
        from cron.blueprint_catalog import load_catalog
        catalog = load_catalog()
        assert len(catalog) >= 5, f"Expected >=5 built-in blueprints, got {len(catalog)}"

    def test_builtin_ids_are_kebab_case(self):
        from cron.blueprint_catalog import load_catalog
        for bp in load_catalog():
            assert " " not in bp["id"], f"Blueprint '{bp['id']}' has spaces"
            assert "/" not in bp["id"], f"Blueprint '{bp['id']}' has slashes"

    def test_builtin_have_required_fields(self):
        from cron.blueprint_catalog import load_catalog
        for bp in load_catalog():
            assert bp.get("id"), f"Missing id in blueprint"
            assert bp.get("name"), f"Missing name in {bp.get('id')}"
            assert bp.get("prompt_template"), f"Missing prompt_template in {bp['id']}"

    def test_find_blueprint_existing(self):
        from cron.blueprint_catalog import find_blueprint
        bp = find_blueprint("daily-standup")
        assert bp is not None
        assert bp["name"] == "Daily Standup Summary"

    def test_find_blueprint_missing(self):
        from cron.blueprint_catalog import find_blueprint
        assert find_blueprint("nonexistent-blueprint") is None

    def test_catalog_is_sorted_by_category_then_name(self):
        from cron.blueprint_catalog import load_catalog
        catalog = load_catalog()
        categories = [bp.get("category", "") for bp in catalog]
        assert categories == sorted(categories)

    def test_user_blueprints_override_builtin(self, tmp_path, monkeypatch):
        """User YAML files override built-in blueprints by id."""
        import yaml
        from cron.blueprint_catalog import _load_user_catalog, _user_catalog_dir

        user_dir = tmp_path / "blueprints"
        user_dir.mkdir()
        # Override built-in daily-standup
        override = {
            "id": "daily-standup",
            "name": "Custom Standup",
            "description": "Overridden",
            "prompt_template": "custom template",
        }
        (user_dir / "daily-standup.yaml").write_text(
            yaml.dump(override), encoding="utf-8"
        )

        monkeypatch.setattr(
            "cron.blueprint_catalog._user_catalog_dir", lambda: user_dir
        )
        # Clear caches
        _load_user_catalog.cache_clear()
        from cron.blueprint_catalog import _load_builtin
        _load_builtin.cache_clear()

        from cron.blueprint_catalog import load_catalog
        catalog = {bp["id"]: bp for bp in load_catalog()}
        assert catalog["daily-standup"]["name"] == "Custom Standup"


class TestBlueprintInstantiation:
    """E2E: instantiate_blueprint → cron job → blueprint_id traceability."""

    @pytest.fixture(autouse=True)
    def _setup_jobs_dir(self, tmp_path, monkeypatch):
        """Redirect cron storage to tmp_path for all instantiation tests."""
        import cron.jobs as _cj

        jobs_dir = tmp_path / "cron"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / "output").mkdir(parents=True, exist_ok=True)
        jobs_file = jobs_dir / "jobs.json"
        jobs_file.write_text('{"jobs": [], "updated_at": ""}', encoding="utf-8")

        monkeypatch.setattr(_cj, "intellect_DIR", tmp_path)
        monkeypatch.setattr(_cj, "CRON_DIR", jobs_dir)
        monkeypatch.setattr(_cj, "JOBS_FILE", jobs_file)
        monkeypatch.setattr(_cj, "OUTPUT_DIR", jobs_dir / "output")

    def test_instantiate_creates_job_with_blueprint_id(self):
        """Instantiate a blueprint and verify the job has blueprint_id."""
        from tools.blueprints import instantiate_blueprint
        result = instantiate_blueprint("daily-standup")
        assert result.get("ok"), f"Instantiation failed: {result}"
        job = result.get("job", {})
        assert job.get("blueprint_id") == "daily-standup"

    def test_instantiate_with_params_substitutes_template(self):
        """Template variables are substituted into the prompt."""
        from tools.blueprints import instantiate_blueprint
        result = instantiate_blueprint("site-monitor", params={"url": "https://example.com"})
        assert result.get("ok"), f"Instantiation failed: {result}"
        prompt = result["job"]["prompt"]
        assert "https://example.com" in prompt
        assert "{{url}}" not in prompt

    def test_instantiate_missing_required_param_errors(self):
        """Missing required params (no default) should return error."""
        from tools.blueprints import instantiate_blueprint
        result = instantiate_blueprint("site-monitor")
        assert "error" in result
        assert "url" in result["error"].lower()

    def test_instantiate_missing_blueprint_errors(self):
        from tools.blueprints import instantiate_blueprint
        result = instantiate_blueprint("nonexistent")
        assert "error" in result

    def test_instantiate_with_schedule_override(self):
        from tools.blueprints import instantiate_blueprint
        result = instantiate_blueprint(
            "daily-standup", schedule_override="0 12 * * *", name="Custom Standup"
        )
        assert result.get("ok"), f"Instantiation failed: {result}"
        assert result["job"]["name"] == "Custom Standup"
        assert result["job"]["schedule"]["display"] == "0 12 * * *"
