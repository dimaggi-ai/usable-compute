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


@pytest.mark.parametrize('group', [100, 101])
def test_publication_group_requires_membership(tmp_path, monkeypatch, group):
    monkeypatch.setattr(os, 'getegid', lambda: 10)
    monkeypatch.setattr(os, 'getgroups', lambda: [11, 12])
    path = tmp_path/'db'
    with pytest.raises(ValueError, match='publication group.*member'):
        w.WatchStore(path, 't', 'c', 'nodes', publication_gid=group)
    assert not path.exists() and not Path(str(path)+'.collector').exists()
