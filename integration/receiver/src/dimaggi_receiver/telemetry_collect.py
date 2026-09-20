"""Explicit bounded read-only collection for reviewed hosts/projects."""

import hashlib
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time
from urllib.parse import urlencode
from .bounded_fetch import https_get
from .jsonio import MAX_BYTES, canonical, loads, read_file
from .telemetry import SMI_FIELDS, TPU_PREFIX, google_tpu, nvidia_smi
from .infrastructure import obj, stamp, text


def collect_nvidia(binary, binary_digest, meta, as_of, freshness=300):
    path = Path(binary)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("explicit regular executable required")
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("executable size budget")
    if "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() != binary_digest:
        raise ValueError("SMI executable pin mismatch")
    proc = subprocess.Popen(
        [str(path), "--query-gpu=" + SMI_FIELDS, "--format=csv,noheader,nounits"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )
    output = bytearray()
    errors = bytearray()
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + 15
    for stream, target in ((proc.stdout, output), (proc.stderr, errors)):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, target)
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("SMI collection deadline")
            for key, _ in selector.select(min(remaining, 0.25)):
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                key.data.extend(data)
                if len(output) + len(errors) > MAX_BYTES:
                    raise ValueError("SMI output budget")
        if proc.wait(timeout=max(0.001, deadline - time.monotonic())) != 0:
            raise ValueError("SMI query failed")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        selector.close()
        proc.stdout.close()
        proc.stderr.close()
    # stderr is not exported and unsupported vendor data stays unknown/refused.
    return nvidia_smi(bytes(output), meta, as_of, freshness)


def read_token(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("owner-only credential file required")
        import sys

        if sys.platform == "darwin":
            import errno

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
        token = stream.read(8194).decode("ascii").removesuffix("\n")
        if not 0 < len(token) <= 8192 or any(
            ord(c) < 33 or ord(c) > 126 for c in token
        ):
            raise ValueError("credential format refused")
        return token


def collect_google(
    identity, token, meta, as_of, start_time, freshness=600, fetch=https_get
):
    obj(identity, ("project_id", "zone", "instance_id"), "Google identity")
    for value in identity.values():
        if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise ValueError("bounded Google identity required")
    if not 0 < (stamp(as_of) - stamp(start_time)).total_seconds() <= 3600:
        raise ValueError("query window must be at most one hour")
    series = []
    deadline = time.monotonic() + 60
    for metric in ("memory_total", "memory_used"):
        page = ""
        seen = set()
        for _ in range(8):
            if time.monotonic() >= deadline:
                raise ValueError("Monitoring total deadline")
            query = {
                "filter": f'metric.type="{TPU_PREFIX+metric}" AND resource.type="gce_instance" AND resource.labels.instance_id="{identity["instance_id"]}" AND resource.labels.zone="{identity["zone"]}"',
                "interval.startTime": start_time,
                "interval.endTime": as_of,
                "view": "FULL",
                "pageSize": "1024",
            }
            if page:
                query["pageToken"] = page
            url = (
                "https://monitoring.googleapis.com/v3/projects/"
                + identity["project_id"]
                + "/timeSeries?"
                + urlencode(query)
            )
            raw, _ = fetch(
                url,
                token=token,
                timeout=min(15, max(0.01, deadline - time.monotonic())),
                limit=MAX_BYTES,
            )
            body = loads(raw.decode("utf-8"))
            if type(body) is not dict or not set(body) <= {
                "timeSeries",
                "nextPageToken",
                "executionErrors",
                "unreachable",
                "unit",
            }:
                raise ValueError("invalid Monitoring envelope")
            if (
                body.get("executionErrors")
                or body.get("unreachable")
                or body.get("unit", "By") != "By"
            ):
                raise ValueError("Monitoring query incomplete or wrong units")
            rows = body.get("timeSeries", [])
            if type(rows) is not list:
                raise ValueError("invalid series list")
            series.extend(rows)
            if len(series) > 1024 or len(canonical(series)) > MAX_BYTES:
                raise ValueError("Monitoring data budget")
            page = body.get("nextPageToken", "")
            if not page:
                break
            if type(page) is not str or len(page) > 4096 or page in seen:
                raise ValueError("Monitoring pagination cycle or invalid token")
            seen.add(page)
        else:
            raise ValueError("Monitoring page budget exhausted")
    # The normalized digest binds complete raw series content, including every
    # point. Credential and page tokens are deliberately absent.
    return google_tpu(
        canonical({"timeSeries": series, "unit": "By"}),
        meta,
        identity,
        as_of,
        freshness,
    )
