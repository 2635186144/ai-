from typing import Literal

from pydantic import Field, model_validator

from design.contracts import Contract


class FactCitation(Contract):
    block_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=12000)


class ParagraphFact(Contract):
    category: Literal['problem', 'concept', 'method', 'derivation', 'experiment', 'result', 'limitation']
    basis: Literal['author_statement', 'reader_inference']
    statement: str = Field(min_length=1, max_length=3000)
    reasoning: str = Field(default='', max_length=4000)
    conditions: list[str] = Field(default_factory=list, max_length=20)
    citations: list[FactCitation] = Field(min_length=1, max_length=12)

    @model_validator(mode='after')
    def inference_requires_reason(self):
        if not self.statement.strip() or (self.basis == 'reader_inference' and not self.reasoning.strip()):
            raise ValueError('A statement is required; reader inference also needs explicit reasoning')
        return self


class ReadingUnknown(Contract):
    question: str = Field(min_length=1, max_length=2000)
    why_unresolved: str = Field(min_length=1, max_length=2000)
    needed_evidence: str = Field(min_length=1, max_length=2000)


class ParagraphDraft(Contract):
    disposition: Literal['analysed', 'non_substantive', 'blocked']
    explanation: str = Field(min_length=1, max_length=6000)
    facts: list[ParagraphFact] = Field(default_factory=list, max_length=30)
    unknowns: list[ReadingUnknown] = Field(default_factory=list, max_length=20)
    unresolved_dependencies: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def meaningful_record(self):
        if not self.explanation.strip():
            raise ValueError('Explanation or disposition reason is required')
        if self.disposition == 'analysed' and not self.facts:
            raise ValueError('Analysed units require at least one grounded fact')
        if self.disposition == 'non_substantive' and self.facts:
            raise ValueError('Non-substantive units cannot silently contain unaccounted facts')
        if self.disposition == 'blocked' and not self.unknowns:
            raise ValueError('Blocked units must identify missing evidence')
        return self


class FactCommand(Contract):
    reading_revision: int = Field(ge=1)
    revision: int = Field(ge=1)
    unit_id: str
    draft: ParagraphDraft


class GenerateFactCommand(Contract):
    reading_revision: int = Field(ge=1)
    revision: int = Field(ge=1)
    unit_id: str


class ModelCitation(Contract):
    block_id: str
    quote: str = Field(min_length=1, max_length=12000)
    # Accepted for backward compatibility, but no longer requested from the model.
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, gt=0)


class ModelFact(ParagraphFact):
    citations: list[ModelCitation] = Field(min_length=1, max_length=12)


class ModelParagraphDraft(ParagraphDraft):
    facts: list[ModelFact] = Field(default_factory=list, max_length=30)
