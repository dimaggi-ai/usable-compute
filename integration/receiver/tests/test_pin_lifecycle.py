import fcntl
import gc
import os
import sqlite3
import time
import weakref
from pathlib import Path

import pytest
from dimaggi_receiver import topology_watch as w
from test_publication_evidence import published, read, iso
from test_failure_ownership import Fault


def test_publication_pin_is_readonly(published):
    store, path, _ = published
    fd = store._published_fd
    before = path.read_bytes()
    try:
        with pytest.raises(OSError): os.pwrite(fd, before[:1], 0)
        assert fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
    finally:
        assert path.read_bytes() == before


@pytest.mark.parametrize('operation', ['withdraw', 'replace', 'close'])
def test_publication_pin_released(published, operation):
    store, path, _ = published
    fd = store._published_fd
    prior = os.fstat(fd)
    if operation == 'withdraw': store._withdraw(True)
    elif operation == 'replace': store.heartbeat()
    else: store.close()
    try:
        current = os.fstat(fd)
    except OSError:
        return
    assert operation == 'replace'
    assert (current.st_dev, current.st_ino) != (prior.st_dev, prior.st_ino)


@pytest.mark.parametrize('obsolete', [False, True])
def test_unreachable_collectors_release_pins(tmp_path, obsolete):
    gc.collect()
    baseline = len(os.listdir('/proc/self/fd' if Path('/proc/self/fd').exists() else '/dev/fd'))
    refs = []
    path = tmp_path/'db'
    for i in range(8):
        target = path if obsolete else tmp_path/str(i)
        store = w.WatchStore(target, 't', 'c', 'nodes')
        refs.append(weakref.ref(store))
        del store
    before = path.read_bytes() if obsolete else (tmp_path/'7').read_bytes()
    gc.collect()
    assert all(ref() is None for ref in refs)
    assert len(os.listdir('/proc/self/fd' if Path('/proc/self/fd').exists() else '/dev/fd')) == baseline
    assert (path if obsolete else tmp_path/'7').read_bytes() == before
