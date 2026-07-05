from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class Solution:
    taps: list[int]
    residual: list[int]
    total_taps: int


def click_matrix_from_centers(centers: list[tuple[float, float]]) -> np.ndarray:
    """Build the modulo-6 click matrix from detected cell centers."""
    if not centers:
        raise ValueError("no cell centers were provided")

    distances: list[float] = []
    for i, a in enumerate(centers):
        nearest = min(
            _dist(a, b) for j, b in enumerate(centers) if i != j
        )
        distances.append(nearest)
    spacing = float(np.median(distances))
    edge_limit = spacing * 1.28

    n = len(centers)
    matrix = np.zeros((n, n), dtype=int)
    for col, a in enumerate(centers):
        matrix[col, col] = 1
        for row, b in enumerate(centers):
            if row == col:
                continue
            if _dist(a, b) <= edge_limit:
                matrix[row, col] = 1
    return matrix % 6


def solve_board(values: Iterable[int], matrix: np.ndarray) -> Solution:
    """Solve A*x = target over Z/6Z, where target makes every value become 1."""
    values_list = list(values)
    if matrix.shape != (len(values_list), len(values_list)):
        raise ValueError("matrix size does not match number of values")

    target = np.array([(1 - value) % 6 for value in values_list], dtype=int)
    mod2 = _solve_prime(matrix % 2, target % 2, 2)
    mod3 = _solve_prime(matrix % 3, target % 3, 3)

    x2 = _choose_small_solution(mod2, 2)
    x3 = _choose_small_solution(mod3, 3)
    taps = [_crt_mod_2_3(int(a), int(b)) for a, b in zip(x2, x3)]

    residual = (matrix @ np.array(taps, dtype=int) - target) % 6
    if np.any(residual):
        raise RuntimeError(f"solver produced a non-zero residual: {residual.tolist()}")
    return Solution(taps=taps, residual=residual.tolist(), total_taps=sum(taps))


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return float(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5)


@dataclass(frozen=True)
class PrimeSolution:
    particular: np.ndarray
    basis: list[np.ndarray]


def _solve_prime(matrix: np.ndarray, rhs: np.ndarray, prime: int) -> PrimeSolution:
    a = np.concatenate([matrix.copy() % prime, rhs.reshape(-1, 1) % prime], axis=1)
    rows, cols = matrix.shape
    pivot_cols: list[int] = []
    r = 0

    for c in range(cols):
        pivot = None
        for candidate in range(r, rows):
            if a[candidate, c] % prime:
                pivot = candidate
                break
        if pivot is None:
            continue
        if pivot != r:
            a[[r, pivot]] = a[[pivot, r]]

        inv = pow(int(a[r, c]), -1, prime)
        a[r, :] = (a[r, :] * inv) % prime
        for rr in range(rows):
            if rr == r:
                continue
            factor = a[rr, c] % prime
            if factor:
                a[rr, :] = (a[rr, :] - factor * a[r, :]) % prime
        pivot_cols.append(c)
        r += 1
        if r == rows:
            break

    for rr in range(r, rows):
        if not np.any(a[rr, :cols] % prime) and a[rr, cols] % prime:
            raise ValueError(f"board is inconsistent modulo {prime}")

    free_cols = [c for c in range(cols) if c not in pivot_cols]
    particular = np.zeros(cols, dtype=int)
    for row, col in enumerate(pivot_cols):
        particular[col] = a[row, cols] % prime

    basis: list[np.ndarray] = []
    for free_col in free_cols:
        vector = np.zeros(cols, dtype=int)
        vector[free_col] = 1
        for row, pivot_col in enumerate(pivot_cols):
            vector[pivot_col] = (-a[row, free_col]) % prime
        basis.append(vector)

    return PrimeSolution(particular=particular, basis=basis)


def _choose_small_solution(solution: PrimeSolution, prime: int) -> np.ndarray:
    if not solution.basis:
        return solution.particular.copy()

    # Nullity is usually small for these boards. If it is unexpectedly large,
    # use the particular solution and avoid an exponential pause.
    if len(solution.basis) > 10:
        return solution.particular.copy()

    best = solution.particular.copy()
    best_cost = int(np.sum(best))
    for coeffs in product(range(prime), repeat=len(solution.basis)):
        candidate = solution.particular.copy()
        for coeff, basis_vec in zip(coeffs, solution.basis):
            if coeff:
                candidate = (candidate + coeff * basis_vec) % prime
        cost = int(np.sum(candidate))
        if cost < best_cost:
            best = candidate
            best_cost = cost
    return best


def _crt_mod_2_3(mod2: int, mod3: int) -> int:
    for value in range(6):
        if value % 2 == mod2 and value % 3 == mod3:
            return value
    raise AssertionError("unreachable CRT state")
