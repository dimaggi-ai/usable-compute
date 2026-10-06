import json, os, re, subprocess, sys
from pathlib import Path
sys.path.insert(0, 'integration/receiver/tools')
from export_sources import export
names = ('cooling-pue-ladder', 'network-vs-more-gpus', 'reliability-economics',
         'research', 'scheduler-vs-more-gpus', 'slice-packer-torus', 'span-contract', 'usable-compute')
lock = json.loads(Path('integration/receiver/src/dimaggi_receiver/sources.lock.json').read_text())
if set(lock['repositories']) != set(names):
    raise ValueError('source set changed; review CI source scope')
root = Path(os.environ['RUNNER_TEMP']) / 'receiver-inputs'
root.mkdir(exist_ok=True)
sources = {}
for name in names:
    commit = lock['repositories'][name]['commit']
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('exact source commit required')
    target = root / name
    subprocess.run(['git', 'init', '-q', str(target)], check=True, timeout=30)
    remote = 'usable-compute' if name == 'research' else name
    subprocess.run(['git', '-C', str(target), 'fetch', '--depth=1',
                    'https://github.com/dimaggi-ai/' + remote + '.git', commit],
                   check=True, timeout=90)
    sources[name] = str(target)
export(lock, sources, root / 'export')
