CREATE TABLE workflow_runs (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES document_versions(id),
    section_id TEXT NOT NULL,
    reading_revision INTEGER NOT NULL,
    request_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(document_id, request_key)
);
CREATE TABLE workflow_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES workflow_runs(id),
    payload_json TEXT NOT NULL
);
CREATE TRIGGER workflow_events_no_update BEFORE UPDATE ON workflow_events
BEGIN SELECT RAISE(ABORT, 'workflow events are append only'); END;
CREATE TRIGGER workflow_events_no_delete BEFORE DELETE ON workflow_events
BEGIN SELECT RAISE(ABORT, 'workflow events are append only'); END;
