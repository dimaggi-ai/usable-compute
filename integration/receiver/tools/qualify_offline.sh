#!/usr/bin/env bash
set -euo pipefail
umask 022
lock=$1
package_root=$(cd "$(dirname "$lock")" && pwd)
wheel_root=${RUNNER_TEMP:?}/receiver-wheelhouse
mkdir -p "$wheel_root"
export SOURCE_DATE_EPOCH=1700000000 PYTHONHASHSEED=0
python -m pip download --only-binary=:all: --require-hashes -r "$lock" --dest "$wheel_root" --timeout 30 --retries 2
python -m pip install --no-index --find-links "$wheel_root" --require-hashes -r "$lock"
python - "$package_root" "$wheel_root" <<'PY'
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
source, wheelhouse = map(Path, sys.argv[1:])
wheels=[]
for index in range(2):
    root=Path(tempfile.mkdtemp(prefix='receiver-build-', dir=os.environ['RUNNER_TEMP']))
    tree=root/'source'
    shutil.copytree(source, tree, ignore=shutil.ignore_patterns('__pycache__', '*.egg-info', 'build', '.pytest_cache'))
    subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-index', '--no-deps', '--no-build-isolation',
                    '--wheel-dir', str(root/'dist'), str(tree)], check=True, timeout=120)
    wheels.append(next((root/'dist').glob('*.whl')))
pins=[hashlib.sha256(wheel.read_bytes()).hexdigest() for wheel in wheels]
if pins[0] != pins[1]:
    raise ValueError('receiver wheel builds differ')
target=wheelhouse/wheels[0].name
shutil.copyfile(wheels[0],target)
lock=wheelhouse/'receiver-wheel.lock'
lock.write_text(target.as_uri()+' --hash=sha256:'+pins[0]+'\n')
subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps', '--require-hashes',
                '-r', str(lock)], check=True, timeout=60)
print('receiver wheel sha256:', pins[0])
PY
python -m pip check
