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


@pytest.fixture(scope='session', autouse=True)
def xfail_policy_evidence(record_testsuite_property):
    record_testsuite_property('receiver_xfail_policy', 'strict-v1')
