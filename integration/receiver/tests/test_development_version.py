import inspect
from pathlib import Path
import tomllib

from packaging.version import Version
from dimaggi_receiver.topology_watch import WatchStore


def test_publication_access_api_has_distinct_development_version(tmp_path):
    metadata = tomllib.loads((Path(__file__).parents[1] / 'pyproject.toml').read_text())
    assert Version(metadata['project']['version']) > Version('0.1.9.dev2')
    assert Version(metadata['project']['version']).is_devrelease
    signature = inspect.signature(WatchStore)
    assert {'publication_mode', 'publication_gid'} <= signature.parameters.keys()
    store = WatchStore(tmp_path / 'db', 't', 'c', 'nodes', publication_mode=0o440)
    assert (tmp_path / 'db').stat().st_mode & 0o777 == 0o440
    store.close()
