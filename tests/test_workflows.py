from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> dict:
    with (WORKFLOWS / name).open(encoding="utf-8") as stream:
        return yaml.load(stream, Loader=yaml.BaseLoader)


def test_ci_is_reusable_and_direct_triggers_stay_on_dev() -> None:
    workflow = _workflow("ci.yml")
    triggers = workflow["on"]

    assert set(triggers) == {"push", "pull_request", "workflow_call"}
    assert triggers["push"]["branches"] == ["dev"]
    assert triggers["pull_request"]["branches"] == ["dev"]
    assert triggers["workflow_call"] == {}
    assert "master" not in triggers["push"]["branches"]


def test_reusable_ci_preserves_the_existing_validation_steps() -> None:
    workflow = _workflow("ci.yml")
    steps = workflow["jobs"]["lint-and-test"]["steps"]
    names = [step.get("name") for step in steps if "name" in step]

    assert names == [
        "Install uv",
        "Set up Python",
        "Sync dependencies (dev extras)",
        "Ruff check",
        "Ruff format check",
        "Run migrations against test DB",
        "Pytest",
    ]
    assert steps[0]["uses"] == "actions/checkout@v4"


def test_deploy_runs_reusable_ci_before_the_coolify_job() -> None:
    workflow = _workflow("deploy.yml")
    triggers = workflow["on"]
    jobs = workflow["jobs"]

    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"]["branches"] == ["master"]
    assert jobs["ci"] == {"uses": "./.github/workflows/ci.yml"}
    assert jobs["deploy"]["needs"] == ["release-ref", "ci"]
    assert jobs["deploy"]["environment"] == "production"
    assert jobs["deploy"]["runs-on"] == "self-hosted"


def test_manual_deploy_requires_the_master_release_ref() -> None:
    workflow = _workflow("deploy.yml")
    guard = workflow["jobs"]["release-ref"]

    assert guard["runs-on"] == "ubuntu-latest"
    assert len(guard["steps"]) == 1
    script = guard["steps"][0]["run"]
    assert 'GITHUB_REF" != "refs/heads/master' in script
    assert "exit 1" in script


def test_deploy_preserves_coolify_queue_and_polling_contract() -> None:
    workflow = _workflow("deploy.yml")
    script = workflow["jobs"]["deploy"]["steps"][0]["run"]

    assert "App\\Models\\Application::find(1)" in script
    assert "queue_application_deployment" in script
    assert "commit: '${{ github.sha }}'" in script
    assert "for i in $(seq 1 60)" in script
    assert "sleep 10" in script
    assert '[ "$STATUS" = "finished" ]' in script
    assert '[ "$STATUS" = "failed" ]' in script
    assert '[ "$STATUS" = "cancelled-by-user" ]' in script
    assert "Timed out after 10 minutes" in script


def test_workflows_do_not_guess_a_production_health_url() -> None:
    combined = "\n".join(
        (WORKFLOWS / name).read_text(encoding="utf-8") for name in ("ci.yml", "deploy.yml")
    )

    assert "PRODUCTION_HEALTH_URL" not in combined
    assert "production health" not in combined.lower()
