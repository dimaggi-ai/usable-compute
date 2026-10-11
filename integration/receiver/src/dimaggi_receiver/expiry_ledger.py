"""Reader-owned, append-only observations of dead collector generations."""
import fcntl
import json
import os
import stat
import time
from contextlib import contextmanager

from .topology import need

HEADER = '"dimaggi-expiry-ledger/v1"\n'


def initialize_expiry_ledger(path):
    """Provision once as the reader identity; never replace an existing ledger."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(HEADER)
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(os.path.dirname(os.path.abspath(path)), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


@contextmanager
def _opened(path):
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'r+', encoding='utf-8') as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
             and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
             'private reader-owned expiry ledger required')
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                need(time.monotonic() < deadline, 'expiry ledger busy')
                time.sleep(0.01)
        try:
            yield stream
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def check_generation(path, store_id, scope, generation, live):
    key = [store_id, scope, generation]
    with _opened(path) as stream:
        need(stream.readline() == HEADER, 'expiry ledger corrupt')
        dead = []
        for line in stream:
            need(line.endswith('\n'), 'expiry ledger truncated')
            entry = json.loads(line)
            need(type(entry) is list and len(entry) == 3
                 and type(entry[0]) is str and bool(entry[0])
                 and type(entry[1]) is list and len(entry[1]) == 4
                 and all(type(v) is str for v in entry[1])
                 and type(entry[2]) is str and bool(entry[2]), 'expiry ledger corrupt')
            dead.append(entry)
        need(key not in dead, 'collector generation previously expired')
        if not live:
            stream.seek(0, os.SEEK_END)
            stream.write(json.dumps(key, separators=(',', ':')) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        need(live, 'collector lease closed or expired')
