"""Collect the workflow selections and regenerate their expected test identities."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
SUITES = {'receiver': ('receiver.yml', 'linux'),
          'receiver-darwin': ('receiver.yml', 'receiver'),
          'tools': ('evidence.yml', 'validate')}


def manifest_path(suite):
    return Path(__file__).with_name(suite + '_tests.json')


def selection(suite):
    import yaml
    filename, job = SUITES[suite]
    workflow = yaml.safe_load((ROOT/'.github/workflows'/filename).read_text())
    selections = []
    for step in workflow['jobs'][job]['steps']:
        command = step.get('run', '').replace('\\\n', ' ')
        for line in command.splitlines():
            args = shlex.split(line)
            if args[:3] != ['python', '-m', 'pytest']:
                continue
            args = args[3:]
            if '--junitxml' in args:
                index = args.index('--junitxml')
                del args[index:index+2]
            selections.append(args)
    if not selections or any(args != selections[0] for args in selections):
        raise ValueError('one consistent CI selection required')
    return selections[0]


def junit_identity(nodeid):
    address, bracket, params = nodeid.partition('[')
    parts = address.split('::')
    parts[0] = parts[0].removesuffix('.py').replace('/', '.')
    return ('.'.join(parts[:-1]), parts[-1] + bracket + params)


def collect(suite):
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary)/'nodes.json'
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--collect', suite, str(output)],
                                cwd=ROOT, capture_output=True, text=True, timeout=45)
        if result.returncode:
            raise ValueError(result.stdout + result.stderr)
        return json.loads(output.read_text())


def main():
    if len(sys.argv) == 4 and sys.argv[1] == '--collect':
        import pytest
        class Collector:
            def pytest_collection_finish(self, session):
                Path(sys.argv[3]).write_text(json.dumps(sorted(item.nodeid for item in session.items)))
        raise SystemExit(pytest.main(selection(sys.argv[2]) + ['--collect-only'], plugins=[Collector()]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--suite', choices=SUITES, action='append')
    args = parser.parse_args()
    for suite in args.suite or SUITES:
        nodes = collect(suite)
        if not nodes or len(set(nodes)) != len(nodes):
            raise ValueError('nonempty unique collection required')
        path = manifest_path(suite)
        if args.check:
            if nodes != json.loads(path.read_text()):
                raise ValueError('stale test manifest: ' + suite)
        else:
            path.write_text(json.dumps(nodes, indent=2) + '\n')
        print(f'{suite}: {len(nodes)} collected identities')


if __name__ == '__main__':
    main()
