# Published storage fixture

`published-schema-86.sql` was generated using the released `v0.7.0` Python source (`4031cd8`), with real Git delivery and application operations. Schema 86 is identical in `v0.6.0`. It contains completed code, a retained output, a dependent task and subtask, a question, document revisions and a second workspace. Absolute fixture paths are normalized; no credentials or native sessions are included.

The SQL serializes logical FTS rows with `CREATE VIRTUAL TABLE`, avoiding SQLite's nonportable virtual-table dump internals. `published-schema-86.json` supplies retained file bytes and their original checksum. Upgrade tests compare authored rows, delivery evidence, file bytes and the verified recovery database, then exercise reads and a new write.
