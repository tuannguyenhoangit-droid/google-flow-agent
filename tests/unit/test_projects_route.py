"""The project DELETE route's Flow-side gate.

The route reaches through to Google Flow, so these lock down when it does and,
more importantly, when it must not: never for a project it does not already
track, never for the pinned shared project, and never leaving local and remote
out of step when the remote call fails.
"""
import pytest

from agent.api import projects


class FakeRepo:
    def __init__(self, exists=True):
        self._exists = exists
        self.deleted = []

    async def get_project(self, pid):
        return {"id": pid} if self._exists else None

    async def delete_project(self, pid):
        self.deleted.append(pid)
        return True


class FakeClient:
    def __init__(self, connected=True, error=None):
        self.connected = connected
        self._error = error
        self.deleted = []

    async def delete_project(self, pid):
        self.deleted.append(pid)
        if self._error:
            return {"error": self._error}
        return {"status": 200, "data": {"projectId": pid}}


@pytest.fixture
def wire(monkeypatch):
    """Point the route at fake collaborators; batch path with a pinned project."""
    def _wire(*, repo, client, batch=True, pin="pinned-project"):
        monkeypatch.setattr(projects, "USE_BATCH_RPC", batch)
        monkeypatch.setattr(projects, "FLOW_PROJECT_ID", pin)
        monkeypatch.setattr(projects, "_get_repo", lambda: repo)
        monkeypatch.setattr(projects, "get_flow_client", lambda: client)
    return _wire


async def test_missing_project_is_404_before_any_remote_call(wire):
    repo, client = FakeRepo(exists=False), FakeClient()
    wire(repo=repo, client=client)
    with pytest.raises(projects.HTTPException) as exc:
        await projects.delete("nope")
    assert exc.value.status_code == 404
    assert client.deleted == [], "a stray id must not reach Flow"
    assert repo.deleted == []


async def test_pinned_project_is_removed_locally_only(wire):
    repo, client = FakeRepo(), FakeClient()
    wire(repo=repo, client=client, pin="pinned-project")
    result = await projects.delete("pinned-project")
    assert result == {"ok": True}
    assert client.deleted == [], "the shared project is never deleted on Flow"
    assert repo.deleted == ["pinned-project"]


async def test_tracked_project_is_deleted_remote_first_then_local(wire):
    repo, client = FakeRepo(), FakeClient()
    wire(repo=repo, client=client)
    result = await projects.delete("proj-1")
    assert result == {"ok": True}
    assert client.deleted == ["proj-1"]
    assert repo.deleted == ["proj-1"]


async def test_remote_failure_keeps_the_local_row(wire):
    repo, client = FakeRepo(), FakeClient(error="NO_FLOW_TAB")
    wire(repo=repo, client=client)
    with pytest.raises(projects.HTTPException) as exc:
        await projects.delete("proj-1")
    assert exc.value.status_code == 502
    assert repo.deleted == [], "local row stays put so the two do not drift"


async def test_offline_extension_deletes_nothing(wire):
    repo, client = FakeRepo(), FakeClient(connected=False)
    wire(repo=repo, client=client)
    with pytest.raises(projects.HTTPException) as exc:
        await projects.delete("proj-1")
    assert exc.value.status_code == 503
    assert client.deleted == []
    assert repo.deleted == []


async def test_legacy_path_deletes_locally_without_touching_flow(wire):
    repo, client = FakeRepo(), FakeClient()
    wire(repo=repo, client=client, batch=False)
    result = await projects.delete("proj-1")
    assert result == {"ok": True}
    assert client.deleted == []
    assert repo.deleted == ["proj-1"]
