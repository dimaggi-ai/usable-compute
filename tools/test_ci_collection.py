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
        result = subprocess.run([sys.executable, *args[1:], '--collect-only'], cwd=root,
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr + result.stdout
        collected += result.stdout
    import ast
    for path in (root/'tools').glob('test_*.py'):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith('test_'):
                assert path.name + '::' + node.name in collected
