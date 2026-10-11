from pathlib import Path
import pytest
import yaml

ROOT=Path(__file__).resolve().parents[3]


def test_linux_ci_uses_hashed_platform_locks():
    jobs=yaml.safe_load((ROOT/'.github/workflows/receiver.yml').read_text())['jobs']
    assert 'linux' in jobs
    matrix=jobs['linux']['strategy']['matrix']['include']
    assert {row['runner'] for row in matrix} == {'ubuntu-24.04','ubuntu-24.04-arm'}
    for row in matrix:
        path=ROOT/'integration/receiver'/row['lock']
        text=path.read_text()
        assert 'pytest==8.3.4' in text and '--hash=sha256:' in text
    assert 'qualify_offline.sh' in str(jobs['linux'])


def test_macos_ci_has_no_unhashed_network_install():
    jobs=yaml.safe_load((ROOT/'.github/workflows/receiver.yml').read_text())['jobs']
    steps=jobs['receiver']['steps']
    build=next(step['run'] for step in steps if step.get('name') == 'Build and install wheel')
    assert 'qualify_offline.sh' in build
    assert 'requirements-darwin-arm64-py312.lock' in build


def test_every_workflow_network_install_requires_hashes():
    for path in (ROOT/'.github/workflows').glob('*.yml'):
        value=yaml.safe_load(path.read_text())
        for job in value['jobs'].values():
            for step in job['steps']:
                for line in step.get('run','').splitlines():
                    if 'pip install' in line or 'pip download' in line:
                        assert '--require-hashes' in line, (path.name,line)


def test_actions_are_exact_commit_pins_with_version_comments():
    import re
    for path in (ROOT/'.github/workflows').glob('*.yml'):
        for line in path.read_text().splitlines():
            if 'uses:' in line:
                assert re.search(r'uses: [^@ ]+@[0-9a-f]{40} # v[0-9]', line), line


def test_both_platforms_export_and_require_source_acceptance():
    jobs=yaml.safe_load((ROOT/'.github/workflows/receiver.yml').read_text())['jobs']
    for name in ('receiver','linux'):
        steps=jobs[name]['steps']
        assert any('ci_export_sources.py' in step.get('run','') for step in steps)
        test=next(s for s in steps if 'check_acceptance.py' in s.get('run',''))
        assert test['env']['DIMAGGI_EXPECT_SOURCES']=='1'
        assert 'DIMAGGI_TEST_SOURCES' in test['env']


def test_missing_expected_sources_is_failure(tmp_path):
    import os
    import subprocess
    import sys
    result=subprocess.run([sys.executable,'-m','pytest',str(ROOT/'integration/receiver/tests/test_sim_order.py'),'-q'],
                          env=dict(os.environ,DIMAGGI_EXPECT_SOURCES='1',DIMAGGI_TEST_SOURCES=str(tmp_path/'missing')),
                          capture_output=True,text=True,timeout=10)
    assert result.returncode != 0
    assert 'CI requires the exported source bundle' in result.stderr
