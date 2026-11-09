"""Array aliases and input checks shared by the exploration numerics.

Public functions take anything array-like and return ``float64`` numpy arrays; the checks here
turn shape and finiteness mistakes into ``InvalidInput`` with the argument name in the message.

Import rule: numpy only.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.typing import ArrayLike, NDArray

from stokes.explore.errors import InvalidInput

Floats = NDArray[np.float64]
Ints = NDArray[np.int64]
Bools = NDArray[np.bool_]

# What a JSON document can hold: used for metadata and the dict forms of tables and domains.
type Json = str | int | float | bool | list[Json] | dict[str, Json] | None


def as_matrix(
    values: ArrayLike,
    *,
    name: str = "points",
    columns: int | None = None,
    finite: bool = False,
) -> Floats:
    """``values`` as a 2-D float64 array (rows are points); 1-D input is one column."""
    array = np.asarray(values, dtype=np.float64)
    if array.ndim == 1:
        array = array[:, None]
    if array.ndim != 2:
        raise InvalidInput(f"{name} must be a 2-D array (points x variables), got {array.ndim}-D")
    if columns is not None and array.shape[1] != columns:
        raise InvalidInput(f"{name} must have {columns} columns, got {array.shape[1]}")
    if finite and not np.isfinite(array).all():
        raise InvalidInput(f"{name} contains NaN or infinite values")
    return array


def as_vector(values: ArrayLike, *, name: str = "values", finite: bool = False) -> Floats:
    """``values`` as a 1-D float64 array; a single column matrix is flattened."""
    array = np.asarray(values, dtype=np.float64)
    if array.ndim == 2 and array.shape[1] == 1:
        array = array[:, 0]
    if array.ndim != 1:
        raise InvalidInput(f"{name} must be a 1-D array, got shape {array.shape}")
    if finite and not np.isfinite(array).all():
        raise InvalidInput(f"{name} contains NaN or infinite values")
    return array


def check_names(names: Sequence[str] | None, count: int, *, prefix: str) -> tuple[str, ...]:
    """``names`` as a tuple of ``count`` strings, generating ``prefix1, prefix2, ...`` if absent."""
    if names is None:
        return tuple(f"{prefix}{i + 1}" for i in range(count))
    result = tuple(str(n) for n in names)
    if len(result) != count:
        raise InvalidInput(f"expected {count} names, got {len(result)}")
    return result
