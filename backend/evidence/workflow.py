"""Durable, bounded section workflow. Never restart a paid call automatically."""
import asyncio
import json
import time
import uuid
from contextlib import contextmanager
from typing import Literal

from pydantic import Field, ValidationError
from starlette.concurrency import run_in_threadpool

from design.contracts import Contract
from backend.evidence import fact_model, workflow_review
from backend.evidence.fact_models import GenerateFactCommand
from backend.evidence.facts import FactWorksheet
from backend.evidence.parser import EvidenceError, digest
from backend.evidence.store import now

LEASE_SECONDS = 180
WORKERS = {}  # Strong references; SQLite token is the authority, not this cache.


class CreateWorkflow(Contract):
    reading_revision: int = Field(ge=1)
    fact_revision: int = Field(ge=1)
    max_calls: int = Field(ge=1, le=200)
    consent_to_send: bool


class WorkflowControl(Contract):
    revision: int = Field(ge=1)
    action: Literal['start', 'pause', 'resume', 'retry', 'cancel']
    accept_new_request: bool = False


def configuration_signature():
    import os
    return digest(json.dumps([os.getenv(k, '') for k in ('MODEL_NAME', 'MODEL_BASE_URL', 'MODEL_API_KEY')]).encode())


class SectionWorkflow:
    def __init__(self, store):
        self.store = store
        self.facts = FactWorksheet(store)

    @staticmethod
    def present(run):
        public = {k: v for k, v in run.items() if k not in ('configuration_signature', 'lease_token')}
        done = sum(t['state'] == 'succeeded' for t in run['tasks'])
        public['progress'] = {'completed': done, 'total': len(run['tasks']), 'percent': round(100*done/len(run['tasks']))}
        public['semantic_verified'] = False
        public['scope'] = 'selected_section_only'
        public['restart_requires_user_action'] = True
        public['automatic_retries'] = 0
        public['call_limit_notice'] = '限制请求次数；每次最多6000输出token，不是人民币或总token预算。失败/中断调用也占次数，费用以服务商为准。'
        return public

    def load(self, db, did, rid):
        row = db.execute('SELECT payload_json FROM workflow_runs WHERE id=? AND document_id=?', (rid, did)).fetchone()
        if not row:
            raise EvidenceError(404, 'workflow_not_found', '分节任务不存在')
        return json.loads(row[0])

    @contextmanager
    def transaction(self, did, rid):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self.load(db, did, rid)
            yield db, run
            db.execute('UPDATE workflow_runs SET payload_json=? WHERE id=?', (json.dumps(run, ensure_ascii=False), rid))
            db.commit()

    @staticmethod
    def event(db, run, kind):
        run['revision'] += 1
        run['updated_at'] = now()
        entry = {'at': run['updated_at'], 'revision': run['revision'], 'event': kind, 'state': run['state']}
        db.execute('INSERT INTO workflow_events(run_id,payload_json) VALUES (?,?)', (run['id'], json.dumps(entry)))

    def create(self, did, sid, command, key):
        fingerprint = digest((sid+command.model_dump_json()).encode())
        # Retries with the same key work even after the worker advances the ledger.
        with self.store.connection() as db:
            row = db.execute('SELECT fingerprint,payload_json FROM workflow_runs WHERE document_id=? AND request_key=?', (did, key)).fetchone()
        if row:
            if row[0] != fingerprint:
                raise EvidenceError(409, 'idempotency_conflict', '请求标识已用于不同任务')
            return self.present(json.loads(row[1]))
        ledger = self.facts.get(did, sid, command.reading_revision)
        packet = self.facts.packet(did, sid, command.reading_revision)
        if not command.consent_to_send:
            raise EvidenceError(422, 'workflow_consent_required', '请先确认本节原文、底稿与审查记录的模型发送范围')
        if not fact_model.status()['configured']:
            raise EvidenceError(409, 'model_not_configured', '尚未配置模型，未创建自动任务')
        tasks = [{'id': f'read:{u["id"]}', 'kind': 'reading', 'unit_id': u['id'], 'state': 'pending', 'attempts': 0}
                 for u in ledger['units'] if u['draft'] is None]
        tasks += [{'id': k, 'kind': k, 'state': 'pending', 'attempts': 0}
                  for k in ('review_rigor', 'review_skeptic', 'challenge', 'response', 'verdict')]
        if command.max_calls < len(tasks):
            raise EvidenceError(422, 'workflow_budget_insufficient', f'首次执行最多需要{len(tasks)}次请求，请调整上限；没有发送请求')
        run = {'id': 'sectionrun_'+uuid.uuid4().hex, 'document_version_id': did, 'section_id': sid,
               'section_title': packet['section']['title'], 'reading_revision': command.reading_revision,
               'extraction_id': packet['extraction_id'],
               'source_preparation': packet.get('source_preparation', {}),
               'ledger_id': ledger['id'], 'fact_revision': command.fact_revision, 'revision': 1,
               'state': 'queued', 'tasks': tasks, 'max_calls': command.max_calls, 'calls_reserved': 0,
               'model_name': fact_model.status()['model_name'], 'configuration_signature': configuration_signature(),
               'created_at': now(), 'updated_at': now(), 'lease_token': None, 'lease_until': 0, 'error': None,
               'reading_gaps': [], 'usage': {'reported_total_tokens': 0, 'calls_without_usage': 0}}
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT fingerprint,payload_json FROM workflow_runs WHERE document_id=? AND request_key=?', (did, key)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise EvidenceError(409, 'idempotency_conflict', '请求标识已用于不同任务')
                return self.present(json.loads(previous[1]))
            self.facts.current_reading(db, packet)
            if self.facts.latest(db, ledger['id'])['revision'] != command.fact_revision:
                raise EvidenceError(409, 'stale_fact_revision', '底稿已变化，请刷新再创建任务')
            for row in db.execute('SELECT payload_json FROM workflow_runs WHERE document_id=? AND section_id=?', (did, sid)):
                other = json.loads(row[0])
                if other['reading_revision'] == command.reading_revision and other['state'] in ('queued', 'running', 'pausing', 'paused'):
                    raise EvidenceError(409, 'workflow_busy', '本节已有活动任务，请继续或取消已有任务')
            db.execute('INSERT INTO workflow_runs VALUES (?,?,?,?,?,?,?)', (run['id'], did, sid, command.reading_revision, key, fingerprint, json.dumps(run, ensure_ascii=False)))
            db.commit()
        return self.present(run)

    def get(self, did, rid):
        # Reading old runs remains possible after the source/ledger changes.
        with self.store.connection() as db:
            run = self.load(db, did, rid)
            events = [json.loads(r[0]) for r in db.execute('SELECT payload_json FROM workflow_events WHERE run_id=? ORDER BY sequence DESC LIMIT 60', (rid,))]
            latest = self.facts.latest(db, run['ledger_id'])
            current_reading = db.execute('SELECT max(revision) FROM reading_versions WHERE extraction_id=?', (latest['extraction_id'],)).fetchone()[0]
        return {**self.present(run), 'events': list(reversed(events)),
                'stale': latest['revision'] != run['fact_revision'] or current_reading != run['reading_revision'],
                'interrupted': run['state'] in ('running', 'pausing', 'cancelling') and run['lease_until'] < time.time()}

    def list(self, did, sid, reading_revision):
        self.store.detail(did)
        with self.store.connection() as db:
            runs = [json.loads(r[0]) for r in db.execute('SELECT payload_json FROM workflow_runs WHERE document_id=? AND section_id=? AND reading_revision=? ORDER BY rowid DESC LIMIT 20', (did, sid, reading_revision))]
        return {'items': [self.get(did, r['id']) for r in runs]}

    def guard(self, db, run, packet):
        self.facts.current_reading(db, packet)
        ledger = self.facts.latest(db, run['ledger_id'])
        if ledger['revision'] != run['fact_revision']:
            raise EvidenceError(409, 'workflow_stale_facts', '底稿已被其他操作修改；旧任务保留，新底稿需要新建审查任务')
        if configuration_signature() != run['configuration_signature'] or not fact_model.status()['configured']:
            raise EvidenceError(409, 'workflow_model_changed', '模型配置发生变化，请基于当前配置新建任务')
        return ledger

    def recover(self, db, run):
        """After lease expiry: reconcile a committed reading, never resend unknown work."""
        for task in run['tasks']:
            if task['state'] != 'running':
                continue
            row = db.execute('SELECT state,result_revision FROM fact_requests WHERE ledger_id=? AND request_key=?',
                             (run['ledger_id'], task.get('request_key', ''))).fetchone() if task['kind'] == 'reading' else None
            if row and row[0] == 'succeeded':
                task['state'] = 'succeeded'
                run['fact_revision'] = row[1]
            else:
                task['state'] = 'failed'
                task['error'] = {'code': 'call_outcome_unknown', 'message': '进程中断：请求结果和费用可能未知；不会自动重发'}
                if row and row[0] == 'pending':
                    db.execute("UPDATE fact_requests SET state='failed',error_code='call_outcome_unknown' WHERE ledger_id=? AND request_key=?", (run['ledger_id'], task['request_key']))
        run.update(state='failed' if any(t['state'] == 'failed' for t in run['tasks']) else 'paused', lease_token=None, lease_until=0)
        self.event(db, run, 'interruption_checkpoint')

    def control(self, did, rid, command):
        with self.store.connection() as db:
            initial = self.load(db, did, rid)
        packet = self.facts.packet(did, initial['section_id'], initial['reading_revision']) if command.action in ('start', 'resume', 'retry') and initial['state'] not in ('running', 'pausing', 'cancelling') else None
        with self.transaction(did, rid) as (db, run):
            if run['revision'] != command.revision:
                raise EvidenceError(409, 'stale_workflow_revision', '任务进度已更新，请刷新后操作')
            if run['state'] in ('running', 'pausing', 'cancelling') and run['lease_until'] < time.time():
                self.recover(db, run)
                # Commit recovery as its own checkpoint. Explicit second action is required.
                return self.present(run)
            if command.action == 'pause':
                if run['state'] == 'running':
                    run['state'] = 'pausing'
                elif run['state'] == 'queued':
                    run['state'] = 'paused'
                elif run['state'] not in ('paused', 'pausing'):
                    raise EvidenceError(409, 'workflow_action_invalid', '当前状态不能暂停')
            elif command.action == 'cancel':
                if run['state'] == 'completed':
                    raise EvidenceError(409, 'workflow_action_invalid', '已完成任务不能取消')
                run['state'] = 'cancelling' if run['state'] in ('running', 'pausing', 'cancelling') else 'cancelled'
            else:
                allowed = {'start': ('queued',), 'resume': ('paused',), 'retry': ('failed',)}
                if run['state'] not in allowed[command.action]:
                    raise EvidenceError(409, 'workflow_action_invalid', '当前状态不能执行该操作')
                if command.action == 'retry' and not command.accept_new_request:
                    raise EvidenceError(422, 'retry_consent_required', '失败请求可能已计费；须明确同意新增一次请求后重试')
                self.guard(db, run, packet)
                if run['calls_reserved'] >= run['max_calls']:
                    raise EvidenceError(409, 'workflow_budget_exhausted', '本任务请求上限已用完；不会扩大预算，请新建任务')
                if command.action == 'retry':
                    for task in run['tasks']:
                        if task['state'] == 'failed':
                            task['state'] = 'pending'
                run.update(state='running', lease_token=uuid.uuid4().hex, lease_until=time.time()+LEASE_SECONDS, error=None)
            self.event(db, run, command.action)
            return self.present(run)

    def claim(self, did, rid, token):
        packet_run = self.get(did, rid)
        packet = self.facts.packet(did, packet_run['section_id'], packet_run['reading_revision'])
        with self.transaction(did, rid) as (db, run):
            if run['lease_token'] != token:
                return None
            if run['state'] in ('pausing', 'cancelling'):
                run.update(state='paused' if run['state'] == 'pausing' else 'cancelled', lease_token=None, lease_until=0)
                self.event(db, run, 'checkpoint_stop')
                return None
            if run['state'] != 'running':
                return None
            ledger = self.guard(db, run, packet)
            if any(t['state'] == 'running' for t in run['tasks']):
                # Another dispatcher holding the same run token already reserved a step.
                return None
            task = next((t for t in run['tasks'] if t['state'] == 'pending'), None)
            if task is None:
                run.update(state='completed', lease_token=None, lease_until=0)
                run['fact_snapshot'] = workflow_review.facts_for(ledger)
                run['reading_gaps'] = [{'unit_id': u['id'], 'disposition': u['draft']['disposition'],
                                        'unknowns': u['draft']['unknowns'], 'dependencies': u['draft']['unresolved_dependencies']}
                                       for u in ledger['units'] if u['draft'] and (u['draft']['unknowns'] or u['draft']['unresolved_dependencies'] or u['draft']['disposition'] == 'blocked')]
                self.event(db, run, 'completed_with_scope_and_gaps')
                return None
            if run['calls_reserved'] >= run['max_calls']:
                raise EvidenceError(409, 'workflow_budget_exhausted', '请求上限已用完，没有继续发送')
            # Validate input sizes before reserving a call; never truncate source silently.
            if task['kind'] == 'reading':
                payload, _ = self.facts.build_payload(packet, self.facts.unit(ledger, task['unit_id']))
            else:
                payload = workflow_review.payload_for(task['kind'], packet, ledger, run['tasks'])
                if 'fact_snapshot' not in run:
                    run['fact_snapshot'] = workflow_review.facts_for(ledger)
            task.update(state='running', attempts=task['attempts']+1, started_at=now())
            task['base_fact_revision'] = ledger['revision']
            task['input_sha256'] = digest(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode())
            task['request_key'] = f'{rid}:{task["id"]}:{task["attempts"]}'
            run['calls_reserved'] += 1
            run['lease_until'] = time.time()+LEASE_SECONDS
            self.event(db, run, 'call_reserved:'+task['id'])
            return run, task, ledger, packet, payload

    def finish(self, did, rid, token, tid, output, fact_revision, usage):
        with self.store.connection() as db:
            initial = self.load(db, did, rid)
        packet = self.facts.packet(did, initial['section_id'], initial['reading_revision'])
        with self.transaction(did, rid) as (db, run):
            if run['lease_token'] != token:
                return False
            # Reading calls already fenced their commit against current source and ledger.
            if fact_revision is not None:
                if self.facts.latest(db, run['ledger_id'])['revision'] != fact_revision:
                    raise EvidenceError(409, 'workflow_stale_facts', '生成后底稿被其他操作修改，任务已停止')
                run['fact_revision'] = fact_revision
            self.guard(db, run, packet)
            task = next(t for t in run['tasks'] if t['id'] == tid)
            if task['state'] != 'running':
                return False
            task.update(state='succeeded', finished_at=now(), output=output, usage=usage)
            task.pop('error', None)
            if type(usage.get('total_tokens')) is int:
                run['usage']['reported_total_tokens'] += usage['total_tokens']
            else:
                run['usage']['calls_without_usage'] += 1
            run['lease_until'] = time.time()+LEASE_SECONDS
            self.event(db, run, 'checkpoint:'+tid)
            return True

    def fail(self, did, rid, token, code, message):
        with self.transaction(did, rid) as (db, run):
            if run['lease_token'] != token:
                return
            for task in run['tasks']:
                if task['state'] == 'running':
                    task.update(state='failed', error={'code': code, 'message': message})
            run.update(state='failed', lease_token=None, lease_until=0, error={'code': code, 'message': message})
            self.event(db, run, 'failed_no_automatic_retry')

    async def work(self, did, rid, token):
        try:
            while True:
                claimed = await run_in_threadpool(self.claim, did, rid, token)
                if claimed is None:
                    return
                run, task, ledger, packet, payload = claimed
                revision = None
                if task['kind'] == 'reading':
                    command = GenerateFactCommand(reading_revision=run['reading_revision'], revision=run['fact_revision'], unit_id=task['unit_id'])
                    updated = await self.facts.generate(did, run['section_id'], command, task['request_key'])
                    revision = updated['revision']
                    unit = self.facts.unit(updated, task['unit_id'])
                    output = {'fact_revision': revision, 'disposition': unit['draft']['disposition'], 'semantic_verified': False}
                    usage = unit['producer'].get('usage', {})
                else:
                    raw = await workflow_review.generate(task['kind'], payload)
                    usage = getattr(raw, 'usage', {})
                    output = workflow_review.validate(task['kind'], raw, packet, ledger, run['tasks'])
                    output['model_name'] = run['model_name']
                if not await run_in_threadpool(self.finish, did, rid, token, task['id'], output, revision, usage):
                    return
                await asyncio.sleep(0)  # Let control requests reach the next checkpoint.
        except asyncio.CancelledError:
            # Keep durable running reservation; explicit recovery after lease expiry.
            raise
        except EvidenceError as exc:
            await run_in_threadpool(self.fail, did, rid, token, exc.code, exc.message)
        except ValidationError:
            await run_in_threadpool(self.fail, did, rid, token, 'invalid_review_schema', '模型输出格式不符合审查契约，未保存；不会自动重试')
        except Exception:
            # No raw exception strings: SDK errors can contain authentication information.
            await run_in_threadpool(self.fail, did, rid, token, 'workflow_internal_error', '任务意外中断，已保留检查点；未自动重试')

    def launch(self, did, rid):
        with self.store.connection() as db:
            run = self.load(db, did, rid)
        if run['state'] not in ('running', 'pausing', 'cancelling'):
            return
        key = (str(self.store.db_path), rid, run['lease_token'])
        if key in WORKERS:
            return
        task = asyncio.create_task(self.work(did, rid, run['lease_token']))
        WORKERS[key] = task
        task.add_done_callback(lambda _: WORKERS.pop(key, None))
