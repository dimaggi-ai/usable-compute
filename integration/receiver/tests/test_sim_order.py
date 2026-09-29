import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import pytest
from dimaggi_receiver.sources import source_lock


def test_direct_sim_normalizes_or_refuses_order_reversal(request):
    path=os.environ.get('DIMAGGI_TEST_RELIABILITY_SIM')
    if path is None:
        root=os.environ.get('DIMAGGI_TEST_SOURCES')
        if not root: pytest.skip('explicit reliability simulator or source bundle required')
        path=str(Path(root)/'reliability-economics/sim/reliability_sim.py')
    actual=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    pinned=source_lock()['repositories']['reliability-economics']['files_sha256']['sim/reliability_sim.py']
    if actual == pinned == '320821f1c26a1c55e504b867abd52926bd3af2965baf4edbca84fe48c1b0f4a2':
        request.node.add_marker(pytest.mark.xfail(strict=True, reason='METRICS-05: source re-pin is phase 2'))
    spec=importlib.util.spec_from_file_location('receiver_sim_order',path)
    module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)
    config=module.Config(nodes=64,horizon_h=24.,seed=20260923,single_rate_per_node_h=0.,spare_nodes=0)
    ordered=module.Sim(config,'auto-restart',[(1.,1),(8.,1)]).run()
    assert ordered['interruptions']==2
    try:
        reversed_result=module.Sim(config,'auto-restart',[(8.,1),(1.,1)]).run()
    except ValueError:
        return
    assert reversed_result == ordered
