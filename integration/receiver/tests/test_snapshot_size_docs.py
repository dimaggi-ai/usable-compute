from pathlib import Path


def test_documented_initial_snapshot_measurement():
    root = Path(__file__).resolve().parents[3]
    size = 7_434_240
    for name in ('README.md', 'integration/receiver/README.md',
                 'integration/receiver/TOPOLOGY-COLLECTION.md',
                 'integration/receiver/tools/README.md'):
        text = (root / name).read_text()
        assert f'{size:,} bytes per initial publication' in text, name
        assert f'{3 * size:,} bytes per cycle' in text, name
    assert f'{3 * size // 30:,} bytes/s' in (root / 'integration/receiver/TOPOLOGY-COLLECTION.md').read_text()
