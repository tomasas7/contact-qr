# 2. Store generated QR images in PostgreSQL (`bytea`)

Date: 2026-10-25 · Status: Accepted

## Context
Generated QR images must persist across restarts, container recreation and redeploys,
alongside contacts and request logs.

## Decision
Render the PNG once, when a contact is saved, and store it in `contacts.qr_png` together
with the exact vCard payload (`contacts.vcard`). The column is deferred, so lists never
load it.

## Alternatives
* PNG files on a second volume, with paths in the DB: two places to back up, and files
  and rows can drift apart (orphaned files, missing images).
* Re-render on every request: cheap, but then the "stored image" isn't really stored, and
  output could change if the library changes.

## Consequences
* All state is in one database and one volume, backed up with one `pg_dump`.
* The contact and its image are written in one transaction.
* About 1–5 KB per contact in the DB, which is negligible here. At very large scale,
  object storage would be the better choice.
