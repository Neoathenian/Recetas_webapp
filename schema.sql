-- db/schema.sql
-- Minimal bootstrap schema for authentication + privileges management.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'app') THEN
    EXECUTE 'CREATE SCHEMA app';
  END IF;
END$$;

-- Canonical users.
CREATE TABLE IF NOT EXISTS app."user" (
  id         BIGSERIAL PRIMARY KEY,
  name       TEXT NOT NULL,
  username   TEXT,
  email      TEXT NOT NULL UNIQUE,
  is_active  BOOLEAN NOT NULL DEFAULT TRUE
);

-- Role flags used by login, navigation, and the privileges page.
CREATE TABLE IF NOT EXISTS app.user_privileges (
  email      TEXT PRIMARY KEY REFERENCES app."user"(email) ON UPDATE CASCADE ON DELETE CASCADE,
  base_user  BOOLEAN NOT NULL DEFAULT FALSE,
  reviewer   BOOLEAN NOT NULL DEFAULT FALSE,
  editor     BOOLEAN NOT NULL DEFAULT FALSE,
  admin      BOOLEAN NOT NULL DEFAULT FALSE,
  creator    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_user_email_lower
  ON app."user" (lower(email));

CREATE INDEX IF NOT EXISTS idx_user_username_lower
  ON app."user" (lower(username));

CREATE INDEX IF NOT EXISTS idx_user_privileges_email_lower
  ON app.user_privileges (lower(email));
