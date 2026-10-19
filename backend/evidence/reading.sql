CREATE TABLE reading_versions (
    extraction_id TEXT NOT NULL REFERENCES evidence_extractions(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(extraction_id, revision)
);
CREATE TABLE reading_requests (
    extraction_id TEXT NOT NULL REFERENCES evidence_extractions(id),
    request_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY(extraction_id, request_key),
    FOREIGN KEY(extraction_id, revision) REFERENCES reading_versions(extraction_id, revision)
);
CREATE TRIGGER reading_versions_no_update BEFORE UPDATE ON reading_versions BEGIN
    SELECT RAISE(ABORT, 'reading history is immutable');
END;
CREATE TRIGGER reading_versions_no_delete BEFORE DELETE ON reading_versions BEGIN
    SELECT RAISE(ABORT, 'reading history is immutable');
END;
