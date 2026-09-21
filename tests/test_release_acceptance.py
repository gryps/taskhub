import json
import subprocess
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from taskhub_v2.api.app import create_app
from taskhub_v2.artifacts import ArtifactStore
from taskhub_v2.config import Settings
from taskhub_v2.domain.models import CodeChangeSummary, ModelResult, ProjectDefinition
from taskhub_v2.execution import LocalTestScheduler
from taskhub_v2.git import GitWorkspaceManager
from taskhub_v2.projects import ProjectRegistry
from taskhub_v2.workers.git_coder import GitCodingWorker
from taskhub_v2.workers.publisher import GitPublisher
from tests.fakes import RecordingProvider


def git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class AcceptanceCoder:
    async def modify(self, requirement, plan, workdir, **options):
        Path(workdir, "delivery.txt").write_text(
            "TASKHUB_RELEASE_ACCEPTANCE_OK\n",
            encoding="utf-8",
        )
        return ModelResult(
            content=CodeChangeSummary(
                summary="Create the verified release acceptance artifact",
                tests=[],
            ),
            provider="release-acceptance",
            model="deterministic-coder",
            usage={"coding_node": "seed-local-acceptance"},
        )


def prepare_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    authority = tmp_path / "authority.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(authority)],
        check=True,
        capture_output=True,
    )
    controller = tmp_path / "controller"
    controller.mkdir()
    git(controller, "init", "-b", "main")
    git(controller, "config", "user.name", "TaskHub Acceptance")
    git(controller, "config", "user.email", "acceptance@taskhub.local")
    (controller / "README.md").write_text("# Release acceptance\n", encoding="utf-8")
    git(controller, "add", "README.md")
    git(controller, "commit", "-m", "Initialize acceptance project")
    git(controller, "remote", "add", "origin", str(authority))
    git(controller, "push", "-u", "origin", "main")

    projects_file = tmp_path / "projects.json"
    registry = ProjectRegistry(str(projects_file))
    registry.add(
        ProjectDefinition(
            id="release-acceptance",
            name="Release acceptance",
            repository=str(controller),
            authority_remote="origin",
            test_commands=[
                [
                    "python3",
                    "-c",
                    (
                        "from pathlib import Path; "
                        "assert Path('delivery.txt').read_text() == "
                        "'TASKHUB_RELEASE_ACCEPTANCE_OK\\n'"
                    ),
                ]
            ],
        )
    )
    return authority, controller, projects_file


def build_settings(tmp_path: Path, projects_file: Path, postgres_dsn: str) -> Settings:
    return Settings(
        checkpointer="postgres",
        postgres_dsn=postgres_dsn,
        provider="deterministic",
        worker_mode="git",
        test_runner="local",
        admin_token="release-acceptance-token",
        session_secret="release-acceptance-session",
        config_encryption_key=Fernet.generate_key().decode(),
        projects_file=str(projects_file),
        workspace_root=str(tmp_path / "workspaces"),
        artifact_root=str(tmp_path / "artifacts"),
        provider_health_file=str(tmp_path / "provider-health.json"),
        nodes_file=str(tmp_path / "nodes.json"),
        node_state_file=str(tmp_path / "node-state.json"),
        operations_log_file=str(tmp_path / "operations.jsonl"),
    )


def install_real_delivery_components(app_module, settings: Settings, monkeypatch) -> None:
    provider = RecordingProvider()
    scheduler = LocalTestScheduler()
    worker = GitCodingWorker(
        ProjectRegistry(settings.projects_file),
        GitWorkspaceManager(settings.workspace_root),
        AcceptanceCoder(),
        ArtifactStore(settings.artifact_root),
        scheduler,
    )
    publisher = GitPublisher(
        ProjectRegistry(settings.projects_file),
        settings.workspace_root,
        scheduler,
    )
    monkeypatch.setattr(app_module, "build_provider", lambda *args: provider)
    monkeypatch.setattr(app_module, "build_worker", lambda *args, **kwargs: worker)
    monkeypatch.setattr(app_module, "build_publisher", lambda *args, **kwargs: publisher)


def test_real_git_delivery_publishes_and_survives_controller_restart(
    monkeypatch,
    postgres_dsn,
    tmp_path: Path,
):
    import taskhub_v2.api.app as app_module

    authority, _controller, projects_file = prepare_project(tmp_path)
    settings = build_settings(tmp_path, projects_file, postgres_dsn)
    install_real_delivery_components(app_module, settings, monkeypatch)

    with TestClient(create_app(settings)) as client:
        login = client.post(
            "/api/auth/login",
            json={"token": "release-acceptance-token"},
        )
        response = client.post(
            "/api/runs",
            headers={"X-CSRF-Token": login.cookies["taskhub_v2_csrf"]},
            json={
                "project_id": "release-acceptance",
                "requirement": "Create the release acceptance artifact",
            },
        )
        assert response.status_code == 201
        run = response.json()
        assert run["status"] == "completed"
        assert run["stage"] == "completed"
        assert run["pending_action"] is None
        run_id = run["run_id"]

        history = client.get(f"/api/runs/{run_id}/history")
        assert history.status_code == 200
        history_text = json.dumps(history.json())
        assert "publication" in history_text
        assert run["implementation"]["coding_node"] == "seed-local-acceptance"

    published = subprocess.run(
        ["git", "--git-dir", str(authority), "show", "main:delivery.txt"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert published == "TASKHUB_RELEASE_ACCEPTANCE_OK\n"
    published_commit = subprocess.run(
        ["git", "--git-dir", str(authority), "rev-parse", "main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # A fresh application instance must read the same completed checkpoint and
    # must not create another commit or rerun the coding worker.
    with TestClient(create_app(settings)) as restarted:
        login = restarted.post(
            "/api/auth/login",
            json={"token": "release-acceptance-token"},
        )
        restored = restarted.get(f"/api/runs/{run_id}")
        assert restored.status_code == 200
        assert restored.json()["status"] == "completed"
        assert login.status_code == 200

    current_commit = subprocess.run(
        ["git", "--git-dir", str(authority), "rev-parse", "main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert current_commit == published_commit
