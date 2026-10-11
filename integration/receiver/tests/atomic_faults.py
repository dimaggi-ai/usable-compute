import builtins
import io
import os
from pathlib import Path
import stat


def inject_failure(monkeypatch, target, phase):
    target = Path(target)
    events = []
    original_open, original_fdopen = io.open, os.fdopen
    original_sync, original_replace = os.fsync, os.replace

    def matches(name):
        if isinstance(name, int):
            return False
        path = Path(name)
        return path.parent == target.parent and (
            path.name == target.name or path.name.startswith('.' + target.name + '.')
        )

    def matches_fd(fd):
        if not target.parent.exists():
            return False
        info = os.fstat(fd)
        return any(os.path.samestat(info, path.stat()) for path in target.parent.iterdir() if matches(path))

    class BrokenWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, data):
            self.stream.write(data[:1])
            self.stream.flush()
            events.append('write')
            raise OSError('injected write failure')

    def opened(file, mode='r', *args, **kwargs):
        stream = original_open(file, mode, *args, **kwargs)
        if phase == 'write' and 'w' in mode and matches(file):
            return BrokenWriter(stream)
        return stream

    def fdopened(fd, mode='r', *args, **kwargs):
        stream = original_fdopen(fd, mode, *args, **kwargs)
        if phase == 'write' and 'w' in mode and matches_fd(fd):
            return BrokenWriter(stream)
        return stream

    def sync(fd):
        if phase == 'fsync' and matches_fd(fd):
            events.append('fsync')
            raise OSError('injected fsync failure')
        if phase == 'directory_fsync' and stat.S_ISDIR(os.fstat(fd).st_mode) and os.path.samestat(os.fstat(fd), target.parent.stat()):
            events.append('directory_fsync')
            raise OSError('injected directory fsync failure')
        return original_sync(fd)

    def replace(source, destination):
        if phase == 'replace' and Path(destination) == target:
            events.append('replace')
            raise OSError('injected replace failure')
        return original_replace(source, destination)

    monkeypatch.setattr(builtins, 'open', opened)
    monkeypatch.setattr(io, 'open', opened)
    monkeypatch.setattr(os, 'fdopen', fdopened)
    monkeypatch.setattr(os, 'fsync', sync)
    monkeypatch.setattr(os, 'replace', replace)
    return events
