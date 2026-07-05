import unittest

import numpy as np

from arrow_puzzle.solver import click_matrix_from_centers, solve_board


class SolverTests(unittest.TestCase):
    def test_single_click_solution(self):
        centers = _hex_radius_one_centers()
        matrix = click_matrix_from_centers(centers)
        known_taps = np.zeros(len(centers), dtype=int)
        known_taps[0] = 1
        deltas = matrix @ known_taps % 6
        values = [((1 - int(delta) - 1) % 6) + 1 for delta in deltas]

        solution = solve_board(values, matrix)
        target = np.array([(1 - value) % 6 for value in values])
        self.assertTrue(np.all((matrix @ np.array(solution.taps) - target) % 6 == 0))


def _hex_radius_one_centers():
    centers = []
    spacing = 100.0
    for q in range(-1, 2):
        for r in range(-1, 2):
            s = -q - r
            if max(abs(q), abs(r), abs(s)) <= 1:
                x = spacing * (q + r / 2)
                y = spacing * (0.8660254038 * r)
                centers.append((x, y))
    return centers


if __name__ == "__main__":
    unittest.main()
