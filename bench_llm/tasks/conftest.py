# This file prevents pytest from collecting tests under tasks/ when running
# from the repository root. The tasks/ directory contains benchmark fixtures
# (solution code, test harnesses) that are executed by src validators in
# isolated workspaces, not by the project's own test suite.

collect_ignore_glob = ["**/*.py"]