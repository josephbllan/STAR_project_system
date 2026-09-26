# Backup, restore and readiness

Restoration is the capability. A backup that has never been restored is not one.

## Daily backup (local composition)

PostgreSQL and Redis are loopback-bound containers. The application process is not in a container.

```
docker compose exec db pg_dump -U shoerag_owner -Fc shoerag > var/backups/shoerag.dump
```

Write-ahead logs are archived by the container's default configuration. Retention: 30 days of dumps, 7 days of WAL, matching `db_schema.md` section 13.

Evidence bytes live under `SHOERAG_STORAGE_ROOT`. Copy that directory alongside the dump. Object versioning is the on-premises equivalent.

## Restore

1. Stop workers and the API so nothing writes during restore.
2. Recreate the database as owner: `dropdb` / `createdb` as `shoerag_owner`.
3. `pg_restore -U shoerag_owner -d shoerag var/backups/shoerag.dump`
4. Restore `SHOERAG_STORAGE_ROOT`.
5. Run `python manage.py migrate --database=migration` (should be a no-op).
6. Hit `GET /healthz` (liveness) and `GET /api/v1/version/` (session).
7. Run `pytest -m "not ml"` against the restored database only after pointing `DATABASE_URL` at a *copy*. Never run the suite against the restored production alias.

## Export-volume alert (SEC-100)

Query served by `ix_audit_auditevent_actor_action_time`:

```sql
SELECT actor_username, count(*)
FROM audit_auditevent
WHERE action LIKE 'export.%'
  AND occurred_at >= now() - interval '24 hours'
GROUP BY actor_username
HAVING count(*) > 20;
```

An empty result is the healthy state. A row is an alert.

## Readiness

The process is ready when: PostgreSQL accepts a connection as `shoerag_app`, Redis answers `PING` on loopback, `SHOERAG_STORAGE_ROOT` is writable, and `GET /healthz` returns `{"status":"ok"}`. Readiness is not liveness: a slow database must not restart the process, which is why `/healthz` touches nothing.
