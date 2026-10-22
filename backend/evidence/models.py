from typing import Literal

from pydantic import Field

from design.contracts import Box, Contract, DocumentVersion, SourceRegion


class SourceBlock(Contract):
    id: str
    region: SourceRegion
    text: str
    text_sha256: str
    order_in_page: int
    reading_order_status: Literal['extraction_order_unverified'] = 'extraction_order_unverified'
    section_hint: Literal['main', 'references_candidate', 'appendix_candidate']
    max_font_size: float


class MaterialCandidate(Contract):
    id: str
    kind: Literal['figure_caption', 'table_caption', 'image', 'appendix_heading', 'equation_candidate']
    pdf_page: int
    bbox: Box
    block_id: str | None
    label: str
    status: Literal['candidate_not_semantically_verified'] = 'candidate_not_semantically_verified'
    limitations: list[str]


class PageInfo(Contract):
    pdf_page: int
    pdf_page_label: str | None
    width: float
    height: float
    original_rotation: int
    text_characters: int
    block_count: int
    image_count: int
    text_status: Literal['text_available', 'sparse_text', 'no_text']
    warnings: list[str]


class Extraction(Contract):
    id: str
    document_version_id: str
    parser_version: str
    status: Literal['parsed', 'partial']
    pages: list[PageInfo]
    blocks: list[SourceBlock]
    materials: list[MaterialCandidate]
    warnings: list[str]
    model_calls: Literal[0] = 0
    semantic_reading_performed: Literal[False] = False


class ExtractionSummary(Contract):
    id: str
    parser_version: str
    status: Literal['parsed', 'partial']
    pages: list[PageInfo]
    block_count: int
    material_count: int
    warnings: list[str]
    model_calls: Literal[0] = 0
    semantic_reading_performed: Literal[False] = False


class TitleSource(Contract):
    block_id: str
    pdf_page: int
    start: int
    end: int
    quote: str


class TitleIdentity(Contract):
    text: str
    status: Literal['source_located', 'filename_only', 'unresolved']
    method: Literal['labeled_title', 'front_matter_layout', 'filename']
    sources: list[TitleSource]
    note: str
    version: str


class EvidenceDocument(Contract):
    document: DocumentVersion
    filename: str
    title: str
    extraction: ExtractionSummary | None
    title_identity: TitleIdentity | None = None


class BlockPage(Contract):
    items: list[SourceBlock]
    total: int
    limit: int = Field(gt=0)
    offset: int = Field(ge=0)
