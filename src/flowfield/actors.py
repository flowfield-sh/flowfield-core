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
    "other": "Other",
}


class Actor(Record):
    role: ActorRole
    label: str
    identity: str


def actor(identity: str) -> Actor:
    # Coordinator tools use agent as their author identity. Managed attempts use worker
    # or worker:<attempt>; keep the original identity rather than rewriting evidence.
    role = ACTOR_ROLES.get(identity.split(":", 1)[0], "other")
    return Actor(role=role, label=ACTOR_LABELS[role], identity=identity)
