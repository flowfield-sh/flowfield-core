"""Durable milestone branch ownership and effective task destinations."""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from flowfield.adapters import git_checkout
from flowfield.adapters import git_integration as gitops
from flowfield.adapters.git_workspace import git
from flowfield.application import Workspace, now
from flowfield.delivery_models import (
    DeliveryConfigure,
    MilestoneDelivery,
    TaskIntegrationPolicy,
    TaskIntegrationPolicyEdit,
    TaskIntegrationPolicyView,
    WorkspaceIntegrationPolicy,
    WorkspaceIntegrationPolicyEdit,
)
from flowfield.errors import ApplicationError
from flowfield.milestones import Milestones
from flowfield.workspaces import Workspaces


def workspace_policy(db: sqlite3.Connection, workspace_id: str) -> WorkspaceIntegrationPolicy:
    Workspaces.read(db, workspace_id)
    row = db.execute(
        "SELECT data FROM workspace_integration_policies WHERE workspace_id=? "
        "ORDER BY revision DESC LIMIT 1",
        (workspace_id,),
    ).fetchone()
    return (
        WorkspaceIntegrationPolicy.model_validate_json(row[0])
        if row
        else WorkspaceIntegrationPolicy()
    )


def integration_enabled(db: sqlite3.Connection, delivery: MilestoneDelivery) -> bool:
    override = delivery.integration_policy.enabled
    return override if override is not None else workspace_policy(db, delivery.workspace_id).enabled


def for_milestone(
    db: sqlite3.Connection, project_id: str, milestone_id: str | None
) -> MilestoneDelivery | None:
    if not milestone_id:
        return None
    row = db.execute(
        "SELECT d.data FROM milestone_deliveries d JOIN workspace_scopes w "
        "ON w.workspace_id=d.workspace_id WHERE w.project_id=? AND d.milestone_id=?",
        (project_id, milestone_id),
    ).fetchone()
    if not row:
        return None
    value = MilestoneDelivery.model_validate_json(row[0])
    if value.project_id != project_id:
        raise ApplicationError(
            "milestone_repository_mismatch",
            "This milestone delivers one repository. Use its project for contributing tasks.",
            409,
        )
    if value.status != "ready":
        raise ApplicationError(
            "milestone_destination_unavailable",
            value.problem or "Milestone checkout creation is incomplete; retry configuration.",
            409,
        )
    return value


def effective_branch(
    db: sqlite3.Connection, project_id: str, milestone_id: str | None
) -> str | None:
    delivery = for_milestone(db, project_id, milestone_id)
    if delivery:
        return delivery.branch
    settings = db.execute(
        "SELECT data FROM integration_settings WHERE project_id=?", (project_id,)
    ).fetchone()
    return json.loads(settings[0]).get("target_branch") if settings else None


class MilestoneDeliveries:
    def __init__(self, store: Workspace):
        self.store = store

    def workspace_policy(self, workspace_id: str) -> WorkspaceIntegrationPolicy:
        with self.store.connection() as db:
            return workspace_policy(db, workspace_id)

    def configure_workspace_policy(
        self, workspace_id: str, request: WorkspaceIntegrationPolicyEdit
    ) -> WorkspaceIntegrationPolicy:
        with self.store.connection(write=True) as db:
            previous = workspace_policy(db, workspace_id)
            self.store._current(previous.revision, request.expected_revision)
            value = WorkspaceIntegrationPolicy(
                revision=previous.revision + 1, enabled=request.enabled, updated_at=now()
            )
            db.execute(
                "INSERT INTO workspace_integration_policies VALUES (?,?,?)",
                (workspace_id, value.revision, value.model_dump_json()),
            )
            return value

    def policy_view(self, workspace_id: str, milestone_id: str) -> TaskIntegrationPolicyView:
        with self.store.connection() as db:
            milestone = Milestones.read(db, workspace_id, milestone_id)
            row = db.execute(
                "SELECT data FROM milestone_deliveries WHERE workspace_id=? AND milestone_id=?",
                (workspace_id, milestone.id),
            ).fetchone()
            if not row:
                raise ApplicationError(
                    "delivery_required", "Prepare an owned milestone branch first.", 409
                )
            delivery = MilestoneDelivery.model_validate_json(row[0])
            defaults = workspace_policy(db, workspace_id)
            return TaskIntegrationPolicyView(
                **delivery.integration_policy.model_dump(),
                delivery_id=delivery.id,
                branch=delivery.branch,
                effective_enabled=integration_enabled(db, delivery),
                workspace_revision=defaults.revision,
            )

    def integration_policy(
        self, workspace_id: str, milestone_id: str, request: TaskIntegrationPolicyEdit
    ) -> MilestoneDelivery:
        """Human settings entry; deliberately absent from coordinator/scoped tools."""
        with self.store.connection(write=True) as db:
            milestone = Milestones.read(db, workspace_id, milestone_id)
            row = db.execute(
                "SELECT data FROM milestone_deliveries WHERE workspace_id=? AND milestone_id=?",
                (workspace_id, milestone.id),
            ).fetchone()
            if not row:
                raise ApplicationError(
                    "delivery_required", "Prepare an owned milestone branch first.", 409
                )
            value = MilestoneDelivery.model_validate_json(row[0])
            self.store._current(value.integration_policy.revision, request.expected_revision)
            if request.expected_workspace_revision is not None:
                self.store._current(
                    workspace_policy(db, workspace_id).revision, request.expected_workspace_revision
                )
            if (
                value.id != request.delivery_id
                or value.branch != request.branch
                or value.status != "ready"
            ):
                raise ApplicationError(
                    "delivery_changed", "Inspect the current owned destination first.", 409
                )
            value.integration_policy = TaskIntegrationPolicy(
                revision=value.integration_policy.revision + 1,
                enabled=request.enabled,
                updated_at=now(),
            )
            db.execute(
                "INSERT INTO milestone_integration_policy_history VALUES (?,?,?)",
                (
                    value.id,
                    value.integration_policy.revision,
                    value.integration_policy.model_dump_json(),
                ),
            )
            db.execute(
                "UPDATE milestone_deliveries SET data=? WHERE id=?",
                (value.model_dump_json(), value.id),
            )
            return value

    def get(self, workspace_id: str, milestone_id: str) -> MilestoneDelivery | None:
        with self.store.connection() as db:
            milestone = Milestones.read(db, workspace_id, milestone_id)
            row = db.execute(
                "SELECT data FROM milestone_deliveries WHERE workspace_id=? AND milestone_id=?",
                (workspace_id, milestone.id),
            ).fetchone()
            return MilestoneDelivery.model_validate_json(row[0]) if row else None

    def _save(self, value: MilestoneDelivery) -> MilestoneDelivery:
        with self.store.connection(write=True) as db:
            db.execute(
                "UPDATE milestone_deliveries SET data=? WHERE id=?",
                (value.model_dump_json(), value.id),
            )
        return value

    def restart(self) -> None:
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT data FROM milestone_deliveries "
                "WHERE json_extract(data,'$.status')='creating'"
            ).fetchall()
        for row in rows:
            value = MilestoneDelivery.model_validate_json(row[0])
            with self.store.connection() as db:
                milestone = Milestones.read(db, value.workspace_id, value.milestone_id)
            self.configure(
                value.workspace_id,
                value.milestone_id,
                DeliveryConfigure(
                    expected_revision=milestone.revision,
                    project_id=value.project_id,
                    branch=value.branch,
                ),
            )

    def configure(
        self, workspace_id: str, milestone_id: str, request: DeliveryConfigure
    ) -> MilestoneDelivery:
        repository = Path(self.store.project(request.project_id).path)
        with gitops.lock(repository):
            with self.store.connection(write=True) as db:
                milestone = Milestones.read(db, workspace_id, milestone_id)
                self.store._current(milestone.revision, request.expected_revision)
                Workspaces.check_project(db, workspace_id, request.project_id)
                row = db.execute(
                    "SELECT data FROM milestone_deliveries WHERE workspace_id=? AND milestone_id=?",
                    (workspace_id, milestone.id),
                ).fetchone()
                if row:
                    value = MilestoneDelivery.model_validate_json(row[0])
                    if value.project_id != request.project_id:
                        raise ApplicationError(
                            "delivery_exists",
                            "This milestone already owns a different destination.",
                            409,
                        )
                    if request.branch and value.branch != request.branch:
                        existing_marker = git(
                            repository,
                            "rev-parse",
                            "--verify",
                            "--quiet",
                            f"refs/flowfield/milestones/{value.id}",
                            allowed_returncodes=(0, 1),
                        ).strip()
                        if value.status == "ready" or existing_marker or Path(value.path).exists():
                            raise ApplicationError(
                                "delivery_exists", "This milestone already owns a destination.", 409
                            )
                        # A collision before any Git ownership was acquired can
                        # choose another name; never delete the colliding branch.
                        gitops.branch_ref(repository, request.branch)
                        value.branch, value.status, value.problem = request.branch, "creating", None
                        db.execute(
                            "UPDATE milestone_deliveries SET data=? WHERE id=?",
                            (value.model_dump_json(), value.id),
                        )
                    if value.status == "ready":
                        return value
                else:
                    tasks = db.execute(
                        "SELECT t.project_id,t.id,t.data FROM tasks t JOIN workspace_scopes w "
                        "ON w.project_id=t.project_id WHERE w.workspace_id=? "
                        "AND json_extract(t.data,'$.milestone_id')=?",
                        (workspace_id, milestone.id),
                    ).fetchall()
                    for task in tasks:
                        data = json.loads(task["data"])
                        if (
                            task["project_id"] != request.project_id
                            or data.get("publication")
                            or db.execute(
                                "SELECT 1 FROM runs WHERE project_id=? AND task_id=?",
                                (task["project_id"], task["id"]),
                            ).fetchone()
                        ):
                            raise ApplicationError(
                                "delivery_reconciliation_required",
                                "Configure milestone delivery before preparing its tasks. "
                                "Existing work keeps its original destination.",
                                409,
                            )
                    destination = effective_branch(db, request.project_id, None)
                    if not destination:
                        raise ApplicationError(
                            "integration_target_required",
                            "Configure the project destination and checks first.",
                            409,
                        )
                    identity = uuid4().hex
                    value = MilestoneDelivery(
                        id=identity,
                        workspace_id=workspace_id,
                        milestone_id=milestone.id,
                        project_id=request.project_id,
                        branch=request.branch
                        or f"flowfield/{milestone.key.lower()}-{identity[:8]}",
                        destination_branch=destination,
                        base_commit=gitops.target(repository, destination),
                        path=str(self.store.directory / "milestone-work" / identity),
                        created_at=now(),
                    )
                    gitops.branch_ref(repository, value.branch)
                    db.execute(
                        "INSERT INTO milestone_deliveries VALUES (?,?,?,?,?)",
                        (
                            identity,
                            workspace_id,
                            milestone.id,
                            request.project_id,
                            value.model_dump_json(),
                        ),
                    )
            # The ownership marker and branch are created in one Git transaction.
            # A crash can resume only this recorded intent, never adopt an existing branch.
            try:
                marker = f"refs/flowfield/milestones/{value.id}"
                owned = (
                    git(
                        repository,
                        "rev-parse",
                        "--verify",
                        "--quiet",
                        marker,
                        allowed_returncodes=(0, 1),
                    )
                    .decode()
                    .strip()
                )
                path = Path(value.path)
                if not owned:
                    if path.exists():
                        raise ApplicationError(
                            "milestone_checkout_collision",
                            "Milestone checkout path already exists; no files were adopted.",
                            409,
                        )
                    git(
                        repository,
                        "update-ref",
                        "--stdin",
                        input=(
                            f"start\ncreate {marker} {value.base_commit}\n"
                            f"create refs/heads/{value.branch} {value.base_commit}\n"
                            "prepare\ncommit\n"
                        ).encode(),
                    )
                elif owned != value.base_commit:
                    raise ApplicationError(
                        "milestone_ownership_changed",
                        "Milestone ownership reference changed; inspect the repository.",
                        409,
                    )
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    git(
                        repository,
                        "-c",
                        "core.hooksPath=/dev/null",
                        "worktree",
                        "add",
                        str(path),
                        value.branch,
                    )
                binding = git_checkout.binding(path)

                def common(p: Path) -> bytes:
                    return git(p, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()

                if common(path) != common(repository):
                    raise ApplicationError(
                        "milestone_checkout_collision",
                        "The milestone path belongs to a different repository.",
                        409,
                    )
                git_checkout.verify(path, value.branch, value.base_commit, binding)
                value.checkout, value.status, value.problem = binding, "ready", None
            except (ApplicationError, OSError) as error:
                value.status, value.problem = "blocked", str(error)
            return self._save(value)
