import pytest
from dimaggi_receiver.quantities import normalize_quantity


def test_bits_bytes_and_binary_decimal_are_distinct():
    assert normalize_quantity(1, "GB/s", "bytes_per_second") == 1000000000
    assert normalize_quantity(1, "Gb/s", "bytes_per_second") == 125000000
    assert normalize_quantity(1, "GiB/s", "bytes_per_second") == 1073741824
    assert normalize_quantity("0.5", "GiB", "bytes") == 536870912
    assert normalize_quantity("0.25", "ms", "microseconds") == 250
    assert normalize_quantity("1.2", "kW", "watts") == 1200


@pytest.mark.parametrize(
    "value,unit,dimension",
    [
        (1, "gbps", "bytes_per_second"),
        (1, "GB", "watts"),
        (True, "B", "bytes"),
        (1.0, "B", "bytes"),
        ("NaN", "B", "bytes"),
        ("Infinity", "B", "bytes"),
        ("-1", "B", "bytes"),
        ("0.1", "B", "bytes"),
        ("1e1000000", "B", "bytes"),
        (2**53, "B", "bytes"),
    ],
)
def test_uncertain_or_inexact_quantities_refuse(value, unit, dimension):
    with pytest.raises(ValueError):
        normalize_quantity(value, unit, dimension)


def test_long_fraction_cannot_round_into_an_integer():
    with pytest.raises(ValueError):
        normalize_quantity("1.000000000000000000000000000001", "B", "bytes")


def test_callers_decimal_context_cannot_change_units():
    from decimal import localcontext

    with localcontext() as context:
        context.prec = 2
        assert normalize_quantity("1.25", "kW", "watts") == 1250
