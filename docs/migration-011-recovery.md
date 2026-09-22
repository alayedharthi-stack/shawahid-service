# Original migration 011 recovery

## Evidence and provenance

Deployment `bcb6eb28-f116-4674-ac9d-a36f9ebceadc`, commit
`2f44c70ddf3f63bbe52af363ed69d90efdf0d7d9`, failed at
2026-09-22T19:11:50.905796937Z with:

```
ERROR [alembic.util.messaging] Can't locate revision identified by '011'
```

Full local Git history (not shallow) and all three remote branches contained
001–010 only; no tag or migration-history match for 011 was found. Old Railway
build-log queries returned no retained content. Local Docker daemon was not
available. The known working deployment was a CLI upload, not a Git commit:
`a868c873-bec4-4cc7-b57f-e73a2ac371f6`, snapshot
`202ac44a-b894-411f-9714-1f64036322b4`.

After the scoped console action was permitted, only the migration directory
listing and the original file were read from that deployment. The file was
downloaded via Railway's Files UI, without changing the container or database:

`/app/alembic/versions/011_teacher_knowledge_question_bank.py`

- Length: 7513 bytes, original CRLF line endings preserved.
- SHA-256 measured both in the source container and downloaded local file:
  `a297e750a65898afd81aa21f36fbb3286c99cbf10c984cee5512226152481100`.
- `revision = "011"`, `down_revision = "010"`.
- `depends_on = None`, `branch_labels = None`.
- Full chain: base → 001 → 002 → 003 → 004 → 005 → 006 → 007 → 008 → 009 → 010 → 011.
- Original adds teacher_knowledge_profiles, question_banks, bank_questions,
  exam_records and their indexes/foreign keys. No guessed substitute was used.
- A path-specific .gitattributes exception prevents normalization of original
  bytes. The provenance test pins the full SHA-256.

No stamp, version-table edits, production upgrade/downgrade, secret extraction,
or changes to Nahla were performed during recovery. This PR restores the original
migration only; it does not claim to reconcile every CLI-only application change.

## Verification and deployment gate

Tests verify the original hash and complete Alembic chain. Isolated CI uses a
disposable PostgreSQL 15 database, migrates base→010, inserts a synthetic teacher,
migrates 010→011, inserts synthetic records, and repeats upgrade-to-head while
already at 011. It verifies preservation and resolution without duplicate table
creation. A synthetic pg_dump/pg_restore test checks the test database restore;
**this is not evidence of a production backup**.

Production remains on the previous working CLI image. PR #2 and worksheet
activation remain closed until #1 plus this repair deploys successfully.
Before deployment a real recoverable production backup must be verified.
At inspection on 2026-09-22, the Shawahid application volume Backups page showed
`No backup schedule`; the project's Postgres Backups page showed
`No volume instance found for this service`. No production backup or restore
has therefore been proven. Do not restart Postgres, attach a new empty mount,
or assume the application volume contains the database. Resolve and verify the
database backup/persistence gate first, without exposing data or credentials.

Scoped project: `28c9a40b-927d-4e28-8e78-38a5472ef7c0` (shawahid-service).
Application: `460b0d9b-2050-407a-9d0c-c0587f5e8886`.
Postgres: `d5f5422a-298b-4351-a788-e7c34b7237af`.
No settings, data, or deployments in the separate Nahla SaaS project were changed.
