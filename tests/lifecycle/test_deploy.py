"""Tests for deploy, rollback, and deployment history endpoints."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any

from robotsix_agent_comm.lifecycle import (
    DeploymentStore,
    LifecycleServer,
    MockBackend,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post(
    url: str,
    body: dict[str, Any],
    token: str | None = "test-token",  # noqa: S107
) -> dict[str, Any]:
    """POST JSON to *url* and return the decoded response body."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())  # type: ignore[no-any-return]
    except urllib.error.HTTPError as exc:
        raw_body = exc.read()
        exc.close()
        return json.loads(raw_body)  # type: ignore[no-any-return]


def _get(
    url: str,
    token: str | None = "test-token",  # noqa: S107
) -> dict[str, Any]:
    """GET JSON from *url* and return the decoded response body."""
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())  # type: ignore[no-any-return]
    except urllib.error.HTTPError as exc:
        raw_body = exc.read()
        exc.close()
        return json.loads(raw_body)  # type: ignore[no-any-return]


def _post_raw(
    url: str,
    body: dict[str, Any],
    token: str | None = "test-token",  # noqa: S107
) -> tuple[int, dict[str, Any]]:
    """POST and return (status_code, body_dict)."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw_body = exc.read()
        exc.close()
        return exc.code, json.loads(raw_body)


# ---------------------------------------------------------------------------
# Deploy success
# ---------------------------------------------------------------------------


class TestDeploySuccess:
    def test_deploy_healthy(
        self, base_url: str, backend: MockBackend, store: DeploymentStore
    ) -> None:
        """A deploy that passes health check returns 200 and records HEALTHY."""
        backend._health_results = [True]

        resp = _post(f"{base_url}/services/test-svc/deploy", {"version": "v1.0.0"})
        assert resp["service"] == "test-svc"
        assert resp["version"] == "v1.0.0"
        assert resp["status"] == "HEALTHY"

        rev = store.get_current("test-svc")
        assert rev is not None
        assert rev.version == "v1.0.0"
        assert rev.status == "HEALTHY"
        assert rev.source == "deploy"

    def test_deploy_records_start_call(
        self, base_url: str, backend: MockBackend
    ) -> None:
        """The backend's start() is called with the service name and version."""
        backend._health_results = [True]

        _post(f"{base_url}/services/my-svc/deploy", {"version": "v2.0.0"})
        assert len(backend.start_calls) == 1
        svc, ver = backend.start_calls[0]
        assert svc == "my-svc"
        assert ver == "v2.0.0"

    def test_deploy_health_check_disabled(
        self, backend: MockBackend, store: DeploymentStore
    ) -> None:
        """With health_check_enabled=False, deploy succeeds without health polling."""
        backend._health_results = [False]  # would fail if checked

        srv = LifecycleServer(
            backend=backend,
            store=store,
            host="127.0.0.1",
            port=0,
            auth_token="test-token",
            health_check_enabled=False,
        )
        srv.start()
        try:
            url = f"http://{srv.host}:{srv.port}"
            resp = _post(f"{url}/services/svc/deploy", {"version": "v3"})
            assert resp["status"] == "HEALTHY"
        finally:
            srv.stop()

    def test_deploy_sets_current_revision(
        self, base_url: str, backend: MockBackend, store: DeploymentStore
    ) -> None:
        """After deploy, get_current() returns the new revision."""
        backend._health_results = [True]

        resp = _post(f"{base_url}/services/svc/deploy", {"version": "v1"})
        rev_id = resp["revision_id"]

        current = store.get_current("svc")
        assert current is not None
        assert current.revision_id == rev_id


# ---------------------------------------------------------------------------
# Deploy auto-rollback
# ---------------------------------------------------------------------------


class TestDeployAutoRollback:
    def test_auto_rollback_on_unhealthy(
        self, base_url: str, backend: MockBackend, store: DeploymentStore
    ) -> None:
        """When health check fails, the server rolls back to the last good revision."""
        # First deploy: healthy.
        backend._health_results = [True]
        _post(f"{base_url}/services/svc/deploy", {"version": "v1"})

        # Second deploy: unhealthy → auto-rollback to v1.
        backend._health_calls = 0  # reset counter
        # With health_interval >= health_timeout, _wait_for_healthy polls
        # exactly once.  So [False, True] means:
        #   call 1 (deploy health check): False → deploy fails
        #   call 2 (rollback health check): True → rollback succeeds
        backend._health_results = [False, True]

        status, resp = _post_raw(
            f"{base_url}/services/svc/deploy", {"version": "v2-bad"}
        )
        assert status == 200
        assert resp["status"] == "HEALTHY"
        assert resp.get("rolled_back") is True
        assert resp["version"] == "v1"

        current = store.get_current("svc")
        assert current is not None
        assert current.version == "v1"
        assert current.source == "rollback"

    def test_auto_rollback_no_previous_revision(
        self, base_url: str, backend: MockBackend
    ) -> None:
        """When the first deploy fails and there's no previous revision, return 502."""
        backend._health_results = [False]

        status, resp = _post_raw(f"{base_url}/services/svc/deploy", {"version": "v1"})
        assert status == 502
        assert "no previous good revision" in resp["error"]

    def test_auto_rollback_records_both_revisions(
        self, base_url: str, backend: MockBackend, store: DeploymentStore
    ) -> None:
        """Auto-rollback records the failed deploy AND the rollback revision."""
        backend._health_results = [True]
        _post(f"{base_url}/services/svc/deploy", {"version": "v1"})

        backend._health_calls = 0
        backend._health_results = [False, True]

        _post(f"{base_url}/services/svc/deploy", {"version": "v2-bad"})

        history = store.get_history("svc")
        assert len(history) == 3  # v1 deploy, v2 deploy (failed), rollback to v1

        sources = [r.source for r in history]
        statuses = [r.status for r in history]
        assert sources == ["deploy", "deploy", "rollback"]
        assert statuses == ["HEALTHY", "UNHEALTHY", "HEALTHY"]

    def test_auto_rollback_rollback_target_unhealthy(
        self, base_url: str, backend: MockBackend, store: DeploymentStore
    ) -> None:
        """When even the rollback target is unhealthy, return 502."""
        # First deploy healthy.
        backend._health_results = [True]
        _post(f"{base_url}/services/svc/deploy", {"version": "v1"})

        # Second deploy: unhealthy, rollback also unhealthy.
        backend._health_calls = 0
        backend._health_results = [False, False]  # both fail

        status, resp = _post_raw(
            f"{base_url}/services/svc/deploy", {"version": "v2-bad"}
        )
        assert status == 502
        assert "rollback target is also unhealthy" in resp["error"]

        # The rollback revision should be recorded as UNHEALTHY.
        history = store.get_history("svc")
        rollback_revs = [r for r in history if r.source == "rollback"]
        assert len(rollback_revs) == 1
        assert rollback_revs[0].status == "UNHEALTHY"


# ---------------------------------------------------------------------------
# Explicit rollback
# ---------------------------------------------------------------------------


class TestExplicitRollback:
    def test_rollback_to_previous(
        self, base_url: str, backend: MockBackend, store: DeploymentStore
    ) -> None:
        """POST /rollback (no body) rolls back to the immediate predecessor."""
        # Deploy v1.
        backend._health_results = [True]
        _post(f"{base_url}/services/svc/deploy", {"version": "v1"})

        # Deploy v2.
        backend._health_results = [True]
        _post(f"{base_url}/services/svc/deploy", {"version": "v2"})

        # Rollback to previous (v1).
        resp = _post(f"{base_url}/services/svc/rollback", {})
        assert resp["status"] == "HEALTHY"
        assert resp["version"] == "v1"

        current = store.get_current("svc")
        assert current is not None
        assert current.version == "v1"
        assert current.source == "rollback"

    def test_rollback_to_explicit_revision(
        self, base_url: str, backend: MockBackend, store: DeploymentStore
    ) -> None:
        """POST /rollback with revision_id rolls back to that specific revision."""
        backend._health_results = [True]
        r1 = _post(f"{base_url}/services/svc/deploy", {"version": "v1"})

        backend._health_results = [True]
        _post(f"{base_url}/services/svc/deploy", {"version": "v2"})

        backend._health_results = [True]
        _post(f"{base_url}/services/svc/deploy", {"version": "v3"})

        # Rollback to v1 explicitly.
        resp = _post(
            f"{base_url}/services/svc/rollback", {"revision_id": r1["revision_id"]}
        )
        assert resp["version"] == "v1"

    def test_rollback_nonexistent_revision(
        self, base_url: str, backend: MockBackend
    ) -> None:
        """Rollback to a nonexistent revision returns 404."""
        backend._health_results = [True]
        _post(f"{base_url}/services/svc/deploy", {"version": "v1"})

        status, resp = _post_raw(
            f"{base_url}/services/svc/rollback", {"revision_id": "nope"}
        )
        assert status == 404

    def test_rollback_no_current_deployment(self, base_url: str) -> None:
        """Rollback with no existing deployment returns 400."""
        status, resp = _post_raw(f"{base_url}/services/svc/rollback", {})
        assert status == 400
        assert "no current deployment" in resp["error"]

    def test_rollback_no_previous_revision(
        self, base_url: str, backend: MockBackend
    ) -> None:
        """Rollback when current has no predecessor returns 400."""
        backend._health_results = [True]
        _post(f"{base_url}/services/svc/deploy", {"version": "v1"})

        status, resp = _post_raw(f"{base_url}/services/svc/rollback", {})
        assert status == 400
        assert "no previous revision" in resp["error"]

    def test_rollback_creates_new_revision(
        self, base_url: str, backend: MockBackend, store: DeploymentStore
    ) -> None:
        """Explicit rollback creates a new revision with source='rollback'."""
        backend._health_results = [True]
        _post(f"{base_url}/services/svc/deploy", {"version": "v1"})

        backend._health_results = [True]
        _post(f"{base_url}/services/svc/deploy", {"version": "v2"})

        resp = _post(f"{base_url}/services/svc/rollback", {})

        # The current should be the rollback revision.
        current = store.get_current("svc")
        assert current is not None
        assert current.revision_id == resp["revision_id"]
        assert current.source == "rollback"

    def test_rollback_starts_target_version(
        self, base_url: str, backend: MockBackend
    ) -> None:
        """Rollback calls backend.start() with the target version."""
        backend._health_results = [True]
        _post(f"{base_url}/services/svc/deploy", {"version": "v1"})

        backend._health_results = [True]
        _post(f"{base_url}/services/svc/deploy", {"version": "v2"})

        backend.start_calls.clear()
        _post(f"{base_url}/services/svc/rollback", {})

        # Last start call should be for v1.
        assert len(backend.start_calls) > 0
        svc, ver = backend.start_calls[-1]
        assert svc == "svc"
        assert ver == "v1"


# ---------------------------------------------------------------------------
# Deployment history
# ---------------------------------------------------------------------------


class TestDeploymentHistory:
    def test_get_deployments_returns_ordered_list(
        self, base_url: str, backend: MockBackend
    ) -> None:
        """GET /deployments returns all revisions ordered oldest-first."""
        backend._health_results = [True]
        _post(f"{base_url}/services/svc/deploy", {"version": "v1"})

        backend._health_results = [True]
        _post(f"{base_url}/services/svc/deploy", {"version": "v2"})

        resp = _get(f"{base_url}/services/svc/deployments")
        assert resp["service"] == "svc"
        deps = resp["deployments"]
        assert len(deps) == 2
        assert deps[0]["version"] == "v1"
        assert deps[1]["version"] == "v2"

    def test_get_deployments_empty(self, base_url: str) -> None:
        """GET /deployments for a service with no history returns empty list."""
        resp = _get(f"{base_url}/services/unknown/deployments")
        assert resp["deployments"] == []

    def test_get_deployments_marks_current(
        self, base_url: str, backend: MockBackend
    ) -> None:
        """The current revision has current=True, others have current=False."""
        backend._health_results = [True]
        _post(f"{base_url}/services/svc/deploy", {"version": "v1"})

        backend._health_results = [True]
        r2 = _post(f"{base_url}/services/svc/deploy", {"version": "v2"})

        resp = _get(f"{base_url}/services/svc/deployments")
        for dep in resp["deployments"]:
            if dep["revision_id"] == r2["revision_id"]:
                assert dep["current"] is True
            else:
                assert dep["current"] is False


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class TestAuth:
    def test_missing_auth_header_returns_401(self, base_url: str) -> None:
        """Requests without Authorization header get 401."""
        data = json.dumps({"version": "v1"}).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/services/svc/deploy", data=data, method="POST"
        )
        req.add_header("Content-Type", "application/json")
        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
            exc.close()
        else:
            raise AssertionError("expected HTTPError 401")

    def test_invalid_token_returns_401(self, base_url: str) -> None:
        """Wrong token returns 401."""
        status, resp = _post_raw(
            f"{base_url}/services/svc/deploy",
            {"version": "v1"},
            token="wrong-token",
        )
        assert status == 401

    def test_no_auth_server_allows_unauthenticated(
        self, base_url_no_auth: str, backend: MockBackend
    ) -> None:
        """When auth_token=None, requests without auth header succeed."""
        backend._health_results = [True]

        data = json.dumps({"version": "v1"}).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url_no_auth}/services/svc/deploy", data=data, method="POST"
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
        assert body["status"] == "HEALTHY"

    def test_health_endpoint_is_unauthenticated(self, base_url: str) -> None:
        """GET /health does not require authentication."""
        resp = _get(f"{base_url}/health", token=None)
        assert resp["status"] == "ok"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_deploy_missing_version(self, base_url: str) -> None:
        """Deploy without 'version' returns 400."""
        status, resp = _post_raw(f"{base_url}/services/svc/deploy", {})
        assert status == 400

    def test_deploy_empty_version(self, base_url: str) -> None:
        """Deploy with empty 'version' returns 400."""
        status, resp = _post_raw(f"{base_url}/services/svc/deploy", {"version": ""})
        assert status == 400

    def test_deploy_non_dict_body(self, base_url: str) -> None:
        """Deploy with a list body returns 400."""
        data = json.dumps([1, 2, 3]).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/services/svc/deploy", data=data, method="POST"
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer test-token")
        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            exc.close()
        else:
            raise AssertionError("expected HTTPError 400")

    def test_rollback_invalid_revision_id_type(
        self, base_url: str, backend: MockBackend
    ) -> None:
        """Rollback with non-string revision_id returns 400."""
        backend._health_results = [True]
        _post(f"{base_url}/services/svc/deploy", {"version": "v1"})

        status, resp = _post_raw(
            f"{base_url}/services/svc/rollback", {"revision_id": 123}
        )
        assert status == 400


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


class TestServerLifecycle:
    def test_start_stop_idempotent(
        self, backend: MockBackend, store: DeploymentStore
    ) -> None:
        """Calling start/stop multiple times is safe."""
        srv = LifecycleServer(backend=backend, store=store, host="127.0.0.1", port=0)
        srv.start()
        srv.start()  # idempotent
        srv.stop()
        srv.stop()  # idempotent

    def test_context_manager(
        self, backend: MockBackend, store: DeploymentStore
    ) -> None:
        """LifecycleServer can be used as a context manager."""
        backend._health_results = [True]
        with LifecycleServer(
            backend=backend, store=store, host="127.0.0.1", port=0
        ) as srv:
            url = f"http://{srv.host}:{srv.port}"
            resp = _post(f"{url}/services/svc/deploy", {"version": "v1"}, token=None)
            assert resp["status"] == "HEALTHY"

    def test_health_endpoint_available(self, base_url: str) -> None:
        """GET /health returns ok."""
        resp = _get(f"{base_url}/health", token=None)
        assert resp == {"status": "ok"}


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_serialized_deploys_same_service(
        self, base_url: str, backend: MockBackend, store: DeploymentStore
    ) -> None:
        """Two concurrent deploys on the same service are serialized."""
        backend._health_results = [True, True]

        results = []

        def _deploy(version: str) -> None:
            resp = _post(f"{base_url}/services/svc/deploy", {"version": version})
            results.append(resp)

        t1 = threading.Thread(target=_deploy, args=("v1",))
        t2 = threading.Thread(target=_deploy, args=("v2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Both should succeed (the lock ensures they don't interleave).
        assert len(results) == 2
        assert all(r["status"] == "HEALTHY" for r in results)

        # History should have both revisions in order.
        history = store.get_history("svc")
        assert len(history) == 2
