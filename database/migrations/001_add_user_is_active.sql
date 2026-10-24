-- Adds the flag that lets administrators deactivate an account without
-- deleting it (its feedback history stays). Run once per database; the
-- setup command only runs it when the column is missing.
ALTER TABLE `USER` ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE AFTER password_hash;
