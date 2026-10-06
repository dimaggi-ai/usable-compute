"""Check the actual pytest selection used by evidence CI."""
from pathlib import Path
import shlex
import subprocess
import sys
import yaml


def test_evidence_ci_collects_all_pytest_functions():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root/'.github/workflows/evidence.yml').read_text())
    commands = [line for step in workflow['jobs']['validate']['steps']
                for line in step.get('run','').splitlines() if '-m pytest ' in line]
    collected = ''
    for command in commands:
        args = shlex.split(command)
        if '--junitxml' in args:
            index = args.index('--junitxml')
            del args[index:index+2]
        result = subprocess.run([sys.executable, *args[1:], '--collect-only'], cwd=root,
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr + result.stdout
        collected += result.stdout
    for name in ('test_records_v1_cannot_credit_unadmitted_references',
                 'test_consumption_requires_persistent_replay_store',
                 'test_records_invokes_admission_and_binds_scope',
                 'test_records_admission_success_and_restart_replay_refusal'):
        assert 'test_rsi_consumption.py::' + name in collected
    import ast
    for path in (root/'tools').glob('test_*.py'):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith('test_'):
                assert path.name + '::' + node.name in collected


def test_tools_ci_manifest_matches_collection():
    import importlib.util
    import json
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('ci_manifest', root/'integration/receiver/tools/ci_test_manifest.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    assert module.collect('tools') == json.loads(module.manifest_path('tools').read_text())
    workflow = yaml.safe_load((root/'.github/workflows/evidence.yml').read_text())
    assert any('check_acceptance.py' in step.get('run', '') and 'tools-tests.xml' in step['run']
               for step in workflow['jobs']['validate']['steps'])
