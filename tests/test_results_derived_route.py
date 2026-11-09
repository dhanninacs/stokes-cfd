"""The field route's fallback for a name the run did not write: a field function or an expression is
packed in the same codec, with its own range, and a bad one is a 400 with the reason."""

from __future__ import annotations

import json
import struct

import numpy as np
import pytest
from fastapi import HTTPException

from stokes.errors import ExpressionError
from stokes.server.app import _derived_payload


def test_a_derived_field_is_packed_with_its_own_range() -> None:
    def derived(run: str, target: str, field: str, plane: object) -> tuple[np.ndarray, str]:
        assert (run, target, field, plane) == ("r_1", "slice", "p_total", None)
        return np.array([1.0, np.nan, 3.0]), "m2/s2"

    payload = _derived_payload(derived, "r_1", "slice", "p_total", None)
    assert payload is not None
    (length,) = struct.unpack_from("<I", payload, 0)
    header = json.loads(payload[4 : 4 + length])
    assert header["field"] == "p_total" and header["units"] == "m2/s2"
    assert (header["min"], header["max"]) == (1.0, 3.0)
    values = np.frombuffer(payload, dtype="<f4", offset=4 + length)
    assert values.tolist() == [1.0, 1.0, 3.0]


def test_no_calculator_means_no_payload_and_a_bad_expression_is_a_400() -> None:
    assert _derived_payload(None, "r_1", "slice", "q", None) is None

    def broken(*_args: object) -> tuple[np.ndarray, str]:
        raise ExpressionError("unknown name 'q'")

    with pytest.raises(HTTPException) as caught:
        _derived_payload(broken, "r_1", "slice", "q", None)
    assert caught.value.status_code == 400
