import asyncio
from typing import cast

import pytest
from acp.client import ClientSideConnection

from flowfield.adapters.claude_cleanup import CAPABILITY, quiesce, require_cleanup
from flowfield.errors import ApplicationError

VALID = {
    **CAPABILITY,
    "sessionId": "owned-session",
    "status": "confirmed",
    "reason": None,
    "checkedNativeOwners": 1,
    "stoppedTasks": 3,
    "quietObservations": 2,
    "nativeOwnerExited": True,
}
CAPABILITIES = {"_meta": {"flowfield.cleanup": CAPABILITY}}


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"sessionId": "another-session"},
        {"version": True},
        {"scope": "native-turns-and-terminals"},
        {"status": "uncertain"},
        {"reason": "timeout"},
        {"checkedNativeOwners": 0},
        {"checkedNativeOwners": True},
        {"quietObservations": 1},
        {"nativeOwnerExited": False},
        {"nativeOwnerExited": 1},
        {"stoppedTasks": 257},
        {"untrustedExtra": "private data"},
    ],
)
def test_only_exact_observed_owner_receipt_is_positive(change):
    class Peer:
        async def ext_method(self, method, params):
            assert method == "flowfield/quiesce"
            assert params == {"sessionId": "owned-session"}
            return {**VALID, **change}

    actual = asyncio.run(quiesce(cast(ClientSideConnection, Peer()), "owned-session", CAPABILITIES))
    assert actual is (not change)


@pytest.mark.parametrize(
    "capabilities",
    [{}, {"_meta": None}, {"_meta": {"flowfield.cleanup": {**CAPABILITY, "version": True}}}],
)
def test_unnegotiated_cleanup_is_rejected(capabilities):
    with pytest.raises(ApplicationError) as error:
        require_cleanup(capabilities)
    assert error.value.code == "cleanup_unavailable"
