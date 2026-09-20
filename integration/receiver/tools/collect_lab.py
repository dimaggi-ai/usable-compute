#!/usr/bin/env python3
"""Explicit one-shot trusted-host collection; no submission or kubeconfig loading."""
from __future__ import annotations

import argparse
import hashlib
import json
import errno
import os
import stat
import sys
from pathlib import Path

from dimaggi_receiver.jsonio import loads, read_file
from dimaggi_receiver.kubernetes_collect import Collector, CollectorConfig
from dimaggi_receiver.observations import ObservationStore, darwin_ofd_command


def read_private_token(path):
    """Check the opened file, not a raceable pre-open path stat."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise ValueError("credential file must be owner-only regular input")
        # Darwin ACL entries can grant access despite restrictive mode bits.
        # Reject any nontrivial ACL rather than claiming mode bits suffice.
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        libc.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
        libc.acl_get_fd_np.restype = ctypes.c_void_p
        libc.acl_free.argtypes = [ctypes.c_void_p]
        libc.acl_free.restype = ctypes.c_int
        ctypes.set_errno(0)
        acl = libc.acl_get_fd_np(stream.fileno(), 0x100)  # Darwin ACL_TYPE_EXTENDED
        if acl:
            libc.acl_free(acl)
            raise ValueError("credential ACL refused")
        # Apple's acl_get_fd_np uses filesec_get_property(FILESEC_ACL);
        # ENOENT means that property is absent on this already-opened fd.
        if ctypes.get_errno() != errno.ENOENT:
            raise ValueError("credential ACL inspection refused")
        raw = stream.read(8194)
        token = raw.decode("ascii").removesuffix("\n")
        if not 0 < len(token) <= 8192:
            raise ValueError("credential size refused")
        return token


def collect_once(args):
    # Refuse unsupported persistence before opening any input or making requests.
    darwin_ofd_command()
    if args.journal == ":memory:":
        raise ValueError("persistent journal required")
    config = loads(read_file(args.config).decode("utf-8"))
    registration = loads(read_file(args.registration).decode("utf-8"))
    intent, report = read_file(args.intent), read_file(args.report)
    if type(config) is not dict or {"ca_pem", "bearer_token"} & set(config):
        raise ValueError("credentials must be separately supplied")
    if type(registration) is not dict or registration.get("evidence_class") != "observed":
        raise ValueError("explicit observed registration required")
    # Files are bounded regular inputs. No environment credential or discovery.
    token = read_private_token(args.token_file)
    configured = CollectorConfig(**config, ca_pem=read_file(args.ca).decode("utf-8"), bearer_token=token)
    collector = Collector(configured)
    with ObservationStore(Path(args.journal)) as store:
        for kind, source in registration["sources"].items():
            store.register_source(source, kind, registration["target_id"])
        store.register_intent(registration)
        result = collector.collect(store, request_id=registration["request_id"],
                                   intent_json=intent, report_json=report)
        # Collector bounds history before its reads and append. Export only the
        # latest collection verdict, never server bodies, credential or logs.
        event = store.history(registration["request_id"])[-1]["event"]
        collection = event["payload"]["collection"]
        return {"schema": "dimaggi-lab-collection-receipt/v1", "result": result,
                "intent_digest": "sha256:" + hashlib.sha256(intent).hexdigest(),
                "report_digest": "sha256:" + hashlib.sha256(report).hexdigest(),
                "verifier_digest": configured.verifier_digest,
                "source_epoch": event["source_epoch"], "source_sequence": event["source_sequence"],
                "source_authenticated": collection["source_authenticated"],
                "pod_spec_verified": collection["pod_spec_verified"],
                "output_verified": collection["output_verified"],
                "started_at_utc": collection["started_at_utc"],
                "finished_at_utc": collection["finished_at_utc"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("config", "ca", "token-file", "registration", "intent", "report", "journal"):
        parser.add_argument("--" + flag, required=True)
    args = parser.parse_args(argv)
    try:
        result = collect_once(args)
    except Exception:
        # Local/remote exceptions may contain private paths or token-derived text.
        print('{"schema":"dimaggi-lab-collection-receipt/v1","refused":true}', file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
