import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile



ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / ('requirements-darwin-arm64-py312.lock' if sys.platform == 'darwin' else
               'requirements-linux-' + os.uname().machine + '-py312.lock')
EPOCH = '1700000000'


def test_wheel_build_uses_locked_tools():
    assert sys.version_info[:2] == (3, 12)
    lock = LOCK.read_text()
    for name in ('pip', 'setuptools', 'wheel'):
        pin = re.search(r'^' + name + r'==([^\s\\]+)', lock, re.MULTILINE)
        assert pin is not None, name + ' must be hash-pinned in the build lock'
        assert importlib.metadata.version(name) == pin[1]
    requires = tomllib.loads((ROOT / 'pyproject.toml').read_text())['build-system']['requires']
    assert requires == ['setuptools==' + importlib.metadata.version('setuptools')]


def test_wheel_bytes_and_modes_ignore_checkout_umask(tmp_path):
    wheels = []
    modes = []
    for mask in (0o022, 0o077):
        tree = tmp_path / f'checkout-{mask:03o}'
        shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(
            '.git', '__pycache__', '*.pyc', '*.egg-info', 'build', 'dist', '.pytest_cache'))
        # Model fresh Git checkouts created under each invoking umask.
        for path in tree.rglob('*'):
            if path.is_file():
                path.chmod(0o666 & ~mask)
                os.utime(path, (int(EPOCH) + mask, int(EPOCH) + mask))
        source = tree / 'src/dimaggi_receiver/profile.json'
        source_mode = source.stat().st_mode
        out = tmp_path / f'wheels-{mask:03o}'
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'wheel', '--no-index', '--no-deps',
             '--no-build-isolation', '--wheel-dir', str(out), str(tree)],
            cwd=tree, env=dict(os.environ, SOURCE_DATE_EPOCH=EPOCH, PYTHONHASHSEED='0'),
            umask=mask, close_fds=True, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        assert source.stat().st_mode == source_mode
        wheel, = out.glob('*.whl')
        wheels.append(wheel.read_bytes())
        with zipfile.ZipFile(wheel) as stream:
            modes.append({info.filename: info.external_attr >> 16 for info in stream.infolist()})
        print(json.dumps({'checkout': str(tree), 'umask': oct(mask),
                          'sha256': hashlib.sha256(wheels[-1]).hexdigest(),
                          'modes': modes[-1]}, sort_keys=True))
    differences = {name: [modes[0][name], modes[1].get(name)] for name in modes[0]
                   if modes[0][name] != modes[1].get(name)}
    assert (modes[0], wheels[0]) == (modes[1], wheels[1]), differences
    for name, mode in modes[0].items():
        assert mode == (0o100664 if name.endswith('.dist-info/RECORD') else 0o100644), name
