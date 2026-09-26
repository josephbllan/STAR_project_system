-- Local composition mirrors the privilege separation of db_schema.md section 12.
-- shoerag_owner owns every object and is used by the migration job only.
-- shoerag_app is used by the api and worker processes and holds no schema privileges.
-- shoerag_readonly is used for diagnostics.
--
-- The revocations that make the audit trail append-only, and that forbid deletion of
-- evidential rows, are granted by migration rather than here: they belong with the
-- tables they protect and must be reviewable in the migration history (DB-43, DB-25).

CREATE ROLE shoerag_app WITH LOGIN PASSWORD 'dev_app_password';
CREATE ROLE shoerag_readonly WITH LOGIN PASSWORD 'dev_readonly_password';

GRANT CONNECT ON DATABASE shoerag TO shoerag_app, shoerag_readonly;
GRANT USAGE ON SCHEMA public TO shoerag_app, shoerag_readonly;

ALTER DEFAULT PRIVILEGES FOR ROLE shoerag_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO shoerag_app;
ALTER DEFAULT PRIVILEGES FOR ROLE shoerag_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO shoerag_app;
ALTER DEFAULT PRIVILEGES FOR ROLE shoerag_owner IN SCHEMA public
  GRANT SELECT ON TABLES TO shoerag_readonly;
