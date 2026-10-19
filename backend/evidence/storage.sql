CREATE TABLE evidence_metadata (
    document_version_id TEXT PRIMARY KEY REFERENCES document_versions(id),
    filename TEXT NOT NULL,
    title TEXT NOT NULL
);
CREATE TABLE evidence_requests (
    scope TEXT NOT NULL,
    request_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    document_version_id TEXT NOT NULL REFERENCES document_versions(id),
    PRIMARY KEY(scope,request_key)
);
CREATE TABLE evidence_extractions (
    id TEXT PRIMARY KEY,
    document_version_id TEXT NOT NULL REFERENCES document_versions(id),
    parser_version TEXT NOT NULL,
    summary_json TEXT NOT NULL CHECK(json_valid(summary_json)),
    UNIQUE(document_version_id,parser_version)
);
CREATE TABLE evidence_pages (
    extraction_id TEXT NOT NULL REFERENCES evidence_extractions(id),
    pdf_page INTEGER NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    PRIMARY KEY(extraction_id,pdf_page)
);
CREATE TABLE evidence_blocks (
    id TEXT PRIMARY KEY,
    extraction_id TEXT NOT NULL REFERENCES evidence_extractions(id),
    document_version_id TEXT NOT NULL REFERENCES document_versions(id),
    pdf_page INTEGER NOT NULL,
    order_in_page INTEGER NOT NULL,
    normalized_text TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json))
);
CREATE TABLE evidence_materials (
    id TEXT PRIMARY KEY,
    extraction_id TEXT NOT NULL REFERENCES evidence_extractions(id),
    pdf_page INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json))
);
CREATE INDEX evidence_block_order ON evidence_blocks(extraction_id,pdf_page,order_in_page);
CREATE INDEX evidence_material_page ON evidence_materials(extraction_id,pdf_page,kind);
