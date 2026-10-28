"""Offline tests for the Kernel browser adapter.

No test here sets a real KERNEL_API_KEY, calls the network, or opens a session:
a missing key must fail with ``configuration_missing`` before any paid call.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.browser import (
    BrowserFailure,
    BrowserProviderError,
    KernelBrowserProvider,
    RunBudget,
)
from app.browser.downloads import looks_like_html, validate_artifact
from app.core.config import Settings


def _settings(**overrides) -> Settings:
    kwargs = {"kernel_api_key": "test-key"}
    kwargs.update(overrides)
    return Settings(**kwargs)


class FakeCreateResponse(SimpleNamespace):
    def __init__(self, session_id: str) -> None:
        super().__init__(session_id=session_id)


class FakeExecuteResponse(SimpleNamespace):
    def __init__(self, *, success=True, result=None, error="", stderr="", stdout="") -> None:
        super().__init__(success=success, result=result, error=error, stderr=stderr, stdout=stdout)


class FakeFile(SimpleNamespace):
    def __init__(self, *, name, path, is_dir, size_bytes) -> None:
        super().__init__(name=name, path=path, is_dir=is_dir, size_bytes=size_bytes)


class FakeBinaryResponse(SimpleNamespace):
    def __init__(self, content: bytes) -> None:
        super().__init__(content=content)


class FakeBrowsers:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.execute_calls: list[tuple[str, str]] = []
        self._execute = FakeExecuteResponse()
        self._fail_delete = False
        self.reads: list[str] = []

    def create(self, **kwargs) -> FakeCreateResponse:
        sid = kwargs.get("name") or "sess-1"
        self.created.append(sid)
        return FakeCreateResponse(sid)

    def delete_by_id(self, id_or_name: str) -> None:
        if self._fail_delete:
            raise RuntimeError("delete failed")
        self.deleted.append(id_or_name)

    @property
    def playwright(self):
        return self

    def execute(self, id_or_name: str, *, code: str, timeout_sec: int | None = None):
        self.execute_calls.append((id_or_name, code))
        return self._execute

    @property
    def fs(self):
        return self

    def list_files(self, id_or_name: str, *, path: str):
        return [FakeFile(name="a.csv", path=f"{path}/a.csv", is_dir=False, size_bytes=3)]

    def read_file(self, id_or_name: str, *, path: str):
        self.reads.append(path)
        return FakeBinaryResponse(b"1,2,3")


class FakeClient(SimpleNamespace):
    def __init__(self) -> None:
        super().__init__(browsers=FakeBrowsers())


def _provider(client: FakeClient | None = None, budget: RunBudget | None = None, **settings) -> KernelBrowserProvider:
    provider = KernelBrowserProvider(settings=_settings(**settings), budget=budget)
    if client is not None:
        provider._client = client
    return provider


# --- configuration -----------------------------------------------------------

def test_missing_key_fails_before_any_call():
    provider = KernelBrowserProvider(settings=Settings(kernel_api_key=""))
    assert provider.configured is False
    with pytest.raises(BrowserProviderError) as exc:
        provider.open_session()
    assert exc.value.failure is BrowserFailure.CONFIGURATION_MISSING
    assert provider._client is None  # no client, no paid call


def test_classify_auth_and_rate_limit():
    from kernel import AuthenticationError, RateLimitError  # noqa: PLC0415

    def make(cls, msg: str = "x"):
        inst = cls.__new__(cls)
        Exception.__init__(inst, msg)
        return inst

    provider = _provider()
    auth = provider._classify(make(AuthenticationError))
    assert auth.failure is BrowserFailure.PROVIDER_AUTH
    rl = provider._classify(make(RateLimitError))
    assert rl.failure is BrowserFailure.PROVIDER_RATE_LIMITED


# --- lifecycle ---------------------------------------------------------------

def test_open_execute_read_release_deletes_session():
    client = FakeClient()
    provider = _provider(client=client)
    session = provider.open_session(name="run-a")
    assert client.browsers.created == ["run-a"]

    result = provider.execute(session, "return 1")
    assert result.success is True

    data = provider.read_file(session, "/tmp/a.csv")
    assert data == b"1,2,3"
    assert client.browsers.reads == ["/tmp/a.csv"]

    session.close()
    assert client.browsers.deleted == ["run-a"]
    assert session.closed is True

    # idempotent release
    session.close()
    assert client.browsers.deleted == ["run-a"]


def test_release_failure_preserves_session_id():
    client = FakeClient()
    client.browsers._fail_delete = True
    provider = _provider(client=client)
    session = provider.open_session(name="s1")
    with pytest.raises(BrowserProviderError) as exc:
        session.close()
    assert exc.value.failure is BrowserFailure.CLEANUP_FAILED
    assert exc.value.detail == "s1"


def test_cleanup_on_execute_exception_via_finally():
    """A caller that wraps execute in try/finally still releases the session."""
    client = FakeClient()
    provider = _provider(client=client)
    session = provider.open_session(name="s2")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        pass
    finally:
        session.close()
    assert client.browsers.deleted == ["s2"]


# --- wall-clock deadline vs standby timeout ----------------------------------

def test_wall_deadline_raises_timeout():
    client = FakeClient()
    provider = _provider(client=client)
    session = provider.open_session(name="s3")
    session.deadline_at = session.created_at - 1  # already expired
    with pytest.raises(BrowserProviderError) as exc:
        provider.execute(session, "return 1")
    assert exc.value.failure is BrowserFailure.TIMEOUT


def test_standby_timeout_is_a_separate_setting():
    provider = _provider()
    assert provider.settings.kernel_standby_timeout_seconds == 60
    assert provider.settings.kernel_max_session_wall_seconds == 180


# --- budget ------------------------------------------------------------------

def test_budget_session_limit():
    client = FakeClient()
    budget = RunBudget(max_sessions=1, max_session_wall_seconds=180, max_total_browser_seconds=900)
    provider = _provider(client=client, budget=budget)
    provider.open_session(name="only")
    with pytest.raises(BrowserProviderError) as exc:
        provider.open_session(name="second")
    assert exc.value.failure is BrowserFailure.BUDGET_EXHAUSTED


def test_budget_total_seconds_exhausted():
    budget = RunBudget(max_sessions=5, max_session_wall_seconds=180, max_total_browser_seconds=10)
    budget.record_wall(10.0)
    with pytest.raises(BrowserProviderError) as exc:
        budget.register_open()
    assert exc.value.failure is BrowserFailure.BUDGET_EXHAUSTED


def test_budget_checkpoint_roundtrip(tmp_path):
    budget = RunBudget(path=tmp_path / "budget.json", max_total_browser_seconds=900)
    budget.register_open()
    budget.record_wall(12.5)
    restored = RunBudget(path=tmp_path / "budget.json")
    restored.load()
    assert restored.sessions_created == 1
    assert restored.total_browser_seconds == 12.5


# --- downloads ---------------------------------------------------------------

def test_reject_html_as_dataset():
    with pytest.raises(BrowserProviderError) as exc:
        validate_artifact(b"<!DOCTYPE html><html>login</html>", max_bytes=1024)
    assert exc.value.failure is BrowserFailure.VALIDATION_FAILED


def test_reject_empty_and_oversize():
    with pytest.raises(BrowserProviderError):
        validate_artifact(b"", max_bytes=1024)
    with pytest.raises(BrowserProviderError):
        validate_artifact(b"x" * 5, max_bytes=3)


def test_accept_plain_bytes_with_hash():
    meta = validate_artifact(b"col1;col2\n", max_bytes=1024)
    assert meta["byte_size"] == 10
    assert len(meta["content_hash"]) == 64


def test_looks_like_html_detection():
    assert looks_like_html(b"<html><head>")
    assert looks_like_html(b"<HTML>")
    assert not looks_like_html(b"a,b,c\n1,2,3")

