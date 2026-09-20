"""Receiver byte limits preserve complete synthetic journal import semantics.

These vectors use the existing exposed synthetic export fixture. Producer-side
complete-export refusal is tested in TENWA; padding does not authenticate a source.
"""
import copy
import json

import pytest

from dimaggi_receiver.journal_import import import_journal, journal_events
from dimaggi_receiver.jsonio import MAX_BYTES
from dimaggi_receiver.observations import ObservationError, ObservationStore
from test_journal_import import JOURNAL, T9, encoded, export, intent, setup


def bounded_payload():
    raw = encoded(export(("prepared",))).encode("utf-8")
    # The newline is part of the transport budget, just as for the Go encoder.
    padded = raw + b" " * (MAX_BYTES - len(raw) - 1) + b"\n"
    assert len(padded) == 4 * 1024 * 1024
    return padded


@pytest.mark.parametrize("text_input", [False, True])
def test_exact_limit_including_newline_imports_complete_synthetic_history(text_input):
    raw = bounded_payload()
    supplied = raw.decode("utf-8") if text_input else raw
    with ObservationStore(":memory:") as store:
        setup(store)
        result = import_journal(store, supplied, request_id="request-1",
                                expected_journal_id=JOURNAL, recorded_at_utc=T9)
        assert result["imported_events"] == 2
        assert result["permission"] == "not_granted"
        assert result["dispatch_possible"] is False
        assert result["execution_proven"] is False
        assert result["workload_outcome"] == "not_observed"
        history = store.history("request-1")
        again = import_journal(store, supplied, request_id="request-1",
                               expected_journal_id=JOURNAL, recorded_at_utc=T9)
        assert again["imported_events"] == 0
        assert store.history("request-1") == history


@pytest.mark.parametrize("text_input", [False, True])
def test_one_byte_over_limit_refuses_before_appending(text_input):
    raw = bounded_payload() + b" "
    assert len(raw) == MAX_BYTES + 1
    supplied = raw.decode("utf-8") if text_input else raw
    with ObservationStore(":memory:") as store:
        setup(store)
        with pytest.raises(ObservationError, match="invalid journal JSON"):
            import_journal(store, supplied, request_id="request-1",
                           expected_journal_id=JOURNAL, recorded_at_utc=T9)
        assert store.history("request-1") == []


def test_multibyte_boundary_is_utf8_bytes_not_character_count():
    candidate = export(("prepared",))
    candidate["records"][0]["snapshot"]["preview"]["core_envelope"]["padding"] = "é"
    raw = json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    padded = raw + b" " * (MAX_BYTES - len(raw) - 1) + b"\n"
    text = padded.decode("utf-8")
    assert len(text) < len(padded) == MAX_BYTES
    assert len(journal_events(text, intent=intent(), expected_journal_id=JOURNAL)) == 2
    with pytest.raises(ObservationError, match="invalid journal JSON"):
        journal_events(text + " ", intent=intent(), expected_journal_id=JOURNAL)


def test_omitted_global_record_is_not_a_request_focused_projection():
    candidate = export()
    del candidate["records"][1]
    with ObservationStore(":memory:") as store:
        setup(store)
        with pytest.raises(ObservationError, match="source sequences"):
            import_journal(store, encoded(candidate), request_id="request-1",
                           expected_journal_id=JOURNAL, recorded_at_utc=T9)
        assert store.history("request-1") == []


def test_invalid_unselected_request_is_validated_before_any_append():
    candidate = export(("prepared",))
    foreign = copy.deepcopy(candidate["records"][0])
    foreign["source_sequence"] = 3
    foreign["record_digest"] = "sha256:" + "f" * 64
    snapshot = foreign["snapshot"]
    snapshot["request_id"] = "another-request"
    snapshot["input"]["request_id"] = "another-request"
    snapshot["preview"]["request_id"] = "another-request"
    snapshot["permission"] = "granted"
    candidate["records"].append(foreign)
    with ObservationStore(":memory:") as store:
        setup(store)
        with pytest.raises(ObservationError, match="cannot grant permission"):
            import_journal(store, encoded(candidate), request_id="request-1",
                           expected_journal_id=JOURNAL, recorded_at_utc=T9)
        assert store.history("request-1") == []
