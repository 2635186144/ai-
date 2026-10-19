"""Automatic whole-text reading, checkpointed notes and evidence-linked synthesis."""
import asyncio
import copy
import json
import time
import uuid
import os
from contextlib import contextmanager
from typing import Literal

from pydantic import Field, ValidationError
from starlette.concurrency import run_in_threadpool

from design.contracts import Contract
from backend.evidence import fact_model, report_model
from backend.evidence.parser import EvidenceError, digest
from backend.evidence.reading import ReadingPreparation
from backend.evidence.report_models import ChunkReading, PaperReport, REPORT_PART_MODELS,ConsolidatedReading
from backend.evidence.store import now
from backend.evidence.workflow import configuration_signature
from backend.evidence.report_quotes import CONTRACT, quote_candidates, locate, QuoteValidationError
from backend.evidence.report_structure import build_outline, containing_section, section_ids_for_evidence

WORKERS = {}
POLICY_VERSION = 'report-batches-v3-7000-64'
COMPATIBLE_POLICIES = {POLICY_VERSION, 'report-batches-v2-7000-16'}
SPAN_CHARACTERS = 3500
BATCH_CHARACTERS = 7000
BATCH_SPANS = 64
MAX_REPORT_CALLS = 500


def recommended_budget(calls_needed):
    return min(MAX_REPORT_CALLS,max(24,calls_needed*2+6+((max(0,calls_needed-3)+2)//3)*2))


def synthesis_tasks():
    return [{'id':'synthesis_'+p,'kind':'synthesis','part':p,'state':'pending','attempts':0} for p in REPORT_PART_MODELS]


class ReportRequest(Contract):
    max_calls: int = Field(ge=1, le=MAX_REPORT_CALLS)
    consent_to_send: bool
    automatic_recovery: bool = False


class ReportControl(Contract):
    revision: int = Field(ge=1)
    action: Literal['start', 'pause', 'resume', 'retry', 'cancel', 'revalidate', 'split_synthesis', 'extend_budget', 'revise_part']
    accept_new_request: bool = False
    max_calls: int | None = Field(default=None, ge=1, le=MAX_REPORT_CALLS)
    automatic_recovery: bool | None = None
    repair_source_mapping: bool = False
    part: Literal['overview','core','guidance'] | None = None
    feedback: str | None = Field(default=None, min_length=1, max_length=6000)
    feedback_evidence_ids: list[str] = Field(default_factory=list, max_length=20)


def split_source(blocks):
    """Exact partition, independent of unreliable chapter proposals; no silent truncation."""
    chunks, current, size = [], [], 0
    for b in blocks:
        for start in range(0, len(b['text']), SPAN_CHARACTERS):
            text = b['text'][start:start+SPAN_CHARACTERS]
            if current and (size+len(text) > BATCH_CHARACTERS or len(current) >= BATCH_SPANS):
                chunks.append(current);current=[];size=0
            current.append({'span_id': f'span_{len(chunks)}_{len(current)}', 'block_id': b['id'],
                            'pdf_page': b['region']['pdf_page'], 'start': start, 'end': start+len(text), 'text': text})
            size += len(text)
    if current:
        chunks.append(current)
    return chunks


def synthesis_notes(notes):
    """Keep all prose/quotes, omit source bookkeeping already retained locally."""
    return [{k:note[k] for k in ('overview','topics','unknowns')} |
            {'evidence':[{k:e[k] for k in ('id','pdf_page','quote')} for e in note['evidence']]} for note in notes]


def compact_section_context(payload):
    """Losslessly group identical chapter mappings; keep every note and quote."""
    if 'evidence_sections' not in payload:return payload
    groups={}
    for eid,sid in payload['evidence_sections'].items():
        ancestors=payload['evidence_section_ancestors'].get(eid,[])
        key=(sid,tuple(ancestors))
        group=groups.setdefault(key,{'section_id':sid,'ancestor_ids':ancestors,'evidence_ids':[]})
        group['evidence_ids'].append(eid)
    compact={k:v for k,v in payload.items() if k not in ('evidence_sections','evidence_section_ancestors')}
    compact['section_evidence_groups']=list(groups.values())
    return compact


def validate_consolidation(raw,notes):
    result=ConsolidatedReading.model_validate(raw).model_dump(mode='json')
    evidence={e['id']:e for note in notes for e in note['evidence']};selected={}
    for topic in result['topics']:
        ids=topic['evidence_ids']
        if len(ids)!=len(set(ids)) or any(e not in evidence for e in ids):
            raise EvidenceError(422,'report_evidence_invalid','整合笔记引用无效，原始笔记仍完整保留')
        for eid in ids:selected[eid]=evidence[eid]
    result.update(evidence=list(selected.values()),semantic_verified=False)
    if len(json.dumps(result,ensure_ascii=False))>50000:
        raise EvidenceError(422,'report_notes_too_large','整合笔记过长，未截断内容')
    return result


def validate_notes(raw, spans, index):
    result = ChunkReading.model_validate(raw).model_dump(mode='json', exclude_none=True)
    by_id = {s['span_id']: s for s in spans}
    coverage = [c['span_id'] for c in result['coverage']]
    if len(coverage) != len(set(coverage)) or set(coverage) != set(by_id):
        raise EvidenceError(422, 'report_reading_coverage_invalid', '本批阅读遗漏或重复了原文范围，未记为已完成')
    read_ids = {c['span_id'] for c in result['coverage'] if c['disposition'] == 'read'}
    cited = set();evidence=[]
    for topic_index,topic in enumerate(result['topics']):
        ids=[]
        for citation_index,c in enumerate(topic.pop('citations')):
            s = by_id.get(c['span_id'])
            offset,end,method=locate(c,s,topic_index,citation_index)
            eid = f'evidence_{index}_{len(evidence)}'
            evidence.append({'id': eid, 'block_id': s['block_id'], 'pdf_page': s['pdf_page'],
                             'start': s['start']+offset, 'end': s['start']+end, 'quote': s['text'][offset:end],
                             'locator_method':method, **({'quote_id':c['quote_id']} if c.get('quote_id') else {})})
            ids.append(eid);cited.add(s['span_id'])
        topic['evidence_ids'] = ids
    if not read_ids <= cited:
        raise EvidenceError(422, 'report_reading_evidence_missing', '标记已读的实质原文缺少笔记引文，未保存')
    result.update(evidence=evidence, semantic_verified=False)
    if len(json.dumps(result, ensure_ascii=False)) > 50000:
        raise EvidenceError(422, 'report_notes_too_large', '本批笔记过长，未静默截断，请调整阅读分批策略')
    return result


def validate_report(raw, notes, part=None, outline=None):
    schema=REPORT_PART_MODELS[part] if part else PaperReport
    result = schema.model_validate(raw).model_dump(mode='json')
    evidence = {e['id']: e for note in notes for e in note['evidence']}
    def check(value):
        if isinstance(value, dict):
            for field in ('evidence_ids','abstract_evidence_ids'):
                if field not in value:continue
                ids = value[field]
                if len(ids) != len(set(ids)) or any(i not in evidence for i in ids):
                    raise EvidenceError(422, 'report_evidence_invalid', '综合报告引用了不存在或重复的原文证据，未发布')
            for item in value.values():check(item)
        elif isinstance(value, list):
            for item in value:check(item)
    check(result)
    section_ids={s['id'] for s in (outline or {}).get('sections',[])}
    for chapter in result.get('argument_map',[]):
        ids=chapter['source_section_ids']
        locations={sid for eid in chapter['content']['evidence_ids'] for sid in section_ids_for_evidence(outline,evidence[eid])}
        if len(ids)!=len(set(ids)) or any(sid not in section_ids or sid not in locations for sid in ids):
            raise EvidenceError(422,'report_section_mapping_invalid','论证节点的章节对应缺少有效来源位置，未发布；位置对应不代表语义已核验')
    if part:return result
    result.update(evidence=list(evidence.values()), semantic_verified=False,
                  reading_unknowns=list(dict.fromkeys(q for note in notes for q in note['unknowns'])),
                  abstract_label='系统白话解读，非作者摘要原文', novelty_scope='仅据论文自身表述，未做外部新颖性检索')
    return result


def repair_source_mapping(raw,notes,part,outline):
    """Explicit local projection of chapter links only; no prose or citation edits.

    Unknown/duplicate IDs and every other validation error still fail closed.
    The caller must retain the original candidate and expose this audit record.
    """
    result=REPORT_PART_MODELS[part].model_validate(raw).model_dump(mode='json')
    result=copy.deepcopy(result)
    sections={s['id']:s for s in (outline or {}).get('sections',[])}
    evidence={e['id']:e for note in notes for e in note['evidence']}
    changes=[]
    for index,chapter in enumerate(result.get('argument_map',[])):
        ids=chapter['source_section_ids']
        if len(ids)!=len(set(ids)) or any(s not in sections for s in ids):
            raise EvidenceError(422,'report_section_mapping_invalid','未知或重复章节编号不能由本地关联修复放行')
        if any(e not in evidence for e in chapter['content']['evidence_ids']):
            raise EvidenceError(422,'report_evidence_invalid','引文编号无效，不能修复章节关联')
        locations={s for e in chapter['content']['evidence_ids'] for s in section_ids_for_evidence(outline,evidence[e])}
        removed=[s for s in ids if s not in locations]
        if removed:
            chapter['source_section_ids']=[s for s in ids if s in locations]
            changes.append({'node_index':index,'heading':chapter['heading'],'removed_section_ids':removed,
                            'removed_section_titles':[sections[s]['title'] for s in removed],
                            'retained_section_ids':chapter['source_section_ids'],
                            'reason':'no_citation_location_in_selected_section','semantic_verified':False})
    return validate_report(result,notes,part,outline),changes


class PaperReports:
    def __init__(self, store):self.store=store

    def candidate_path(self,rid,task):
        return self.store.root/'report_candidates'/(digest(f'{rid}:{task["id"]}:{task["attempts"]}'.encode())+'.json')

    def save_candidate(self,did,rid,task,raw):
        # Private local recovery data: never credentials, headers or reasoning_content.
        path=self.candidate_path(rid,task);path.parent.mkdir(exist_ok=True)
        data={'document_version_id':did,'run_id':rid,'task_id':task['id'],'attempt':task['attempts'],
              'input_sha256':task['input_sha256'],'prompt_version':task['prompt_version'],
              'response':raw,'usage':getattr(raw,'usage',{}),'validation_state':'unvalidated_not_published','created_at':now()}
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');os.replace(temp,path)

    def revalidate(self,did,rid,command):
        # Pure local validation. It never invokes a model or reserves a request.
        with self.store.connection() as db:run=self.load(db,did,rid)
        if run['revision']!=command.revision or run['state']!='failed':
            raise EvidenceError(409,'report_action_invalid','仅能对当前失败检查点重新校验，请刷新进度')
        task=next((t for t in run['tasks'] if t['state']=='failed'),None)
        if task is None or not self.candidate_path(rid,task).exists():
            raise EvidenceError(409,'report_candidate_unavailable','旧程序未保留本次待核验回复，无法本地恢复；后续请求已改为保存候选回复')
        try:data=json.loads(self.candidate_path(rid,task).read_text(encoding='utf-8'))
        except (OSError,ValueError):raise EvidenceError(409,'report_candidate_invalid','待核验回复文件不可读，未发布结果')
        if (data.get('document_version_id')!=did or data.get('run_id')!=rid or data.get('task_id')!=task['id']
                or data.get('attempt')!=task['attempts'] or data.get('input_sha256')!=task['input_sha256']):
            raise EvidenceError(409,'report_candidate_invalid','待核验回复不属于当前来源和请求，未发布结果')
        eid,blocks,*_=self.source(did)
        if eid!=run['extraction_id']:raise EvidenceError(409,'report_source_changed','原文解析已变化，未发布历史候选')
        repairs=[]
        if command.repair_source_mapping and (task.get('part')!='overview' or (run.get('error') or {}).get('code')!='report_section_mapping_invalid'):
            raise EvidenceError(409,'report_action_invalid','仅对章节位置关联失败的概览候选执行局部修复')
        if task['kind']=='reading':
            by_id={b['id']:b for b in blocks}
            spans=[{**s,'text':by_id[s['block_id']]['text'][s['start']:s['end']]} for s in task['source_spans']]
            output=validate_notes(data['response'],spans,task['index'])
        elif task['kind']=='consolidation':
            lookup={t['id']:t for t in run['tasks']}
            output=validate_consolidation(data['response'],[lookup[i]['output'] for i in task['input_task_ids']])
        elif command.repair_source_mapping:
            output,repairs=repair_source_mapping(data['response'],[t['output'] for t in run['tasks'] if t['kind']=='reading'],task['part'],run.get('source_outline'))
        else:output=validate_report(data['response'],[t['output'] for t in run['tasks'] if t['kind']=='reading'],task.get('part'),run.get('source_outline'))
        with self.transaction(did,rid) as (db,current):
            if current['revision']!=command.revision or current['state']!='failed':raise EvidenceError(409,'stale_report_revision','检查点发生变化，未覆盖已有结果')
            target=next(t for t in current['tasks'] if t['id']==task['id'])
            target.update(state='succeeded',output=output,usage=data.get('usage',{}),finished_at=now(),revalidated_locally=True)
            if repairs:
                target['source_mapping_repairs']=repairs
                current.setdefault('local_recovery_events',[]).append({'task_id':task['id'],'attempt':task['attempts'],
                    'at':now(),'kind':'explicit_source_mapping_projection','changes':repairs,
                    'original_candidate_preserved':True,'model_calls_added':0,'semantic_verified':False})
            target.pop('error',None);target.pop('validation_failure',None)
            current.update(state='paused',error=None,lease_token=None,lease_until=0)
            if target['kind']=='synthesis':self.complete_synthesis(current,target,output)
        return self.get(did,rid)

    def source(self, did, parse=False):
        detail=self.store.detail(did)
        if not detail.extraction:
            if not parse:return None
            self.store.parse(did, 'whole-paper-local-parse')
        eid,blocks,summary,materials=ReadingPreparation(self.store).source(did)
        return eid,blocks,summary,materials

    def plan(self, did):
        source=self.source(did)
        profile=fact_model.status()
        if source is None:return {'parsed':False,'model':profile,'calls_needed':None,'chunks':None}
        eid,blocks,summary,materials=source
        chunks=split_source(blocks)
        return {'parsed':True,'extraction_id':eid,'source_outline':build_outline(eid,blocks),'model':profile,'chunks':len(chunks),'synthesis_calls':3,'calls_needed':len(chunks)+3,'policy_version':POLICY_VERSION,
                'recommended_max_calls':recommended_budget(len(chunks)+3),'automatic_recovery_retry_limit':1,'max_report_calls':MAX_REPORT_CALLS,
                'reader_prompt_version':report_model.PROMPT_VERSION,'synthesis_prompt_version':report_model.SYNTHESIS_VERSION,
                'text_pages':sorted({b['region']['pdf_page'] for b in blocks}), 'total_pages':len(summary.pages),
                'text_characters':sum(len(b['text']) for b in blocks),
                'page_gaps':[p.model_dump(mode='json') for p in summary.pages if p.text_status!='text_available'],
                'material_count':len(materials),'visual_understanding_performed':False,
                'send_notice':'按批发送全文提取文字、页码、图表位置及阅读笔记；不发送PDF文件或图片。每次最多6000输出token，请求次数上限不是费用上限。'}

    def load(self, db, did, rid):
        row=db.execute('SELECT payload_json FROM paper_report_runs WHERE document_id=? AND id=?',(did,rid)).fetchone()
        if not row:raise EvidenceError(404,'paper_report_not_found','全文解读任务不存在')
        return json.loads(row[0])

    @contextmanager
    def transaction(self,did,rid):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');run=self.load(db,did,rid)
            yield db,run
            run['revision']+=1;run['updated_at']=now()
            db.execute('UPDATE paper_report_runs SET payload_json=? WHERE id=?',(json.dumps(run,ensure_ascii=False),rid));db.commit()

    @staticmethod
    def present(run):
        result={k:v for k,v in run.items() if k not in ('configuration_signature','lease_token')}
        completed=sum(t['state']=='succeeded' for t in run['tasks'])
        result['progress']={'completed':completed,'total':len(run['tasks'])}
        result['interrupted']=run['state'] in ('running','pausing','cancelling') and run['lease_until']<time.time()
        result['semantic_verified']=False
        result['automatic_retries']=sum(e['kind']=='bounded_retry' for e in run.get('recovery_events',[]))
        result['recommended_completion_limit']=min(MAX_REPORT_CALLS,max(run['max_calls'],run['calls_reserved']+recommended_budget(sum(t['state'] in ('pending','failed') for t in run['tasks']))))
        reads=[t for t in run['tasks'] if t['kind']=='reading']
        legacy=[t for t in run['tasks'] if t['kind']=='synthesis' and not t.get('part') and t['state'] in ('failed','pending')]
        result['split_synthesis_available']=bool(reads and all(t['state']=='succeeded' for t in reads) and legacy and run['state'] in ('failed','paused'))
        if result['split_synthesis_available']:
            result['synthesis_recovery']={'additional_calls':3,'old_limit':run['max_calls'],'minimum_new_limit':max(run['max_calls'],run['calls_reserved']+3)}
        remaining=[t for t in run['tasks'] if t['state'] in ('pending','failed')]
        result['budget_extension_available']=bool(run['state']=='failed' and reads and remaining
            and all(t['kind'] in ('reading','consolidation') or t.get('part') for t in remaining)
            and run.get('policy_version') in COMPATIBLE_POLICIES and run['max_calls']-run['calls_reserved']<len(remaining))
        if result['budget_extension_available']:
            result['report_continuation']={'additional_calls':len(remaining),'new_limit':run['calls_reserved']+len(remaining),
                                           **({'remaining_reading':sum(t['kind']=='reading' for t in remaining)} if any(t['kind']=='reading' for t in remaining) else {})}
        result['requires_replan']=run.get('policy_version') not in COMPATIBLE_POLICIES and run['state'] not in ('completed','completed_with_gaps','cancelled')
        return result

    def get(self,did,rid):
        with self.store.connection() as db:run=self.load(db,did,rid)
        result=self.present(run)
        failed=next((t for t in run['tasks'] if t['state']=='failed'),None)
        result['candidate_available']=bool(failed and self.candidate_path(rid,failed).exists())
        if run['state']=='failed' and failed:
            path=self.store.root/'diagnostics'/(digest(f'{rid}:{failed["id"]}:{failed["attempts"]}'.encode())+'.json')
            if path.exists():
                try:
                    raw=json.loads(path.read_text(encoding='utf-8'))
                    # Only typed counters and known categories, never arbitrary diagnostic text.
                    safe={k:raw[k] for k in ('http_status','content_characters','max_output_tokens','reasoning_content_characters') if type(raw.get(k)) is int}
                    safe.update({k:raw[k] for k in ('finish_reason','stage','thinking_mode') if raw.get(k) in ('stop','length','other','empty_content','output_truncated','reasoning_only','disabled','provider_default')})
                    safe['usage']={k:raw['usage'][k] for k in ('prompt_tokens','completion_tokens','total_tokens','reasoning_tokens') if type((raw.get('usage') or {}).get(k)) is int}
                    result['last_failure']=safe
                    if safe.get('http_status')==429:
                        result['error']={'code':'report_rate_limited','message':'模型服务限流（HTTP 429），已停止立即自动重试。请稍后继续；若持续发生，请检查服务商额度或模型可用性。增加本地请求预算不能解除限流，已保存内容保留。'}
                    if safe.get('finish_reason')=='length':
                        message=(('全文研读笔记已保存，本段报告综合输出被截断。当前已采用分段综合，需缩短失败段的论证节点与重复引文后再继续；无需重新阅读全文，不应原样重试。'
                                  if failed.get('part') else '全文研读笔记已保存，最后的报告综合输出被截断。请仅将综合改为分段生成，无需重新阅读全文。') if failed['kind']=='synthesis'
                                 else '上次请求已用满输出token上限，回复被截断；失败记录已保留，请按较小批次重新规划，不要原样重复重试。')
                        result['error']={'code':'report_output_truncated','message':message}
                except (ValueError,TypeError,KeyError,OSError):pass
        return result

    def list(self,did):
        self.store.detail(did)
        with self.store.connection() as db:
            rows=db.execute('SELECT payload_json FROM paper_report_runs WHERE document_id=? ORDER BY rowid DESC LIMIT 20',(did,)).fetchall()
        return {'items':[self.get(did,json.loads(r[0])['id']) for r in rows]}

    def create(self,did,command,key):
        fingerprint=digest(command.model_dump_json().encode())
        with self.store.connection() as db:
            row=db.execute('SELECT fingerprint,payload_json FROM paper_report_runs WHERE document_id=? AND request_key=?',(did,key)).fetchone()
        if row:
            if row[0]!=fingerprint:raise EvidenceError(409,'idempotency_conflict','请求标识已用于不同解读任务')
            return self.present(json.loads(row[1]))
        if not command.consent_to_send:raise EvidenceError(422,'report_consent_required','请确认全文文字发送范围后开始分析')
        if not fact_model.status()['configured']:raise EvidenceError(409,'model_not_configured','配置模型后可解读全文；原文预览仍可使用')
        eid,blocks,summary,materials=self.source(did,parse=True)
        chunks=split_source(blocks)
        if not chunks:raise EvidenceError(422,'report_no_text','未提取到可读文字，当前无法生成全文解读')
        if len(chunks)+3>command.max_calls:raise EvidenceError(422,'report_budget_insufficient',f'全文需要{len(chunks)}批阅读及3段综合，请提高请求上限；没有发送模型请求')
        run={'id':'paperreport_'+uuid.uuid4().hex,'document_version_id':did,'extraction_id':eid,'revision':1,
             'state':'queued','created_at':now(),'updated_at':now(),'model_name':fact_model.status()['model_name'],
             'configuration_signature':configuration_signature(),'max_calls':command.max_calls,'calls_reserved':0,
             'automatic_recovery':command.automatic_recovery,
             'lease_token':None,'lease_until':0,'error':None,'report':None,
             'prompt_version':report_model.PROMPT_VERSION,'reader_profile':'计算机技术硕士',
             'policy_version':POLICY_VERSION,
             'source_outline':build_outline(eid,blocks),
             'coverage':{'total_pages':len(summary.pages),'text_pages':sorted({b['region']['pdf_page'] for b in blocks}),
                         'page_gaps':[p.model_dump(mode='json') for p in summary.pages if p.text_status!='text_available'],
                         'material_count':len(materials),'visual_understanding_performed':False,'all_text_batches_processed':False},
             'tasks':[{'id':f'read_{i}','kind':'reading','index':i,'state':'pending','attempts':0,
                       'pages':sorted({s['pdf_page'] for s in chunk}),'source_spans':[{k:s[k] for k in ('span_id','block_id','start','end','pdf_page')} for s in chunk]} for i,chunk in enumerate(chunks)]
                     +synthesis_tasks()}
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT fingerprint,payload_json FROM paper_report_runs WHERE document_id=? AND request_key=?',(did,key)).fetchone()
            if row:
                if row[0]!=fingerprint:raise EvidenceError(409,'idempotency_conflict','请求标识已被使用')
                return self.present(json.loads(row[1]))
            for r in db.execute('SELECT payload_json FROM paper_report_runs WHERE document_id=?',(did,)):
                if json.loads(r[0])['state'] in ('queued','running','paused','pausing','cancelling'):
                    raise EvidenceError(409,'report_busy','本文已有解读任务，请继续或取消已有任务')
            db.execute('INSERT INTO paper_report_runs VALUES (?,?,?,?,?)',(run['id'],did,key,fingerprint,json.dumps(run,ensure_ascii=False)));db.commit()
        return self.present(run)

    def control(self,did,rid,command):
        if command.action=='revalidate':
            try:return self.revalidate(did,rid,command)
            except ValidationError:raise EvidenceError(422,'report_schema_invalid','待核验回复格式仍不符合契约，未发布，也未新增请求')
        with self.transaction(did,rid) as (db,run):
            if command.revision!=run['revision']:raise EvidenceError(409,'stale_report_revision','解读进度已更新，请刷新再操作')
            if command.action=='retry' and command.automatic_recovery is not None:
                if not command.accept_new_request:raise EvidenceError(422,'report_retry_consent','请同意完整流程及有限自动修复')
                if command.max_calls is not None:
                    if command.max_calls<run['max_calls']:raise EvidenceError(422,'report_budget_insufficient','不能减少已批准任务上限')
                    run.setdefault('budget_changes',[]).append({'from':run['max_calls'],'to':command.max_calls,'at':now(),'reason':'explicit_automatic_completion'})
                    run['max_calls']=command.max_calls
                run['automatic_recovery']=command.automatic_recovery
            if command.action=='revise_part':
                if run['state'] not in ('completed','completed_with_gaps') or not run.get('report'):
                    raise EvidenceError(409,'report_action_invalid','仅对已完成报告进行单段修订')
                if not command.accept_new_request or not command.part or not command.feedback or command.max_calls is None:
                    raise EvidenceError(422,'report_revision_consent','修订需要明确反馈、目标部分、新增请求同意与任务上限')
                if command.max_calls<max(run['max_calls'],run['calls_reserved']+1):
                    raise EvidenceError(422,'report_budget_insufficient','修订上限不足以容纳一次新增请求')
                eid,*_=self.source(did)
                if eid!=run['extraction_id'] or configuration_signature()!=run['configuration_signature']:
                    raise EvidenceError(409,'report_source_changed','来源或配置变化，未修订报告')
                evidence={e['id'] for e in run['report']['evidence']}
                if not command.feedback_evidence_ids or any(e not in evidence for e in command.feedback_evidence_ids):
                    raise EvidenceError(422,'report_evidence_invalid','修订反馈必须关联已有原文引文')
                target=next((t for t in run['tasks'] if t.get('part')==command.part),None)
                if not target:raise EvidenceError(409,'report_action_invalid','旧单段报告不支持此修订方式')
                run.setdefault('report_history',[]).append({'at':now(),'report':copy.deepcopy(run['report']),
                    'revised_part':command.part,'calls_reserved':run['calls_reserved']})
                target.setdefault('revision_history',[]).append({'output':copy.deepcopy(target['output']),
                    'attempts':target['attempts'],'usage':target.get('usage',{}),'prompt_version':target.get('prompt_version')})
                target.update(state='pending',review_feedback={'observation':command.feedback,'evidence_ids':command.feedback_evidence_ids})
                run.setdefault('budget_changes',[]).append({'from':run['max_calls'],'to':command.max_calls,'at':now(),'reason':'explicit_part_revision'})
                run.update(max_calls=command.max_calls,state='running',report=None,error=None,lease_token=uuid.uuid4().hex,lease_until=time.time()+180)
            elif command.action=='split_synthesis':
                if not self.present(run)['split_synthesis_available']:raise EvidenceError(409,'report_action_invalid','仅可在全文笔记已完成、旧综合尚未完成时切换')
                if not command.accept_new_request or command.max_calls is None:raise EvidenceError(422,'report_split_consent','请明确确认3段综合的新增请求及本任务上限')
                if command.max_calls<max(run['max_calls'],run['calls_reserved']+3):raise EvidenceError(422,'report_budget_insufficient','新上限必须容纳3段综合；不会隐式增加预算')
                eid,*_=self.source(did)
                if eid!=run['extraction_id']:raise EvidenceError(409,'report_source_changed','解析版本已变化，未改变综合任务或预算')
                if configuration_signature()!=run['configuration_signature']:raise EvidenceError(409,'report_model_changed','模型配置变化，不能自动切换调用')
                old=[t for t in run['tasks'] if t['kind']=='synthesis']
                run.setdefault('superseded_tasks',[]).extend(old)
                run.setdefault('budget_changes',[]).append({'from':run['max_calls'],'to':command.max_calls,'at':now(),'reason':'explicit_split_synthesis'})
                run['tasks']=[t for t in run['tasks'] if t['kind']=='reading']+synthesis_tasks()
                run.update(max_calls=command.max_calls,state='running',error=None,lease_token=uuid.uuid4().hex,lease_until=time.time()+180)
            elif command.action=='extend_budget':
                public=self.present(run)
                if not public['budget_extension_available']:raise EvidenceError(409,'report_action_invalid','当前不需要为未完成步骤追加额度')
                if not command.accept_new_request or command.max_calls is None:raise EvidenceError(422,'report_budget_consent','需要明确确认未完成步骤的请求及新上限')
                if command.max_calls<public['report_continuation']['new_limit']:raise EvidenceError(422,'report_budget_insufficient','新上限不足以处理未完成步骤')
                eid,*_=self.source(did)
                if eid!=run['extraction_id'] or configuration_signature()!=run['configuration_signature']:raise EvidenceError(409,'report_source_changed','来源或配置变化，未增加额度')
                run.setdefault('budget_changes',[]).append({'from':run['max_calls'],'to':command.max_calls,'at':now(),'reason':'explicit_report_continuation'})
                for t in run['tasks']:
                    if t['state']=='failed':t['state']='pending'
                run.update(max_calls=command.max_calls,state='running',error=None,lease_token=uuid.uuid4().hex,lease_until=time.time()+180)
            elif run['state'] in ('running','pausing','cancelling') and run['lease_until']<time.time():
                for t in run['tasks']:
                    if t['state']=='running':t.update(state='failed',error='call_outcome_unknown')
                run.update(state='failed',lease_token=None,lease_until=0,error={'code':'call_outcome_unknown','message':'进程中断，请求结果/费用未知；核查后明确同意才能重试'})
            elif command.action=='pause' and run['state']=='running':run['state']='pausing'
            elif command.action=='cancel' and run['state'] not in ('completed','completed_with_gaps'):
                run['state']='cancelling' if run['state'] in ('running','pausing','cancelling') else 'cancelled'
            elif command.action in ('start','resume','retry'):
                valid={'start':'queued','resume':'paused','retry':'failed'}
                if run['state']!=valid[command.action]:raise EvidenceError(409,'report_action_invalid','当前状态不能执行该操作')
                if self.present(run)['split_synthesis_available']:raise EvidenceError(409,'report_split_required','研读笔记已保存，请仅将报告综合改为分段生成')
                if self.present(run)['budget_extension_available']:raise EvidenceError(409,'report_budget_consent','剩余额度不足，请明确确认剩余报告的新请求上限；已完成内容保留')
                if run.get('policy_version') not in COMPATIBLE_POLICIES:raise EvidenceError(409,'report_replan_required','旧任务采用较大的批次，请按新方案重新准备；历史记录保留，不会自动重发')
                if command.action=='retry' and not command.accept_new_request:raise EvidenceError(422,'report_retry_consent','重试会新增可能计费的请求，请明确同意')
                if run['calls_reserved']>=run['max_calls']:raise EvidenceError(409,'report_budget_exhausted','本任务请求上限已耗尽，可基于历史另建任务')
                if configuration_signature()!=run['configuration_signature']:raise EvidenceError(409,'report_model_changed','模型配置变化，请新建解读任务')
                for t in run['tasks']:
                    if t['state']=='failed':t['state']='pending'
                run.update(state='running',lease_token=uuid.uuid4().hex,lease_until=time.time()+180,error=None)
            else:raise EvidenceError(409,'report_action_invalid','当前状态不能执行该操作')
        return self.get(did,rid)

    def claim(self,did,rid,token):
        eid,blocks,summary,materials=self.source(did)
        by_id={b['id']:b for b in blocks}
        with self.transaction(did,rid) as (db,run):
            if run['lease_token']!=token:return None
            if run['state'] in ('pausing','cancelling'):
                run.update(state='paused' if run['state']=='pausing' else 'cancelled',lease_token=None,lease_until=0);return None
            if run['state']!='running' or any(t['state']=='running' for t in run['tasks']):return None
            if eid!=run['extraction_id']:raise EvidenceError(409,'report_source_changed','原文解析版本变化，旧任务停止')
            if configuration_signature()!=run['configuration_signature'] or not fact_model.status()['configured']:raise EvidenceError(409,'report_model_changed','模型配置变化，旧任务停止')
            task=next((t for t in run['tasks'] if t['state']=='pending'),None)
            if not task:return None
            if task['kind']=='synthesis' and not task.get('part'):raise EvidenceError(409,'report_split_required','全文笔记已保存，请确认仅将报告综合改为3段生成，避免单次报告再次截断')
            if run['calls_reserved']>=run['max_calls']:raise EvidenceError(409,'report_budget_exhausted','请求上限已耗尽，未发送后续请求')
            if task['kind']=='reading':
                spans=[{**s,'text':by_id[s['block_id']]['text'][s['start']:s['end']]} for s in task['source_spans']]
                for s in spans:s['quote_candidates']=quote_candidates(s)
                payload={'batch':task['index']+1,'total_batches':sum(t['kind']=='reading' for t in run['tasks']),'source':spans,
                         'citation_contract':CONTRACT,
                         'notice':'PDF提取顺序尚未人工核对，图表/公式未视觉核验，批次不是章节',
                         'materials':[m for m in materials if m['pdf_page'] in task['pages']]}
            elif task['kind']=='consolidation':
                by_task={t['id']:t for t in run['tasks']}
                notes=[by_task[i]['output'] for i in task['input_task_ids']]
                payload={'all_batch_notes':synthesis_notes(notes),'stage':'long_paper_consolidation','level':task['level']}
            else:
                by_task={t['id']:t for t in run['tasks']}
                notes=[by_task[i]['output'] for i in run['synthesis_input_task_ids']] if run.get('synthesis_input_task_ids') else [t['output'] for t in run['tasks'] if t['kind']=='reading']
                payload={'reader_profile':run['reader_profile'],'all_batch_notes':synthesis_notes(notes),'coverage':run['coverage'],'part':task['part']}
                if task.get('review_feedback'):
                    payload['review_feedback']=task['review_feedback']
                    payload['previous_part']=task['revision_history'][-1]['output']
                if run.get('source_outline'):
                    payload['source_sections']=[{k:s[k] for k in ('id','title','level','pdf_page')} for s in run['source_outline']['sections']]
                    payload['evidence_sections']={e['id']:(section['id'] if (section:=containing_section(run['source_outline'],e)) else None)
                                                  for note in notes for e in note['evidence']}
                    payload['evidence_section_ancestors']={e['id']:section_ids_for_evidence(run['source_outline'],e) for note in notes for e in note['evidence']}
                grouped=len(json.dumps(payload,ensure_ascii=False))>120000
                if grouped:payload=compact_section_context(payload)
                size=len(json.dumps(payload,ensure_ascii=False))
                run['synthesis_context']={'characters':size,'chapter_mapping':'lossless_grouping' if grouped else 'per_evidence','consolidation_level':run.get('consolidation_level',0)}
                if size>140000:
                    if run.get('consolidation_level',0)>=4:raise EvidenceError(422,'report_context_too_large','分层整合后仍超出安全上下文，原始笔记完整保留')
                    inputs=run.get('synthesis_input_task_ids') or [t['id'] for t in run['tasks'] if t['kind']=='reading']
                    groups=[];group=[];group_size=0
                    for tid in inputs:
                        note_size=len(json.dumps(synthesis_notes([by_task[tid]['output']]),ensure_ascii=False))
                        if group and group_size+note_size>60000:groups.append(group);group=[];group_size=0
                        group.append(tid);group_size+=note_size
                    if group:groups.append(group)
                    level=run.get('consolidation_level',0)+1
                    jobs=[{'id':f'consolidate_{level}_{i}','kind':'consolidation','level':level,'input_task_ids':g,'state':'pending','attempts':0} for i,g in enumerate(groups)]
                    index=run['tasks'].index(task);run['tasks'][index:index]=jobs
                    run.update(synthesis_input_task_ids=[j['id'] for j in jobs],consolidation_level=level)
                    run.setdefault('recovery_events',[]).append({'kind':'context_consolidation','at':now(),'level':level,'jobs':len(jobs),'original_notes_preserved':True})
                    return {'kind':'local_replan'},{}
            if len(json.dumps(payload,ensure_ascii=False))>140000:raise EvidenceError(422,'report_context_too_large','整合分批仍超出安全范围，原文笔记已保存')
            if task.get('error'):
                payload['recovery_feedback']={'previous_error':task['error'],'instruction':'上次尝试未通过。返回完整JSON，自查编号/覆盖和章节对应；严格减少重复：综合论证节点最多6个、每项content约100字、每字段最多3个直接支持的引用，避免6000token截断；阅读仍须覆盖每个实质片段。保留关键条件、数字冲突与未知。'}
            if task['attempts'] and not any(a['attempt']==task['attempts'] for a in task.get('attempt_history',[])):
                task.setdefault('attempt_history',[]).append({'attempt':task['attempts'],'input_sha256':task.get('input_sha256'),
                    'prompt_version':task.get('prompt_version',run['prompt_version']),'error':task.get('error'),'legacy_checkpoint':True})
            task.update(state='running',attempts=task['attempts']+1,started_at=now(),input_sha256=digest(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()),
                        prompt_version=report_model.PROMPT_VERSION if task['kind']=='reading' else report_model.SYNTHESIS_VERSION,citation_contract=CONTRACT if task['kind']=='reading' else 'existing_evidence_ids')
            run['calls_reserved']+=1;run['lease_until']=time.time()+180
            return task,payload

    @staticmethod
    def publish(run,output):
        run['coverage']['all_text_batches_processed']=True
        unreadable=[c for t in run['tasks'] if t['kind']=='reading' for c in t['output']['coverage'] if c['disposition']=='unreadable']
        run['coverage']['unreadable_spans']=unreadable
        run.update(report=output,state='completed_with_gaps' if unreadable or run['coverage']['page_gaps'] else 'completed',lease_token=None,lease_until=0)

    @staticmethod
    def complete_synthesis(run,task,output):
        if not task.get('part'):
            PaperReports.publish(run,output);return
        stages=[t for t in run['tasks'] if t['kind']=='synthesis']
        if all(t['state']=='succeeded' for t in stages):
            merged={k:v for t in stages for k,v in t['output'].items()}
            notes=[t['output'] for t in run['tasks'] if t['kind']=='reading']
            PaperReports.publish(run,validate_report(merged,notes,outline=run.get('source_outline')))

    def finish(self,did,rid,token,tid,output,usage):
        # Reject source tampering after the network wait, before publishing output.
        eid,*_=self.source(did)
        with self.transaction(did,rid) as (db,run):
            if run['lease_token']!=token:return False
            if eid!=run['extraction_id'] or configuration_signature()!=run['configuration_signature']:raise EvidenceError(409,'report_source_changed','来源或模型配置在分析期间变化，结果未发布')
            task=next(t for t in run['tasks'] if t['id']==tid)
            if task['state']!='running':return False
            task.update(state='succeeded',output=output,usage=usage,finished_at=now());task.pop('error',None)
            task.pop('validation_failure',None)
            run['lease_until']=time.time()+180
            if task['kind']=='synthesis':
                self.complete_synthesis(run,task,output)
            return True

    def fail(self,did,rid,token,code,message,details=None):
        with self.transaction(did,rid) as (db,run):
            if run['lease_token']!=token:return
            for t in run['tasks']:
                if t['state']=='running':
                    t.update(state='failed',error=code)
                    if details:t['validation_failure']=details
            run.update(state='failed',error={'code':code,'message':message},lease_token=None,lease_until=0)

    def recover_automatically(self,did,rid,code,worker_token):
        """Only opt-in, bounded recovery inside the already approved total budget."""
        public=self.get(did,rid)
        if not public.get('automatic_recovery') or public['state']!='failed':return None
        if code=='report_section_mapping_invalid' and public.get('candidate_available'):
            try:
                recovered=self.revalidate(did,rid,ReportControl(revision=public['revision'],action='revalidate',repair_source_mapping=True))
                if recovered['state'] in ('completed','completed_with_gaps'):return None
                self.control(did,rid,ReportControl(revision=recovered['revision'],action='resume'))
                with self.transaction(did,rid) as (db,current):current['lease_token']=worker_token
                return worker_token
            except (EvidenceError,ValidationError):public=self.get(did,rid)
        retryable={'fact_model_failed','report_schema_invalid','report_evidence_invalid','report_section_mapping_invalid',
                   'report_reading_evidence_missing','report_reading_coverage_invalid','report_quote_invalid','report_notes_too_large'}
        if code not in retryable or public.get('last_failure',{}).get('http_status') in (401,403,429):return None
        failed=next((t for t in public['tasks'] if t['state']=='failed'),None)
        if not failed or failed['attempts']>=2 or public['calls_reserved']>=public['max_calls']:return None
        with self.transaction(did,rid) as (db,run):
            if run['revision']!=public['revision'] or run['state']!='failed':return None
            target=next(t for t in run['tasks'] if t['id']==failed['id']);target['state']='pending'
            run.setdefault('recovery_events',[]).append({'kind':'bounded_retry','at':now(),'task_id':target['id'],'reason':code,'next_attempt':target['attempts']+1})
            run.update(state='running',error=None,lease_token=worker_token,lease_until=time.time()+180)
            return run['lease_token']

    async def work(self,did,rid,token):
        try:
            while True:
                claimed=await run_in_threadpool(self.claim,did,rid,token)
                if not claimed:return
                task,payload=claimed
                if task['kind']=='local_replan':
                    await asyncio.sleep(0);continue
                if task['kind']=='reading':
                    raw=await report_model.read_chunk(payload)
                    await run_in_threadpool(self.save_candidate,did,rid,task,raw)
                    output=validate_notes(raw,payload['source'],task['index'])
                elif task['kind']=='consolidation':
                    raw=await report_model.consolidate(payload)
                    await run_in_threadpool(self.save_candidate,did,rid,task,raw)
                    with self.store.connection() as db:current=self.load(db,did,rid)
                    lookup={t['id']:t for t in current['tasks']}
                    output=validate_consolidation(raw,[lookup[i]['output'] for i in task['input_task_ids']])
                else:
                    raw=await report_model.synthesize(payload)
                    await run_in_threadpool(self.save_candidate,did,rid,task,raw)
                    with self.store.connection() as db:current=self.load(db,did,rid)
                    notes=[t['output'] for t in current['tasks'] if t['kind']=='reading']
                    output=validate_report(raw,notes,task['part'],current.get('source_outline'))
                if not await run_in_threadpool(self.finish,did,rid,token,task['id'],output,getattr(raw,'usage',{})):return
                await asyncio.sleep(0)
        except asyncio.CancelledError:raise
        except ValidationError:
            await run_in_threadpool(self.fail,did,rid,token,'report_schema_invalid','模型输出缺少解读内容或格式不符合契约；已保留笔记，不自动重试')
            recovered=await run_in_threadpool(self.recover_automatically,did,rid,'report_schema_invalid',token)
            if recovered:await self.work(did,rid,recovered)
        except EvidenceError as exc:
            if isinstance(exc,fact_model.ModelFailure):
                from backend.evidence.facts import FactWorksheet
                await run_in_threadpool(FactWorksheet(self.store).save_diagnostics,rid,task['id']+':'+str(task['attempts']),exc.diagnostics)
            await run_in_threadpool(self.fail,did,rid,token,exc.code,exc.message,exc.safe_details if isinstance(exc,QuoteValidationError) else None)
            recovered=await run_in_threadpool(self.recover_automatically,did,rid,exc.code,token)
            if recovered:await self.work(did,rid,recovered)
        except Exception:
            await run_in_threadpool(self.fail,did,rid,token,'report_internal_error','解读意外中断，检查点已保留，不自动重试')

    def launch(self,did,rid):
        with self.store.connection() as db:run=self.load(db,did,rid)
        if run['state'] not in ('running','pausing','cancelling'):return
        key=(str(self.store.db_path),rid,run['lease_token'])
        if key in WORKERS:return
        task=asyncio.create_task(self.work(did,rid,run['lease_token']));WORKERS[key]=task
        task.add_done_callback(lambda _:WORKERS.pop(key,None))
