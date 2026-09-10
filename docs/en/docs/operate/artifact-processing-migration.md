---
title: Migrate Artifact processing state
---

Existing Topic Memory processing databases need explicit maintenance before
starting the Scope-based Artifact Processing Supervisor. A new, empty database
is initialized automatically. An existing database with no completed migration
marker is rejected before background processing starts.

## Plan and apply

Inspect the plan with the deployment configuration that will run after migration:

```shell
powercontext server processing-migrate --action plan --env-file .env
```

Back up the database. Stop every old background candidate and its Workers,
disable their automatic restart, and pause input writes and explicit triggers.
The command cannot establish those external conditions for you. Then run:

```shell
powercontext server processing-migrate --action apply --env-file .env \
  --migration-id rfc1515 --batch-size 100 --maintenance-confirmed
powercontext server processing-migrate --action verify --env-file .env
```

`apply` commits each bounded step separately. If it stops, repeat it with the
same migration ID and configuration. Do not clear the migration marker, receipts
or old automatic targets to restart it. A receipt and its intent/Topic target
updates commit together, so repeating a committed page does not increment the
request counter again. MySQL schema changes are individually checked on resume;
the presence of an added column alone does not mark the data migration complete.

Only resume API writes and background processes after verification reports
`ready: true`. Keep all replicas on the same mode and binding/Family mapping.
Ordinary startup rejects incomplete migration and an incompatible deployment
manifest. Changing a schedule, Worker budget or timeout does not require a mode
migration.

## Preserved state

The migration keeps Topic Pending, Source Cursor values and CAS generations,
evidence and publication data. Uncovered Sources become ordinary dirty state;
unconfirmed flushes and unfinished automatic targets become accepted calls.
Topic's frozen input target remains in its own table. A later ordinary Source
does not extend a known old automatic target.

The old implementation could start catch-up calls outside an automatic wave and
finish that wave while those calls were still running. Consequently, the absence
of a wave row cannot prove that a dirty Scope was never admitted. When no frozen
target establishes the boundary, migration conservatively retains a pending
Topic Scope as an accepted invocation. Its processor resumes from the Cursor.
Closing the new automatic schedule does not cancel that recovery responsibility.
Sources with no old Pending are calibrated as ordinary dirty, without accepting
new business actions.

The old automatic completion timestamp is retained as the new scheduling
checkpoint; migration does not restart the remaining interval from the current
time. Old automatic rows are cleared only after their durable replacement and
Cursor consistency have been verified. The obsolete table and migration receipts
remain available; the runtime no longer treats that table as an active queue.

## Switch between global and dedicated

Use the same coordinated shutdown and write pause. Select the new mode in every
replica's configuration, plan the change, then apply it with a **new migration
ID**, for example `global-to-dedicated-1`. Switching back also needs a new ID.
The migration advances and invalidates all old Lease generations before startup
under the new mode. It preserves request counters, dirty markers, Topic targets,
Cursor values and schedule checkpoints. It does not replay the legacy import.

SQLite and embedded SeekDB keep their single-host `all` role in either mode.
Changing mode does not enable split-process deployment for those backends. A
rolling deployment mixing the two modes is unsupported.
