CREATE TABLE paper_report_runs (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES document_versions(id),
    request_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(document_id, request_key)
);
