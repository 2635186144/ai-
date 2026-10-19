"""Formal contracts for the planned review pipeline; not application endpoints."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r'^[A-Za-z][A-Za-z0-9:_-]{0,127}$')]
Digest = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Text = Annotated[str, Field(min_length=1, max_length=20000)]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Box = tuple[Nonnegative, Nonnegative, Nonnegative, Nonnegative]
Resolution = Literal['retained', 'withdrawn', 'partly_resolved', 'unresolved']
Assessment = Literal['verified_local_inconsistency', 'supported_concern', 'evidence_limit', 'refuted_allegation', 'not_assessable']
Severity = Literal['local_correction', 'interpretation_limit', 'main_claim_risk', 'none', 'undetermined']
RunState = Literal['queued', 'parsing', 'reading', 'mapping', 'reviewing', 'challenging', 'verifying', 'synthesizing', 'needs_material', 'needs_configuration', 'partial', 'completed', 'failed', 'cancel_requested', 'cancelled']


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, validate_default=True)


class Producer(Contract):
    kind: Literal['assistant_curated', 'model', 'human', 'tool']
    actor_id: Identifier
    context_id: Identifier | None = None
    model_profile_id: Identifier | None = None


class DocumentVersion(Contract):
    id: Identifier
    sha256: Digest
    page_count: int = Field(gt=0)
    source_ref: Text
    material_role: Literal['main', 'supplement']


class SourceRegion(Contract):
    id: Identifier
    document_version_id: Identifier
    pdf_page: int = Field(gt=0)
    page_width: float = Field(gt=0, allow_inf_nan=False)
    page_height: float = Field(gt=0, allow_inf_nan=False)
    bbox: Box
    label: Text
    precision: Literal['page_region', 'text_block', 'table_cell']
    text_sha256: Digest | None = None
    locator_status: Literal['located', 'unverified']
    visual_checked: bool
    producer: Producer

    @model_validator(mode='after')
    def valid_box(self):
        x0, y0, x1, y1 = self.bbox
        if not (x0 < x1 <= self.page_width and y0 < y1 <= self.page_height):
            raise ValueError('bbox must be non-empty and inside its PDF page')
        return self


class Coverage(Contract):
    document_version_id: Identifier
    extracted_pages: list[int]
    reading_note_pages: list[int]
    visually_checked_pages: list[int]
    critical_unreadable_pages: list[int]
    note: Text


class ReadingNote(Contract):
    id: Identifier
    section: Text
    explanation: Text
    evidence_ids: list[Identifier] = Field(min_length=1)
    producer: Producer


class Claim(Contract):
    id: Identifier
    claim_type: Literal['construction', 'conditional_construction', 'empirical_performance', 'mechanistic_interpretation', 'scope']
    paraphrase: Text
    scope: Text
    support_status: Literal['supported_within_scope', 'partial', 'unassessed']
    evidence_ids: list[Identifier]
    producer: Producer

    @model_validator(mode='after')
    def support_needs_evidence(self):
        if self.support_status != 'unassessed' and not self.evidence_ids:
            raise ValueError('assessed claim requires source evidence')
        return self


class Observation(Contract):
    status: Literal['reported', 'not_retrieved', 'not_parsed', 'not_reported_after_review']
    value: str | float | None
    evidence_ids: list[Identifier]
    note: Text

    @model_validator(mode='after')
    def value_state(self):
        if self.status == 'reported' and (self.value is None or not self.evidence_ids):
            raise ValueError('reported observation requires a value and evidence')
        if self.status != 'reported' and self.value is not None:
            raise ValueError('missing observation must not contain an invented value')
        return self


class ExperimentRecord(Contract):
    id: Identifier
    document_version_id: Identifier
    source_row: Text
    model: Observation
    task: Observation
    configuration: Observation
    metric: Observation
    result: Observation
    trainable_parameters: Observation
    aggregation: Observation
    selection_rule: Observation
    evidence_ids: list[Identifier] = Field(min_length=1)
    target_claim_ids: list[Identifier] = Field(min_length=1)
    producer: Producer


class Finding(Contract):
    id: Identifier
    target_claim_ids: list[Identifier] = Field(min_length=1)
    issue_type: Literal['internal_equation_inconsistency', 'interpretation_scope', 'statistical_evidence_limit', 'candidate_reporting_omission', 'candidate_overgeneralization', 'other_concern']
    challenge: Text
    alternative_explanation: Text
    impact_scope: Text
    withdrawal_condition: Text
    evidence_ids: list[Identifier] = Field(min_length=1)
    producer: Producer


class Response(Contract):
    id: Identifier
    finding_id: Identifier
    paper_grounded_answer: Text
    addresses_scope: Text
    acknowledged_gap: Text
    evidence_ids: list[Identifier]
    producer: Producer


class Check(Contract):
    kind: Literal['source_locator', 'visual_check', 'local_calculation', 'semantic_review', 'external_check', 'execution_reproduction']
    status: Literal['performed', 'not_performed', 'failed']
    scope: Text
    artifact_ref: Text | None = None


class Verification(Contract):
    id: Identifier
    finding_id: Identifier
    response_ids: list[Identifier]
    resolution: Resolution
    assessment: Assessment
    severity: Severity
    rationale: Text
    evidence_ids: list[Identifier]
    remaining_unknowns: list[Text]
    checks: list[Check] = Field(min_length=1)
    producer: Producer

    @model_validator(mode='after')
    def bounded_verdict(self):
        if self.resolution == 'withdrawn' and (self.assessment != 'refuted_allegation' or self.severity != 'none' or not self.response_ids):
            raise ValueError('withdrawal requires a response, refuted allegation and no residual severity')
        if self.assessment == 'verified_local_inconsistency':
            kinds = {c.kind for c in self.checks if c.status == 'performed'}
            if self.resolution != 'retained' or not {'source_locator', 'local_calculation'} <= kinds or not self.evidence_ids:
                raise ValueError('local verification requires located evidence and a performed calculation')
        if any(c.kind == 'local_calculation' and c.status == 'performed' and not c.artifact_ref for c in self.checks):
            raise ValueError('performed calculation requires a recorded artifact')
        return self


class HumanAttestation(Contract):
    id: Identifier
    scope: Text
    note: Text
    producer: Producer

    @model_validator(mode='after')
    def real_human_actor(self):
        if self.producer.kind != 'human':
            raise ValueError('human attestation cannot be attributed to a model, tool or assistant')
        return self


class Dependency(Contract):
    source_id: Identifier
    dependent_id: Identifier
    relation: Literal['supports', 'depends_on', 'responds_to', 'verifies']


class ReviewSnapshot(Contract):
    schema_version: Literal['0.2']
    id: Identifier
    origin: Literal['assistant_curated_design_sample', 'pipeline_output']
    report_status: Literal['draft', 'partial', 'ready_for_human_review', 'human_reviewed']
    producer: Producer
    documents: list[DocumentVersion] = Field(min_length=1)
    coverage: list[Coverage]
    source_regions: list[SourceRegion]
    reading_notes: list[ReadingNote]
    claims: list[Claim]
    experiments: list[ExperimentRecord]
    findings: list[Finding]
    responses: list[Response]
    verifications: list[Verification]
    human_attestations: list[HumanAttestation]
    dependencies: list[Dependency]
    limitations: list[Text] = Field(min_length=1)

    @model_validator(mode='after')
    def cross_references(self):
        def unique(items):
            keys = [item.id for item in items]
            if len(keys) != len(set(keys)):
                raise ValueError('duplicate IDs within an entity collection')
            return set(keys)

        docs = {d.id: d for d in self.documents}
        unique(self.documents)
        if sum(d.material_role == 'main' for d in self.documents) != 1:
            raise ValueError('a review snapshot requires exactly one main paper')
        if len({c.document_version_id for c in self.coverage}) != len(self.coverage) or {c.document_version_id for c in self.coverage} != set(docs):
            raise ValueError('coverage must explicitly describe every material version exactly once')
        regions = unique(self.source_regions)
        region_map = {r.id: r for r in self.source_regions}
        claims = unique(self.claims)
        findings = unique(self.findings)
        responses = {r.id: r for r in self.responses}
        groups = [self.source_regions, self.reading_notes, self.claims, self.experiments, self.findings, self.responses, self.verifications, self.human_attestations]
        nodes = []
        for group in groups:
            unique(group)
            nodes.extend(group)
        all_ids = [node.id for node in nodes]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError('node IDs must be unique across all node kinds')
        for source in self.source_regions:
            if source.document_version_id not in docs or source.pdf_page > docs[source.document_version_id].page_count:
                raise ValueError('source region belongs to an absent version or invalid page')
        for cov in self.coverage:
            if cov.document_version_id not in docs:
                raise ValueError('coverage has an absent document version')
            for pages in [cov.extracted_pages, cov.reading_note_pages, cov.visually_checked_pages, cov.critical_unreadable_pages]:
                if len(pages) != len(set(pages)) or any(type(p) is not int or not 1 <= p <= docs[cov.document_version_id].page_count for p in pages):
                    raise ValueError('coverage contains duplicate or invalid page numbers')
        for node in nodes:
            if hasattr(node, 'evidence_ids') and not set(node.evidence_ids) <= regions:
                raise ValueError('unknown source evidence ID')
            if hasattr(node, 'target_claim_ids') and not set(node.target_claim_ids) <= claims:
                raise ValueError('unknown target claim ID')
            if hasattr(node, 'finding_id') and node.finding_id not in findings:
                raise ValueError('unknown finding ID')
        for exp in self.experiments:
            if exp.document_version_id not in docs:
                raise ValueError('experiment has an absent document version')
            for name in ['model', 'task', 'configuration', 'metric', 'result', 'trainable_parameters', 'aggregation', 'selection_rule']:
                if not set(getattr(exp, name).evidence_ids) <= regions:
                    raise ValueError('unknown observation evidence ID')
                if not set(getattr(exp, name).evidence_ids) <= set(exp.evidence_ids):
                    raise ValueError('experiment dependencies must include every observation source')
        verified_findings = set()
        for verification in self.verifications:
            if verification.finding_id in verified_findings:
                raise ValueError('one current verdict per finding per snapshot; older verdicts belong to older snapshots')
            verified_findings.add(verification.finding_id)
            if verification.assessment == 'verified_local_inconsistency' and any(region_map[eid].locator_status != 'located' for eid in verification.evidence_ids):
                raise ValueError('verified local inconsistency cannot rely on unlocated source regions')
            for rid in verification.response_ids:
                if rid not in responses or responses[rid].finding_id != verification.finding_id:
                    raise ValueError('a response cannot resolve another finding')
        if self.report_status in {'ready_for_human_review', 'human_reviewed'} and verified_findings != findings:
            raise ValueError('final review packet requires a disposition for every finding')
        if self.report_status == 'human_reviewed' and not self.human_attestations:
            raise ValueError('human_reviewed status requires an explicit human attestation')
        node_ids = set(all_ids)
        graph = {key: [] for key in node_ids}
        actual_edges = {(e.source_id, e.dependent_id, e.relation) for e in self.dependencies}
        if len(actual_edges) != len(self.dependencies):
            raise ValueError('duplicate dependency edges')
        required_edges = set()
        for node in nodes:
            required_edges.update((eid, node.id, 'supports') for eid in getattr(node, 'evidence_ids', []))
            required_edges.update((cid, node.id, 'depends_on') for cid in getattr(node, 'target_claim_ids', []))
            if isinstance(node, Response):
                required_edges.add((node.finding_id, node.id, 'responds_to'))
            if isinstance(node, Verification):
                required_edges.add((node.finding_id, node.id, 'verifies'))
                required_edges.update((rid, node.id, 'verifies') for rid in node.response_ids)
        if not required_edges <= actual_edges:
            raise ValueError('dependency graph omits an explicit source or review input')
        for edge in self.dependencies:
            if edge.source_id not in node_ids or edge.dependent_id not in node_ids:
                raise ValueError('dependency references an absent node')
            graph[edge.source_id].append(edge.dependent_id)
        done, active = set(), set()

        def visit(key):
            if key in active:
                raise ValueError('invalidation dependencies must form an acyclic graph')
            if key in done:
                return
            active.add(key)
            for child in graph[key]:
                visit(child)
            active.remove(key)
            done.add(key)

        for key in graph:
            visit(key)
        return self


class Budget(Contract):
    max_model_calls: int = Field(ge=0, le=200)
    max_total_tokens: int = Field(ge=0, le=2000000)
    max_adversarial_rounds: int = Field(ge=1, le=2)
    max_concurrent_calls: int = Field(ge=1, le=3)


class CreateReviewRun(Contract):
    document_version_ids: list[Identifier] = Field(min_length=1, max_length=10)
    mode: Literal['local_only', 'external_model']
    model_profile_id: Identifier | None = None
    external_processing_consent: bool
    budget: Budget

    @model_validator(mode='after')
    def explicit_remote_processing(self):
        if len(self.document_version_ids) != len(set(self.document_version_ids)):
            raise ValueError('document versions must not repeat')
        if self.mode == 'external_model' and (not self.external_processing_consent or not self.model_profile_id or self.budget.max_model_calls < 1 or self.budget.max_total_tokens < 1):
            raise ValueError('external processing requires explicit consent, a profile and a positive budget')
        if self.mode == 'local_only' and (self.budget.max_model_calls != 0 or self.budget.max_total_tokens != 0):
            raise ValueError('local-only run reserves no remote model budget')
        return self


class RunView(Contract):
    id: Identifier
    state: RunState
    revision: int = Field(ge=0)
    latest_snapshot_id: Identifier | None
    completed_stages: list[Text]
    pending_requirements: list[Text]
    model_calls_used: int = Field(ge=0)
    tokens_used: int = Field(ge=0)
    budget: Budget


class ProposedCorrection(Contract):
    field: Literal['scope', 'paraphrase', 'challenge', 'metric', 'result', 'aggregation', 'selection_rule']
    text_value: Text | None = None
    observation_value: Observation | None = None

    @model_validator(mode='after')
    def typed_correction(self):
        if self.field in {'scope', 'paraphrase', 'challenge'}:
            if self.text_value is None or self.observation_value is not None:
                raise ValueError('text field requires exactly a text replacement')
        elif self.observation_value is None or self.text_value is not None:
            raise ValueError('experiment field requires exactly an observation replacement')
        return self


class HumanDecisionRequest(Contract):
    base_snapshot_id: Identifier
    node_id: Identifier
    action: Literal['confirm', 'dispute', 'annotate', 'propose_correction']
    rationale: Text
    evidence_ids: list[Identifier]
    proposed_correction: ProposedCorrection | None = None

    @model_validator(mode='after')
    def bounded_correction(self):
        if (self.action == 'propose_correction') != (self.proposed_correction is not None):
            raise ValueError('correction action and typed correction payload must be paired')
        if self.action == 'propose_correction' and not self.evidence_ids:
            raise ValueError('proposed correction requires source evidence')
        return self


class HumanDecisionAccepted(Contract):
    decision_id: Identifier
    run_id: Identifier
    location: Text


class RerunRequest(Contract):
    base_snapshot_id: Identifier
    decision_ids: list[Identifier] = Field(min_length=1)
    reason: Text

    @model_validator(mode='after')
    def distinct_decisions(self):
        if len(self.decision_ids) != len(set(self.decision_ids)):
            raise ValueError('rerun decision IDs must not repeat')
        return self


class Accepted(Contract):
    run_id: Identifier
    state: RunState
    location: Text


class ApiError(Contract):
    code: Identifier
    message: Text
    retryable: bool
    details: list[Text]
