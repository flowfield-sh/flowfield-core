"""Display attribution, never an authentication or permission boundary."""

from typing import Literal

from flowfield.execution_models import Record

ActorRole = Literal["human", "coordinator", "worker", "reviewer", "flowfield", "other"]
ACTOR_ROLES: dict[str, ActorRole] = {
    "human": "human",
    "coordinator": "coordinator",
    "agent": "coordinator",
    "worker": "worker",
    "reviewer": "reviewer",
    "flowfield": "flowfield",
    "service": "flowfield",
}
ACTOR_LABELS = {
    "human": "You",
    "coordinator": "Coordinator",
    "worker": "Worker",
    "reviewer": "Reviewer",
    "flowfield": "Flowfield",
}


class Actor(Record):
    role: ActorRole
    label: str
    identity: str


def actor(identity: str) -> Actor:
    # Coordinator tools use agent as their author identity. Managed attempts use worker
    # or worker:<attempt>; keep the original identity rather than rewriting evidence.
    role = ACTOR_ROLES.get(identity, "other")
    if identity.startswith("worker:"):
        role = "worker"
    elif identity.startswith("coordinator:"):
        role = "coordinator"
    return Actor(role=role, label=ACTOR_LABELS.get(role, identity), identity=identity)
