"""Darwin ownership survives Python builds that omit the OFD macro."""
import errno
import fcntl
import os
import sys

import pytest

from dimaggi_receiver.observations import ObservationError, ObservationStore, darwin_ofd_command, ofd_lock_spec


def test_missing_python_constant_still_enforces_descriptor_ownership(monkeypatch, tmp_path):
    if sys.platform == 'darwin':
        monkeypatch.delattr(fcntl, 'F_OFD_SETLK', raising=False)
        assert darwin_ofd_command() == 90
    else:
        assert ofd_lock_spec()[0] == 37
    path = tmp_path / 'journal.sqlite'
    with ObservationStore(path):
        unrelated = os.open(path, os.O_RDONLY)
        os.close(unrelated)
        with pytest.raises(ObservationError, match='active application writer'):
            ObservationStore(path)
    with ObservationStore(path):
        pass


def test_kernel_refusal_never_falls_back(monkeypatch, tmp_path):
    expected = ofd_lock_spec()[0]
    calls = []
    def unsupported(fd, command, payload):
        calls.append(command)
        raise OSError(errno.EINVAL, 'unsupported OFD command')
    monkeypatch.setattr(fcntl, 'fcntl', unsupported)
    path = tmp_path / 'refused.sqlite'
    with pytest.raises(OSError, match='unsupported OFD'):
        ObservationStore(path)
    assert calls == [expected]
    assert path.read_bytes() == b''


def test_wrong_platform_or_constant_refused(monkeypatch):
    monkeypatch.setattr('sys.platform', 'darwin')
    monkeypatch.setattr(fcntl, 'F_OFD_SETLK', 999, raising=False)
    with pytest.raises(ObservationError, match='unexpected Darwin'):
        darwin_ofd_command()
    monkeypatch.setattr('sys.platform', 'linux')
    with pytest.raises(ObservationError, match='64-bit Darwin'):
        darwin_ofd_command()
