"""Whole-paper reading outputs, distinct from review opinions."""
from typing import Literal
from pydantic import Field, model_validator
from design.contracts import Contract


class NoteCitation(Contract):
    span_id: str
    quote: str | None = Field(default=None, min_length=1, max_length=5000)
    quote_id: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode='after')
    def one_locator(self):
        if (self.quote is None) == (self.quote_id is None):
            raise ValueError('Provide exactly one original quote or supplied quote_id')
        return self


class Coverage(Contract):
    span_id: str
    disposition: Literal['read', 'non_substantive', 'unreadable']
    reason: str = Field(min_length=1, max_length=1000)


class Topic(Contract):
    heading: str = Field(min_length=1, max_length=250)
    role: str = Field(min_length=1, max_length=1500)
    explanation: str = Field(min_length=1, max_length=5000)
    method_details: list[str] = Field(max_length=12)
    findings: list[str] = Field(max_length=12)
    author_contributions: list[str] = Field(max_length=10)
    limitations: list[str] = Field(max_length=10)
    reading_focus: list[str] = Field(max_length=10)
    citations: list[NoteCitation] = Field(min_length=1, max_length=80)


class ChunkReading(Contract):
    overview: str = Field(min_length=1, max_length=2500)
    coverage: list[Coverage] = Field(min_length=1, max_length=80)
    topics: list[Topic] = Field(max_length=12)
    unknowns: list[str] = Field(max_length=20)


class SelectedCitation(Contract):
    span_id: str
    quote_id: str


class SelectedTopic(Topic):
    citations: list[SelectedCitation] = Field(min_length=1, max_length=80)


class SelectedChunkReading(ChunkReading):
    topics: list[SelectedTopic] = Field(max_length=12)


class Insight(Contract):
    text: str = Field(min_length=1, max_length=9000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class Chapter(Contract):
    heading: str = Field(min_length=1, max_length=250)
    role: str = Field(min_length=1, max_length=1500)
    content: Insight
    connects_to: str = Field(min_length=1, max_length=1500)
    source_section_ids: list[str] = Field(default_factory=list, max_length=20)


class CoreContent(Contract):
    title: str = Field(min_length=1, max_length=250)
    explanation: Insight


class Contribution(Contract):
    title: str = Field(min_length=1, max_length=250)
    author_claim: Insight
    explanation: Insight
    boundary: Insight
    baseline: Insight | None = None
    change: Insight | None = None
    rationale: Insight | None = None
    support: Insight | None = None


class ReadingStop(Contract):
    priority: Literal['first', 'next', 'optional']
    section: str = Field(min_length=1, max_length=250)
    why: str = Field(min_length=1, max_length=2000)
    questions: list[str] = Field(min_length=1, max_length=8)
    prerequisites: list[str] = Field(max_length=8)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)


class GuidanceCheck(Contract):
    target: str = Field(pattern=r'^(conclusion|limitations|open_questions\.[0-9]+)$')
    kind: Literal['explanation', 'reading_question', 'criticism', 'extraction_gap']
    judgment: Literal['supported', 'partial', 'unresolved', 'withdrawn']
    reason: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=6)
    source_ids: list[str] = Field(default_factory=list, max_length=6)


class PaperReport(Contract):
    title: Insight
    research_direction: Insight
    one_sentence: Insight
    abstract_explanation: Insight
    abstract_evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    argument_map: list[Chapter] = Field(min_length=1, max_length=30)
    core_content: list[CoreContent] = Field(min_length=1, max_length=12)
    innovations: list[Contribution] = Field(max_length=10)
    reading_route: list[ReadingStop] = Field(min_length=1, max_length=12)
    conclusion: Insight
    limitations: Insight
    open_questions: list[str] = Field(max_length=20)
    guidance_checks: list[GuidanceCheck] = Field(default_factory=list, max_length=22)


class ReportOverview(Contract):
    title: Insight
    research_direction: Insight
    one_sentence: Insight
    abstract_explanation: Insight
    abstract_evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    argument_map: list[Chapter] = Field(min_length=1, max_length=30)


class ReportCore(Contract):
    core_content: list[CoreContent] = Field(min_length=1, max_length=12)
    innovations: list[Contribution] = Field(max_length=10)


class ReportGuidance(Contract):
    reading_route: list[ReadingStop] = Field(min_length=1, max_length=12)
    conclusion: Insight
    limitations: Insight
    open_questions: list[str] = Field(max_length=20)
    guidance_checks: list[GuidanceCheck] = Field(default_factory=list, max_length=22)


REPORT_PART_MODELS = {'overview': ReportOverview, 'core': ReportCore, 'guidance': ReportGuidance}


class ConsolidatedTopic(Contract):
    heading: str = Field(min_length=1,max_length=250)
    role: str = Field(min_length=1,max_length=500)
    explanation: str = Field(min_length=1,max_length=3000)
    method_details: list[str] = Field(max_length=8)
    findings: list[str] = Field(max_length=8)
    author_contributions: list[str] = Field(max_length=8)
    limitations: list[str] = Field(max_length=12)
    reading_focus: list[str] = Field(max_length=8)
    evidence_ids: list[str] = Field(min_length=1,max_length=40)


class ConsolidatedReading(Contract):
    overview: str = Field(min_length=1,max_length=1500)
    topics: list[ConsolidatedTopic] = Field(min_length=1,max_length=8)
    unknowns: list[str] = Field(max_length=40)
