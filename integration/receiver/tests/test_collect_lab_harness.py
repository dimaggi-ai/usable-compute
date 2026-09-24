"""Harness runs existing synthetic local TLS fixture, never a live lab."""
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_kubernetes_collect import lab, tls_material, NOW

spec = importlib.util.spec_from_file_location("collect_lab", Path(__file__).parents[1] / "tools" / "collect_lab.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def inputs(lab, tmp_path, monkeypatch):
    config, _, _, store, planning = lab
    registration = store.intent("request-1")
    store.close()
    data = asdict(config)
    token, ca = data.pop("bearer_token"), data.pop("ca_pem")
    contents = {"config": json.dumps(data), "ca": ca, "token_file": token + "\n",
                "registration": json.dumps(registration), "intent": planning["intent_json"].decode(),
                "report": planning["report_json"].decode()}
    args = {}
    for name, value in contents.items():
        path = tmp_path / name
        path.write_text(value)
        if name == "token_file": path.chmod(0o600)
        args[name] = str(path)
    args["journal"] = str(tmp_path / "harness.sqlite")
    original = harness.Collector
    monkeypatch.setattr(harness, "Collector", lambda config: original(config, clock=lambda: NOW))
    return SimpleNamespace(**args)


def test_persistent_reopen_preserves_unknown_and_sequence(lab, tmp_path, monkeypatch):
    args = inputs(lab, tmp_path, monkeypatch)
    one, two = harness.collect_once(args), harness.collect_once(args)
    assert one["result"]["state"] == two["result"]["state"] == "unknown"
    assert [one["source_sequence"], two["source_sequence"]] == [1, 2]
    assert one["source_authenticated"] is True and one["pod_spec_verified"] is False
    assert "TEST-COLLECTOR-CREDENTIAL" not in json.dumps(two)
    assert "source_bundle" not in json.dumps(two)


@pytest.mark.parametrize("change", ["memory", "unsupported_platform", "synthetic", "embedded_token", "duplicate_json", "bad_digest", "exposed_token", "symlink_token"])
def test_refusal_without_http(lab, tmp_path, monkeypatch, change):
    args = inputs(lab, tmp_path, monkeypatch)
    if change == "memory": args.journal = ":memory:"
    elif change == "unsupported_platform": monkeypatch.setattr("sys.platform", "unsupported")
    elif change == "exposed_token": Path(args.token_file).chmod(0o644)
    elif change == "symlink_token":
        link = tmp_path / "token-link"
        link.symlink_to(args.token_file)
        args.token_file = str(link)
    elif change == "duplicate_json": Path(args.config).write_text('{"endpoint":1,"endpoint":2}')
    elif change == "synthetic":
        value = json.loads(Path(args.registration).read_text())
        value["evidence_class"] = "synthetic"
        Path(args.registration).write_text(json.dumps(value))
    else:
        value = json.loads(Path(args.config).read_text())
        value["bearer_token" if change == "embedded_token" else "verifier_digest"] = "bad"
        Path(args.config).write_text(json.dumps(value))
    with pytest.raises(Exception): harness.collect_once(args)
    assert lab[2] == []


def test_cli_exception_has_no_private_details(monkeypatch, capsys):
    def fail(args): raise ValueError("TOKEN-PRIVATE /private/path")
    monkeypatch.setattr(harness, "collect_once", fail)
    argv = [part for name in ("config", "ca", "token-file", "registration", "intent", "report", "journal")
            for part in ("--" + name, "unused")]
    assert harness.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and json.loads(captured.err)["refused"] is True
    assert "PRIVATE" not in captured.err


def test_extended_acl_refuses_even_with_owner_only_mode(lab, tmp_path, monkeypatch):
    import subprocess
    args = inputs(lab, tmp_path, monkeypatch)
    import sys,os,struct
    if sys.platform=='darwin':
        subprocess.run(["chmod", "+a", "everyone allow read", args.token_file], check=True)
    else:
        # POSIX ACL with a masked named user remains nontrivial despite mode 0600.
        acl=struct.pack('<I',2)+b''.join(struct.pack('<HHI',tag,perm,uid) for tag,perm,uid in
            [(1,6,0xffffffff),(2,4,65534),(4,0,0xffffffff),(16,0,0xffffffff),(32,0,0xffffffff)])
        os.setxattr(args.token_file,'system.posix_acl_access',acl)
    try:
        assert Path(args.token_file).stat().st_mode & 0o077 == 0
        with pytest.raises(ValueError, match="ACL"):
            harness.collect_once(args)
        assert lab[2] == []
    finally:
        if sys.platform=='darwin':subprocess.run(["chmod", "-N", args.token_file], check=True)
        else:os.removexattr(args.token_file,'system.posix_acl_access')
