import os
from pathlib import Path
import pytest


def pytest_sessionstart(session):
    if os.environ.get('DIMAGGI_EXPECT_SOURCES') == '1':
        root = os.environ.get('DIMAGGI_TEST_SOURCES')
        if not root or not Path(root).is_dir():
            raise pytest.UsageError('CI requires the exported source bundle')


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.passed and hasattr(report, 'wasxfail'):
        report.outcome = 'failed'
        report.longrepr = 'Unexpected XPASS: ' + str(report.wasxfail)
        del report.wasxfail
    if os.environ.get('DIMAGGI_EXPECT_SOURCES') == '1' and report.skipped and not hasattr(report, 'wasxfail'):
        report.outcome = 'failed'
        report.longrepr = 'Unexpected skip with required CI sources: ' + str(report.longrepr)


@pytest.fixture(autouse=True)
def historical_watch_clock(request, monkeypatch):
    module = request.module.__name__.split('.')[-1]
    if module in {'test_watch_current', 'test_lease_fencing'}:
        stamp = '2026-09-24T00:00:00Z'
    elif module in {'test_binding_v2', 'test_infrastructure', 'test_infrastructure_roundtrip'}:
        stamp = '2026-09-20T12:00:01Z'
    else:
        return
    from dimaggi_receiver import topology_watch as watch
    monkeypatch.setattr(watch.time, 'time', lambda: watch._utc(stamp).timestamp())


@pytest.fixture(scope='session', autouse=True)
def xfail_policy_evidence(record_testsuite_property):
    record_testsuite_property('receiver_xfail_policy', 'strict-v1')


@pytest.fixture(autouse=True)
def historical_kernel_publication_clock(request, monkeypatch):
    # Historical-clock tests must put the kernel timestamp in the same simulated
    # domain as time.time(). Real-clock publication/SIGSTOP tests use real fstat.
    module = request.module.__name__.split('.')[-1]
    modules = {'test_watch_current', 'test_lease_fencing', 'test_binding_v2',
               'test_infrastructure', 'test_infrastructure_roundtrip',
               'test_lease_expiry', 'test_clock_tolerance', 'test_reader_clock',
               'test_reader_future', 'test_reader_ledger', 'test_reader_renewal',
               'test_reader_lock_order', 'test_reader_tombstone_reason',
               'test_reader_availability', 'test_snapshot_publication',
               'test_commit_bound', 'test_lease_lock_descriptor'}
    if module not in modules:
        return
    from dimaggi_receiver import topology_watch as watch
    from types import SimpleNamespace
    stamps = {}
    replace, fstat = os.replace, os.fstat
    def publish(src, dst):
        result = replace(src, dst)
        info = os.stat(dst)
        stamps[info.st_dev, info.st_ino] = watch.time.time()
        return result
    def snapshot_stat(fd):
        info = fstat(fd)
        stamp = stamps.get((info.st_dev, info.st_ino))
        if stamp is None:
            return info
        fields = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
        fields.update(st_ctime=stamp, st_ctime_ns=int(stamp*1e9))
        return SimpleNamespace(**fields)
    monkeypatch.setattr(os, 'replace', publish)
    monkeypatch.setattr(os, 'fstat', snapshot_stat)
