import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from backend.evidence.models import BlockPage, EvidenceDocument, ExtractionSummary, SourceBlock
from backend.evidence.parser import MAX_BYTES, EvidenceError
from backend.evidence.store import PROJECT, EvidenceStore
from backend.evidence.reading import ReadingAction, ReadingPreparation
from backend.evidence.fact_models import FactCommand, GenerateFactCommand
from backend.evidence.facts import FactWorksheet
from backend.evidence.support import SupportCommand
from backend.evidence.workflow import SectionWorkflow, CreateWorkflow, WorkflowControl
from backend.evidence.paper_reports import PaperReports, ReportRequest, ReportControl
from design.contracts import ApiError, DocumentVersion, SourceRegion


class EvidenceRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except EvidenceError as exc:
                return JSONResponse(status_code=exc.status, content={'code': exc.code, 'message': exc.message, 'retryable': False, 'details': []})
            except RequestValidationError as exc:
                details = [f"{'/'.join(str(v) for v in e['loc'])}: {e['msg']}" for e in exc.errors()]
                return JSONResponse(status_code=422, content={'code': 'invalid_request', 'message': '请求格式不正确，请检查文件、参数或请求标识', 'retryable': False, 'details': details})

        return handler


router = APIRouter(prefix='/api/v2/documents', tags=['Evidence foundation'], route_class=EvidenceRoute,
    responses={code: {'model': ApiError} for code in [404, 409, 413, 422]})


@lru_cache(maxsize=4)
def cached_store(root):
    return EvidenceStore(root)


def get_store():
    return cached_store(str(Path(os.getenv('EVIDENCEMAP_EVIDENCE_DIR', str(PROJECT / 'dev_data' / 'evidence'))).resolve()))


Store = Annotated[EvidenceStore, Depends(get_store)]
RequestKey = Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=160)]


@router.post('', response_model=DocumentVersion, status_code=201)
async def upload_document(store: Store, request_key: RequestKey, file: UploadFile = File(...), material_role: Literal['main', 'supplement'] = Form('main')):
    try:
        content = await file.read(MAX_BYTES + 1)
        return await run_in_threadpool(store.register, content, file.filename, material_role, request_key)
    finally:
        await file.close()


@router.get('')
def list_documents(store: Store, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    return store.list_documents(limit, offset)


@router.get('/{document_id}', response_model=EvidenceDocument)
def document_detail(document_id: str, store: Store):
    return store.detail(document_id)


@router.get('/{document_id}/file')
def source_file(document_id: str, store: Store):
    return FileResponse(store.file_path(document_id), media_type='application/pdf', headers={'Cache-Control': 'no-store'})


@router.post('/{document_id}/parse', response_model=ExtractionSummary)
def parse(document_id: str, store: Store, request_key: RequestKey):
    return store.parse(document_id, request_key)


@router.get('/{document_id}/blocks', response_model=BlockPage)
def source_blocks(document_id: str, store: Store, page: int | None = Query(None, ge=1), q: str = Query('', max_length=500), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return store.blocks(document_id, page, q, limit, offset)


@router.get('/{document_id}/blocks/{block_id}', response_model=SourceBlock)
def source_block(document_id: str, block_id: str, store: Store):
    return store.block(document_id, block_id)


@router.get('/{document_id}/blocks/{block_id}/region', response_model=SourceRegion)
def source_region(document_id: str, block_id: str, store: Store):
    return store.block(document_id, block_id).region


@router.get('/{document_id}/materials')
def materials(document_id: str, store: Store):
    return {'items': [m.model_dump(mode='json') for m in store.materials(document_id)],
            'status': 'candidates_only', 'table_cells_extracted': False, 'semantic_review_performed': False}


@router.get('/{document_id}/pages/{page}/image')
def page_image(document_id: str, page: int, store: Store, scale: float = Query(1.25, ge=.5, le=2)):
    return Response(store.render(document_id, page, scale), media_type='image/png', headers={'Cache-Control': 'no-store'})


@router.get('/{document_id}/visuals')
def source_visual_index(document_id: str, store: Store):
    from backend.evidence.visuals import source_visuals
    return source_visuals(store,document_id)


@router.get('/{document_id}/visuals/{visual_id}/image')
def source_visual_image(document_id: str, visual_id: str, store: Store, scale: float = Query(1.5, ge=.5, le=2)):
    from backend.evidence.visuals import render_visual
    return Response(render_visual(store,document_id,visual_id,scale),media_type='image/png',headers={'Cache-Control':'no-store'})


@router.get('/{document_id}/blocks/{block_id}/image')
def block_image(document_id: str, block_id: str, store: Store, scale: float = Query(1.5, ge=.5, le=2)):
    return Response(store.render(document_id, 1, scale, block_id), media_type='image/png', headers={'Cache-Control': 'no-store'})


@router.get('/{document_id}/reading')
def reading_preparation(document_id: str, store: Store):
    return ReadingPreparation(store).get(document_id)


@router.get('/{document_id}/report-plan')
def paper_report_plan(document_id: str, store: Store):
    return PaperReports(store).plan(document_id)


@router.get('/{document_id}/reports')
def paper_report_list(document_id: str, store: Store):
    return PaperReports(store).list(document_id)


@router.post('/{document_id}/reports', status_code=201)
async def create_paper_report(document_id: str, command: ReportRequest, store: Store, request_key: RequestKey):
    reports = PaperReports(store)
    # Preparation is local only. Starting is a separate explicit action so retries
    # of this creation request can never silently restart a paid request.
    return await run_in_threadpool(reports.create, document_id, command, request_key)


@router.get('/{document_id}/reports/{report_id}')
def paper_report_detail(document_id: str, report_id: str, store: Store):
    return PaperReports(store).get(document_id, report_id)


@router.post('/{document_id}/reports/{report_id}/control')
async def control_paper_report(document_id: str, report_id: str, command: ReportControl, store: Store):
    reports = PaperReports(store)
    result = await run_in_threadpool(reports.control, document_id, report_id, command)
    if result['state'] in ('running','pausing','cancelling'):
        reports.launch(document_id, report_id)
    return result


@router.post('/{document_id}/reading')
def update_reading(document_id: str, command: ReadingAction, store: Store, request_key: RequestKey):
    return ReadingPreparation(store).update(document_id, command, request_key)


@router.get('/{document_id}/reading/sections/{section_id}/packet')
def section_packet(document_id: str, section_id: str, store: Store, revision: int = Query(..., ge=1), preview: bool = False):
    return ReadingPreparation(store).packet(document_id, section_id, revision, preview=preview)


@router.get('/{document_id}/reading/sections/{section_id}/facts')
def fact_worksheet(document_id: str, section_id: str, store: Store, reading_revision: int = Query(..., ge=1)):
    return FactWorksheet(store).get(document_id, section_id, reading_revision)


@router.post('/{document_id}/reading/sections/{section_id}/facts')
def save_paragraph_fact(document_id: str, section_id: str, command: FactCommand, store: Store, request_key: RequestKey):
    return FactWorksheet(store).update(document_id, section_id, command, request_key)


@router.post('/{document_id}/reading/sections/{section_id}/facts/generate')
async def generate_paragraph_fact(document_id: str, section_id: str, command: GenerateFactCommand, store: Store, request_key: RequestKey):
    return await FactWorksheet(store).generate(document_id, section_id, command, request_key)


@router.post('/{document_id}/reading/sections/{section_id}/facts/support-review')
def review_fact_support(document_id: str, section_id: str, command: SupportCommand, store: Store, request_key: RequestKey):
    return FactWorksheet(store).review_support(document_id, section_id, command, request_key)


@router.get('/{document_id}/reading/sections/{section_id}/runs')
def section_runs(document_id: str, section_id: str, store: Store, reading_revision: int = Query(..., ge=1)):
    return SectionWorkflow(store).list(document_id, section_id, reading_revision)


@router.post('/{document_id}/reading/sections/{section_id}/runs', status_code=201)
def create_section_run(document_id: str, section_id: str, command: CreateWorkflow, store: Store, request_key: RequestKey):
    return SectionWorkflow(store).create(document_id, section_id, command, request_key)


@router.get('/{document_id}/runs/{run_id}')
def section_run(document_id: str, run_id: str, store: Store):
    return SectionWorkflow(store).get(document_id, run_id)


@router.post('/{document_id}/runs/{run_id}/control')
async def control_section_run(document_id: str, run_id: str, command: WorkflowControl, store: Store):
    workflow = SectionWorkflow(store)
    result = await run_in_threadpool(workflow.control, document_id, run_id, command)
    # Only an explicit start/resume/retry can dispatch; polling and server startup never do.
    if command.action in ('start', 'resume', 'retry') and result['state'] == 'running':
        workflow.launch(document_id, run_id)
    return result
