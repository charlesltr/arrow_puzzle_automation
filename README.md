# Arrow Puzzle Solver

Local helper for recognizing and solving the Exponential Idle arrow puzzle from a screenshot.

Planned workflow:

1. Select the puzzle area on screen.
2. Detect the honeycomb cells and read values `1..6`.
3. Solve the board as a modulo-6 linear puzzle.
4. Optionally click the solution back into the game.
