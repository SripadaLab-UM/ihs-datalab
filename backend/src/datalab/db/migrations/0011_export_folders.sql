-- Export folders (Settings → Export folders): whether a folder is offered
-- as a destination for exports and workflow deliveries. Turning it off keeps
-- the folder (and any workflow key mapped to it) but refuses to write there.
ALTER TABLE export_destinations ADD COLUMN offered INTEGER NOT NULL DEFAULT 1;

-- What a delivery's folder was called, and which sync app's folder it was
-- in (dropbox, onedrive, box, google_drive, icloud), when it was delivered:
-- so the run's page can say "Saved to Lab Dropbox (on this computer)" later,
-- even if the folder has since been renamed or removed.
ALTER TABLE workflow_run_deliveries ADD COLUMN destination_name TEXT;
ALTER TABLE workflow_run_deliveries ADD COLUMN sync_provider TEXT;
