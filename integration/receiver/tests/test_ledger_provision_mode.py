import os
import stat

import pytest
from dimaggi_receiver.expiry_ledger import initialize_expiry_ledger, check_generation


@pytest.mark.parametrize('mask', [0o277, 0o077, 0o777])
def test_provision_enforces_usable_private_mode(tmp_path, mask):
    path = tmp_path/'ledger'
    previous = os.umask(mask)
    try:
        initialize_expiry_ledger(path)
    finally:
        os.umask(previous)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    check_generation(path, 'store', ['t', 'c', 'nodes', ''], 'generation', True)
    with pytest.raises(ValueError, match='closed or expired'):
        check_generation(path, 'store', ['t', 'c', 'nodes', ''], 'generation', False)
