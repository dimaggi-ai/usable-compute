"""Identity must survive JSON transport without Unicode replacement or aliasing."""
import io
import json

import pytest

from dimaggi_receiver.bindings import policy_input
from dimaggi_receiver.cli import main
from dimaggi_receiver.jsonio import canonical, digest, dumps, loads


@pytest.mark.parametrize("raw", [
    '"\\ud800"', '"\\udfff"', '{"\\ud800": 1}',
    '{"nested": ["\\udfff"]}', '"\\ud800A"', '"\\udfff\\ud800"',
])
def test_unpaired_escaped_surrogates_rejected(raw):
    with pytest.raises(ValueError, match="Unicode scalar"):
        loads(raw)


@pytest.mark.parametrize("encode", [canonical, dumps, digest])
@pytest.mark.parametrize("value", ["\ud800", {"\udfff": "key"},
                                   {"nested": ["\ud800"]}, ("\ud800\udc00",)])
def test_direct_python_strings_cannot_bypass_boundary(encode, value):
    with pytest.raises(ValueError, match="Unicode scalar"):
        encode(value)


@pytest.mark.parametrize("request_id", ["\ud800", "\udfff"])
def test_request_identity_rejected_before_report_parsing(request_id):
    with pytest.raises(ValueError, match="Unicode scalar"):
        policy_input(b"{}", request_id)


def test_valid_pair_and_literal_replacement_character_remain_distinct():
    paired = loads('"\\ud83d\\ude80"')
    replacement = loads('"\\ufffd"')
    assert paired == "🚀"
    assert replacement == "�"
    assert digest(paired) != digest(replacement)
    assert loads(dumps({paired: replacement})) == {paired: replacement}


def test_valid_non_bmp_key_and_repeated_container():
    shared = {"🚀": "valid"}
    assert loads(dumps([shared, shared])) == [shared, shared]


def test_cycle_still_rejected_without_infinite_scalar_walk():
    value = []
    value.append(value)
    with pytest.raises(ValueError, match="Circular reference"):
        canonical(value)


@pytest.mark.parametrize("raw", ['{"\\ud800":1,"\\ud800":2}',
                                  '{"nested":{"\\udfff":1,"\\udfff":2}}'])
def test_invalid_duplicate_keys_return_structured_cli_error(raw, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(raw.encode("utf-8"))))
    assert main(["evaluate", "--sources", "/unused-invalid-input"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["schema_version"] == "dimaggi-receiver-error/v1"
    assert error["message"] == "JSON strings must contain only Unicode scalar values"
    assert error["execution_authorized"] is False
    assert error["mutation_request"] is None
