"""No database, no filesystem, no network.

This layer can be pure because `import-linter` forbids `retrieval/` from importing Django at
all; the contract and this layer support each other ( 5.1).
"""
