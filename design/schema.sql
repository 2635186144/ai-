-- Canonical base DDL. EvidenceStore uses it only to initialize its isolated dev DB.
-- Existing legacy data/ files are not migrated. Review-run execution is not implemented.
PRAGMA foreign_keys = ON;

CREATE TABLE document_versions (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL CHECK(length(sha256)=64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    page_count INTEGER NOT NULL CHECK(page_count > 0),
    source_ref TEXT NOT NULL,
    material_role TEXT NOT NULL CHECK(material_role IN ('main','supplement')),
    created_at TEXT NOT NULL
);

CREATE TABLE review_runs (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK(state IN ('queued','parsing','reading','mapping','reviewing','challenging','verifying','synthesizing','needs_material','needs_configuration','partial','completed','failed','cancel_requested','cancelled')),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
    request_json TEXT NOT NULL CHECK(json_valid(request_json)),
    idempotency_key TEXT NOT NULL UNIQUE,
    request_sha256 TEXT NOT NULL,
    parent_run_id TEXT REFERENCES review_runs(id),
    base_snapshot_id TEXT REFERENCES review_snapshots(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK((parent_run_id IS NULL) = (base_snapshot_id IS NULL))
);

CREATE TABLE run_documents (
    run_id TEXT NOT NULL REFERENCES review_runs(id),
    document_version_id TEXT NOT NULL REFERENCES document_versions(id),
    PRIMARY KEY(run_id,document_version_id)
);

CREATE TABLE review_snapshots (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES review_runs(id),
    parent_snapshot_id TEXT REFERENCES review_snapshots(id),
    schema_version TEXT NOT NULL CHECK(schema_version='0.2'),
    report_status TEXT NOT NULL CHECK(report_status IN ('draft','partial','ready_for_human_review','human_reviewed')),
    origin TEXT NOT NULL CHECK(origin IN ('assistant_curated_design_sample','pipeline_output')),
    producer_json TEXT NOT NULL CHECK(json_valid(producer_json)),
    coverage_json TEXT NOT NULL CHECK(json_valid(coverage_json)),
    limitations_json TEXT NOT NULL CHECK(json_valid(limitations_json)),
    sealed INTEGER NOT NULL DEFAULT 0 CHECK(sealed IN (0,1)),
    created_at TEXT NOT NULL
);

-- Typed JSON nodes keep schema evolution manageable; relational keys protect IDs.
CREATE TABLE review_nodes (
    snapshot_id TEXT NOT NULL REFERENCES review_snapshots(id),
    node_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('source_region','reading_note','claim','experiment','finding','response','verification','human_attestation')),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    CHECK(json_type(payload_json,'$.id') IS 'text' AND json_extract(payload_json,'$.id')=node_id),
    PRIMARY KEY(snapshot_id,node_id)
);

CREATE TABLE source_regions (
    snapshot_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    document_version_id TEXT NOT NULL REFERENCES document_versions(id),
    pdf_page INTEGER NOT NULL CHECK(pdf_page > 0),
    page_width REAL NOT NULL CHECK(page_width > 0),
    page_height REAL NOT NULL CHECK(page_height > 0),
    x0 REAL NOT NULL CHECK(x0 >= 0),
    y0 REAL NOT NULL CHECK(y0 >= 0),
    x1 REAL NOT NULL,
    y1 REAL NOT NULL,
    CHECK(x0 < x1 AND x1 <= page_width AND y0 < y1 AND y1 <= page_height),
    PRIMARY KEY(snapshot_id,node_id),
    FOREIGN KEY(snapshot_id,node_id) REFERENCES review_nodes(snapshot_id,node_id)
);

-- Direction always means input -> dependent output, for invalidation traversal.
CREATE TABLE node_links (
    snapshot_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    dependent_id TEXT NOT NULL,
    relation TEXT NOT NULL CHECK(relation IN ('supports','depends_on','responds_to','verifies')),
    CHECK(source_id != dependent_id),
    PRIMARY KEY(snapshot_id,source_id,dependent_id,relation),
    FOREIGN KEY(snapshot_id,source_id) REFERENCES review_nodes(snapshot_id,node_id),
    FOREIGN KEY(snapshot_id,dependent_id) REFERENCES review_nodes(snapshot_id,node_id)
);

CREATE TABLE task_runs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES review_runs(id),
    stage TEXT NOT NULL CHECK(stage IN ('parsing','reading','mapping','reviewing','challenging','verifying','synthesizing')),
    state TEXT NOT NULL CHECK(state IN ('queued','running','succeeded','failed','cancelled','blocked')),
    cache_key TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
    lease_token TEXT,
    lease_expires_at TEXT,
    output_digest TEXT,
    CHECK(state != 'running' OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    UNIQUE(run_id,stage,cache_key)
);

CREATE TABLE budget_reservations (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES task_runs(id),
    attempt INTEGER NOT NULL CHECK(attempt >= 0),
    reserved_calls INTEGER NOT NULL CHECK(reserved_calls IN (0,1)),
    reserved_tokens INTEGER NOT NULL CHECK(reserved_tokens >= 0),
    actual_tokens INTEGER CHECK(actual_tokens >= 0),
    state TEXT NOT NULL CHECK(state IN ('reserved','settled','released')),
    UNIQUE(task_id,attempt)
);

CREATE TABLE human_decisions (
    id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('confirm','dispute','annotate','propose_correction')),
    rationale TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL CHECK(json_valid(evidence_ids_json)),
    correction_json TEXT CHECK(correction_json IS NULL OR json_valid(correction_json)),
    created_at TEXT NOT NULL,
    CHECK((action='propose_correction') = (correction_json IS NOT NULL)),
    FOREIGN KEY(snapshot_id,node_id) REFERENCES review_nodes(snapshot_id,node_id)
);

CREATE TABLE run_events (
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES review_runs(id),
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    created_at TEXT NOT NULL
);

CREATE INDEX events_by_run_cursor ON run_events(run_id,cursor);
CREATE INDEX nodes_by_kind ON review_nodes(snapshot_id,kind);
CREATE INDEX links_by_input ON node_links(snapshot_id,source_id);
CREATE INDEX tasks_by_state ON task_runs(state,lease_expires_at);

CREATE TRIGGER document_version_no_update BEFORE UPDATE ON document_versions
BEGIN SELECT RAISE(ABORT,'document versions are immutable'); END;

CREATE TRIGGER run_material_frozen BEFORE INSERT ON run_documents
WHEN (SELECT state FROM review_runs WHERE id=NEW.run_id) != 'queued'
BEGIN SELECT RAISE(ABORT,'run material set is frozen after dispatch'); END;

CREATE TRIGGER run_material_no_update BEFORE UPDATE ON run_documents
BEGIN SELECT RAISE(ABORT,'create a new run for changed materials'); END;

CREATE TRIGGER run_material_no_delete BEFORE DELETE ON run_documents
BEGIN SELECT RAISE(ABORT,'create a new run for changed materials'); END;

CREATE TRIGGER snapshot_sealed BEFORE UPDATE ON review_snapshots WHEN OLD.sealed=1
BEGIN SELECT RAISE(ABORT,'sealed snapshots are immutable'); END;

CREATE TRIGGER snapshot_no_delete BEFORE DELETE ON review_snapshots
BEGIN SELECT RAISE(ABORT,'snapshots are append only'); END;

CREATE TRIGGER node_insert_sealed BEFORE INSERT ON review_nodes
WHEN (SELECT sealed FROM review_snapshots WHERE id=NEW.snapshot_id)=1
BEGIN SELECT RAISE(ABORT,'cannot add to a sealed snapshot'); END;

CREATE TRIGGER node_update_sealed BEFORE UPDATE ON review_nodes
WHEN (SELECT sealed FROM review_snapshots WHERE id=OLD.snapshot_id)=1
 OR (SELECT sealed FROM review_snapshots WHERE id=NEW.snapshot_id)=1
BEGIN SELECT RAISE(ABORT,'cannot modify a sealed snapshot'); END;

CREATE TRIGGER node_delete_sealed BEFORE DELETE ON review_nodes
WHEN (SELECT sealed FROM review_snapshots WHERE id=OLD.snapshot_id)=1
BEGIN SELECT RAISE(ABORT,'cannot remove from a sealed snapshot'); END;

CREATE TRIGGER source_region_valid BEFORE INSERT ON source_regions
WHEN NEW.pdf_page > (SELECT page_count FROM document_versions WHERE id=NEW.document_version_id)
 OR (SELECT kind FROM review_nodes WHERE snapshot_id=NEW.snapshot_id AND node_id=NEW.node_id) != 'source_region'
 OR NOT EXISTS(SELECT 1 FROM review_snapshots s JOIN run_documents d ON d.run_id=s.run_id
               WHERE s.id=NEW.snapshot_id AND d.document_version_id=NEW.document_version_id)
BEGIN SELECT RAISE(ABORT,'source version, node kind or page is invalid for this run'); END;

CREATE TRIGGER source_region_insert_sealed BEFORE INSERT ON source_regions
WHEN (SELECT sealed FROM review_snapshots WHERE id=NEW.snapshot_id)=1
BEGIN SELECT RAISE(ABORT,'cannot add to a sealed snapshot'); END;

CREATE TRIGGER source_region_no_update BEFORE UPDATE ON source_regions
BEGIN SELECT RAISE(ABORT,'source regions are immutable'); END;

CREATE TRIGGER source_region_no_delete BEFORE DELETE ON source_regions
BEGIN SELECT RAISE(ABORT,'source regions are immutable'); END;

CREATE TRIGGER link_insert_sealed BEFORE INSERT ON node_links
WHEN (SELECT sealed FROM review_snapshots WHERE id=NEW.snapshot_id)=1
BEGIN SELECT RAISE(ABORT,'cannot add to a sealed snapshot'); END;

CREATE TRIGGER link_no_update BEFORE UPDATE ON node_links
BEGIN SELECT RAISE(ABORT,'dependency links are immutable'); END;

CREATE TRIGGER link_no_delete BEFORE DELETE ON node_links
BEGIN SELECT RAISE(ABORT,'dependency links are immutable'); END;

CREATE TRIGGER decision_no_update BEFORE UPDATE ON human_decisions
BEGIN SELECT RAISE(ABORT,'human decisions are append only'); END;
CREATE TRIGGER decision_no_delete BEFORE DELETE ON human_decisions
BEGIN SELECT RAISE(ABORT,'human decisions are append only'); END;
CREATE TRIGGER event_no_update BEFORE UPDATE ON run_events
BEGIN SELECT RAISE(ABORT,'events are append only'); END;
CREATE TRIGGER event_no_delete BEFORE DELETE ON run_events
BEGIN SELECT RAISE(ABORT,'events are append only'); END;
