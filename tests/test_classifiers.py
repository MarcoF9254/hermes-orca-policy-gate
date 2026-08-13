import itertools
from hermes_orca_gate.classifiers import classify_dispatch, classify_worker


def test_dispatch_classifier_total():
    expected = {'pending': 'OBSERVATION_PENDING', 'dispatched': 'OBSERVATION_PENDING', 'completed': 'RUNTIME_UNKNOWN', 'failed': 'FAILED', 'circuit_broken': 'CIRCUIT_BROKEN'}
    for status, result in expected.items(): assert classify_dispatch(status) == result
    for status in (None, '', 'COMPLETED', 'new'): assert classify_dispatch(status) == 'RUNTIME_UNKNOWN'
    assert classify_dispatch('completed', completion_evidence=True) == 'OK'


def test_worker_classifier_all_pairs_and_precedence():
    dispatches = ['pending', 'dispatched', 'completed', 'failed', 'circuit_broken']
    workers = ['starting', 'ready', 'start_unknown', 'failed', 'succeeded', 'stopping', 'stop_unknown', 'stopped', 'abandoned']
    assert len([classify_worker(d, w) for d, w in itertools.product(dispatches, workers)]) == 45
    assert classify_worker('circuit_broken', 'failed') == 'CIRCUIT_BROKEN'
    assert classify_worker('completed', 'start_unknown') == 'RUNTIME_UNKNOWN'
    assert classify_worker('failed', 'ready') == 'FAILED'
    assert classify_worker('pending', 'stopped') == 'STOPPED'
    assert classify_worker('completed', 'ready') == 'RUNTIME_UNKNOWN'
    assert classify_worker('completed', 'ready', completion_evidence=True) == 'OK'
    assert classify_worker('pending', 'succeeded') == 'OK'
    assert classify_worker('pending', None) == 'RUNTIME_UNKNOWN'
