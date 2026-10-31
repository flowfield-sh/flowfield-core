# Published 0.10.1 store

Generated from the existing synthetic published-schema-92.sql fixture by opening it with the actual v0.10.1 source at c658ec6. The resulting store has schema93. SQL export preserves authored rows and rebuilds the SQLite FTS virtual table from its declared schema and logical rows instead of relying on writable_schema or exporting FTS shadow tables. No personal workspace data is included.

The schema94 tests exercise additive context-measurement migration, verified recovery copies, rollback and repeated startup against this fixture; existing published0.6.0 fixtures continue to cover skipped-version upgrades and retained output files.
