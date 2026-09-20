from copy import deepcopy
import pytest
from dimaggi_receiver.source_watch import collect, compare, validate_config
from dimaggi_receiver.bounded_fetch import https_get

CONFIG = {
    "schema": "dimaggi-source-watch-config/v1",
    "sources": [
        {
            "id": "tpu-runtime",
            "url": "https://docs.cloud.google.com/tpu/docs/runtimes",
            "profile_ids": ["google.tpu.v6e"],
            "max_bytes": 1000,
        }
    ],
}
NOW = "2026-09-20T12:00:00Z"


def test_change_quarantine_stable_findings_and_no_activation():
    a = collect(CONFIG, NOW, lambda *a, **k: (b"old", "text/html"))
    b = collect(CONFIG, "2026-09-20T12:01:00Z", lambda *a, **k: (b"new", "text/html"))
    report = compare(CONFIG, a, b)
    assert report["status"] == "changes" and report["findings"][0][
        "affected_profiles"
    ] == ["google.tpu.v6e"]
    assert not report["activation_authorized"] and report["mutation_request"] is None
    later = collect(
        CONFIG, "2026-09-20T12:02:00Z", lambda *a, **k: (b"new", "text/html")
    )
    assert (
        compare(CONFIG, a, later)["findings"][0]["finding_id"]
        == report["findings"][0]["finding_id"]
    )
    assert compare(CONFIG, b, later)["status"] == "no_change"


@pytest.mark.parametrize("kind", ["failure", "empty", "large", "format"])
def test_collection_failure_never_no_change(kind):
    def fetch(*a, **k):
        if kind == "failure":
            raise TimeoutError("private failure details")
        if kind == "empty":
            return b"", "text/html"
        if kind == "large":
            return b"x" * 1001, "text/html"
        return b"x", "application/octet-stream"

    good = collect(CONFIG, NOW, lambda *a, **k: (b"old", "text/html"))
    bad = collect(CONFIG, NOW, fetch)
    assert bad["status"] == "incomplete" and "private failure" not in str(bad)
    assert compare(CONFIG, good, bad)["status"] == "incomplete"
    assert compare(CONFIG, bad, bad)["status"] == "incomplete"


@pytest.mark.parametrize(
    "url",
    [
        "http://docs.nvidia.com/test",
        "https://evil.example/test",
        "https://docs.nvidia.com.evil.example/test",
        "https://docs.nvidia.com@evil.example/test",
        "https://docs.nvidia.com:443/test",
        "https://docs.nvidia.com/test?credential=x",
        "https://docs.nvidia.com/test#x",
    ],
)
def test_source_scope(url):
    c = deepcopy(CONFIG)
    c["sources"][0]["url"] = url
    with pytest.raises(ValueError):
        validate_config(c)


def test_changed_mapping_tampering_and_clock_rollback():
    a = collect(CONFIG, NOW, lambda *a, **k: (b"a", "text/html"))
    b = collect(CONFIG, "2026-09-20T11:59:59Z", lambda *a, **k: (b"a", "text/html"))
    with pytest.raises(ValueError):
        compare(CONFIG, a, b)
    b = deepcopy(a)
    b["sources"][0]["profile_ids"] = ["foreign"]
    with pytest.raises(ValueError):
        compare(CONFIG, a, b)
    c = deepcopy(CONFIG)
    c["sources"][0]["profile_ids"] = ["new-profile"]
    with pytest.raises(ValueError):
        compare(c, a, a)


def test_fetch_redirect_and_timeout_boundaries(monkeypatch):
    import subprocess
    import dimaggi_receiver.bounded_fetch as f

    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], kw["timeout"])

    monkeypatch.setattr(f.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="deadline"):
        https_get("https://docs.nvidia.com/test", timeout=1)
    for url in (
        "http://example.test",
        "https://user:password@example.test",
        "https://example.test:8443",
    ):
        with pytest.raises(ValueError):
            https_get(url)


def test_pending_review_survives_recovery_and_reversion(tmp_path):
    from dimaggi_receiver.source_watch import review_queue, check_queue

    a = collect(CONFIG, NOW, lambda *a, **k: (b"old", "text/html"))
    b = collect(CONFIG, NOW, lambda *a, **k: (b"new", "text/html"))
    queue = review_queue(CONFIG, compare(CONFIG, a, b))
    assert len(queue["findings"]) == 1
    queue = review_queue(CONFIG, compare(CONFIG, b, b), queue)
    assert len(queue["findings"]) == 1
    queue = review_queue(CONFIG, compare(CONFIG, b, a), queue)
    assert len(queue["findings"]) == 2
    queue["findings"] = []
    with pytest.raises(ValueError):
        check_queue(queue)


def test_history_runner_retains_sources_and_pending_reviews(tmp_path):
    import importlib.util
    from pathlib import Path
    from dimaggi_receiver.jsonio import loads

    spec = importlib.util.spec_from_file_location(
        "watch_run", Path(__file__).parents[1] / "tools/source_watch_run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first, code = module.run(CONFIG, tmp_path, lambda *a, **k: (b"old", "text/html"))
    assert code == 0 and first["pending_reviews"] == 0
    second, code = module.run(CONFIG, tmp_path, lambda *a, **k: (b"new", "text/html"))
    third, code = module.run(CONFIG, tmp_path, lambda *a, **k: (b"new", "text/html"))
    assert code == 0 and second["pending_reviews"] == third["pending_reviews"] == 1
    assert len(list(tmp_path.glob("*/sources/*"))) == 3
    Path(third["run"], "queue.json").unlink()
    with pytest.raises(FileNotFoundError):
        module.run(CONFIG, tmp_path, lambda *a, **k: (b"new", "text/html"))


def test_actual_redirect_handler_refuses_following(monkeypatch):
    import io, json, sys, urllib.request
    from email.message import Message
    from urllib.error import HTTPError
    from dimaggi_receiver.bounded_fetch import _worker

    captured = []

    def opener(*handlers):
        captured.extend(handlers)
        raise ValueError("test stop before network")

    monkeypatch.setattr(urllib.request, "build_opener", opener)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.TextIOWrapper(
            io.BytesIO(
                json.dumps(
                    dict(
                        url="https://docs.nvidia.com/test",
                        token=None,
                        timeout=1,
                        limit=100,
                    )
                ).encode()
            )
        ),
    )
    with pytest.raises(ValueError):
        _worker()
    assert captured[0].proxies == {}
    redirect = captured[1]
    headers = Message()
    headers["Location"] = "https://foreign.example/"
    assert (
        redirect.http_error_302(
            urllib.request.Request("https://docs.nvidia.com/test"),
            io.BytesIO(),
            302,
            "redirect",
            headers,
        )
        is None
    )
    with pytest.raises(HTTPError):
        urllib.request.HTTPDefaultErrorHandler().http_error_default(
            urllib.request.Request("https://docs.nvidia.com/test"),
            io.BytesIO(),
            302,
            "redirect",
            headers,
        )


def test_explicit_review_is_pinned_and_does_not_refresh_source_time(tmp_path):
    import importlib.util
    from pathlib import Path
    from dimaggi_receiver.jsonio import loads

    modules = []
    for name in ("source_watch_run", "source_watch_review"):
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).parents[1] / ("tools/" + name + ".py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    runner, reviewer = modules
    runner.run(CONFIG, tmp_path, lambda *a, **k: (b"old", "text/html"))
    result, _ = runner.run(CONFIG, tmp_path, lambda *a, **k: (b"new", "text/html"))
    queue = loads(Path(result["run"], "queue.json").read_text())
    finding = queue["findings"][0]["finding_id"]
    evidence = tmp_path / "review.txt"
    evidence.write_text("Synthetic review fixture; not an actual source assessment.")
    with pytest.raises(ValueError):
        reviewer.review(tmp_path, "wrong", [finding], evidence, "test-reviewer")
    with pytest.raises(ValueError):
        reviewer.review(
            tmp_path, queue["queue_digest"], ["foreign"], evidence, "test-reviewer"
        )
    reviewed = reviewer.review(
        tmp_path, queue["queue_digest"], [finding], evidence, "test-reviewer"
    )
    assert reviewed["pending_reviews"] == 0
    updated = loads(Path(reviewed["run"], "queue.json").read_text())
    assert updated["observed_at"] == queue["observed_at"]
    result, _ = runner.run(CONFIG, tmp_path, lambda *a, **k: (b"new", "text/html"))
    assert result["pending_reviews"] == 0
    result, _ = runner.run(
        CONFIG, tmp_path, lambda *a, **k: (b"changed-again", "text/html")
    )
    assert result["pending_reviews"] == 1
