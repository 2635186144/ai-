CREATE TABLE fact_versions (
    ledger_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    extraction_id TEXT NOT NULL REFERENCES evidence_extractions(id),
    reading_revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(ledger_id, revision),
    FOREIGN KEY(extraction_id, reading_revision) REFERENCES reading_versions(extraction_id, revision)
);
CREATE TABLE fact_requests (
    ledger_id TEXT NOT NULL,
    request_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','succeeded','failed')),
    base_revision INTEGER NOT NULL,
    result_revision INTEGER,
    error_code TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(ledger_id, request_key),
    FOREIGN KEY(ledger_id, base_revision) REFERENCES fact_versions(ledger_id, revision),
    FOREIGN KEY(ledger_id, result_revision) REFERENCES fact_versions(ledger_id, revision)
);
CREATE TRIGGER fact_versions_no_update BEFORE UPDATE ON fact_versions BEGIN
    SELECT RAISE(ABORT, 'fact history is immutable');
END;
CREATE TRIGGER fact_versions_no_delete BEFORE DELETE ON fact_versions BEGIN
    SELECT RAISE(ABORT, 'fact history is immutable');
END;
