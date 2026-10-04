"""Bound the real SQLite publication lifecycle on each qualified platform."""
import subprocess
import sys


def test_publication_backup_finishes_before_lease_bound(tmp_path):
    # On Darwin, taking flock on the target before SQLite backup makes SQLite's
    # own file locks report BUSY indefinitely. A child deadline catches hangs.
    code = '''
import sys, time
from datetime import datetime, timezone
from pathlib import Path
from dimaggi_receiver import topology_watch as watch
iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat().replace('+00:00', 'Z')
path = Path(sys.argv[1])
ledger = path.with_name('ledger')
watch.initialize_expiry_ledger(ledger)
store = watch.WatchStore(path, 't', 'c', 'nodes')
now = time.time()
store.relist({'apiVersion': 'v1', 'kind': 'NodeList',
              'metadata': {'resourceVersion': '1'}, 'items': []}, iso(now), iso(now + 290))
store.heartbeat()
if sys.platform == 'linux':
    assert not watch.read_current(path, expiry_ledger=ledger, tenant='t', cluster='c',
                                  collection='nodes', now=iso(time.time()))['issues']
else:
    try:
        watch.read_current(path, expiry_ledger=ledger, tenant='t', cluster='c',
                           collection='nodes', now=iso(time.time()))
    except ValueError as error:
        assert 'kernel publication evidence unavailable' in str(error)
    else:
        raise AssertionError('unqualified platform must refuse current evidence')
store.close()
replacement = watch.WatchStore(path, 't', 'c', 'nodes')
replacement.close()
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path / 'watch.db')],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
