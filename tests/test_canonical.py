import hashlib
import pytest
from hermes_orca_gate.canonical import canonical_bytes, canonical_digest


def test_canonical_json_golden_utf8_and_ordering():
    value = {'z': None, 'a': '\u96ea', 'nested': {'false': False, 'n': 3}}
    expected = b'{"a":"\xe9\x9b\xaa","nested":{"false":false,"n":3},"z":null}'
    assert canonical_bytes(value) == expected
    assert canonical_digest(value) == hashlib.sha256(expected).hexdigest()


def test_canonical_rejects_floats_and_non_string_keys():
    with pytest.raises(ValueError, match='float'):
        canonical_bytes({'age': 1.5})
    with pytest.raises(ValueError, match='string'):
        canonical_bytes({1: 'bad'})
