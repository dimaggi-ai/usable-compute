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
