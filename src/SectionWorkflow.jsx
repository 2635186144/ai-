import React,{useEffect,useRef,useState} from 'react';
import './workflow.css';

const names={reading:'逐段深读',review_rigor:'方法与证据审查',review_skeptic:'独立反方审查',challenge:'逐项质询',response:'原文证据回应',verdict:'逐项裁决'};
const states={queued:'已准备',running:'运行中',pausing:'等待当前请求保存后暂停',paused:'已暂停',failed:'已停止，需处理',completed:'流程已完成',cancelling:'等待当前请求保存后取消',cancelled:'已取消'};
const decisions={upheld:'保留问题',revised:'修订问题边界',dismissed:'证据回应后撤销',undetermined:'证据不足，暂不裁决'};
const judgments={supported:'模型认为支持',partial:'部分支持',unsupported:'缺少支持',contradicted:'模型认为矛盾',unknown:'无法判断'};

export default function SectionWorkflow({packet,ledger,disabled,onRefresh,onActiveChange,onLocate}){
 const [run,setRun]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[consent,setConsent]=useState(false),[retryConsent,setRetryConsent]=useState(false),[limit,setLimit]=useState(ledger.coverage.pending+7),[history,setHistory]=useState([]);
 const refreshRef=useRef(onRefresh),revisionRef=useRef(ledger.revision);
 refreshRef.current=onRefresh;
 const base=`/api/v2/documents/${packet.document_version_id}`;
 const path=`${base}/reading/sections/${packet.section.id}/runs`;
 const active=!!run&&['running','pausing','cancelling'].includes(run.state);
 useEffect(()=>{onActiveChange(active);return ()=>onActiveChange(false);},[active,onActiveChange]);
 async function request(url,options={}){const r=await fetch(url,options),data=await r.json();if(!r.ok)throw Error(data.message||'任务请求失败');return data;}
 useEffect(()=>{const c=new AbortController();request(`${path}?reading_revision=${packet.reading_revision}`,{signal:c.signal}).then(d=>{setHistory(d.items);setRun(d.items[0]||null);}).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return ()=>c.abort();},[path,packet.reading_revision]);
 useEffect(()=>{revisionRef.current=ledger.revision;},[ledger.revision]);
 useEffect(()=>{if(!run)return;let stopped=false,timer;
  async function poll(){try{const d=await request(`${base}/runs/${run.id}`);if(stopped)return;setRun(d);
   if(d.fact_revision>revisionRef.current&&!disabled){revisionRef.current=d.fact_revision;await refreshRef.current();}
  }catch(e){if(!stopped)setError(e.message);}finally{if(!stopped)timer=setTimeout(poll,1500);}}
  timer=setTimeout(poll,800);return ()=>{stopped=true;clearTimeout(timer);};
 },[run?.id,base,disabled]);
 async function create(){setBusy(true);setError('');try{const d=await request(path,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()},body:JSON.stringify({reading_revision:packet.reading_revision,fact_revision:ledger.revision,max_calls:Number(limit),consent_to_send:consent})});setRun(d);setHistory(h=>[d,...h]);setRetryConsent(false);}catch(e){setError(e.message);}finally{setBusy(false);}}
 async function control(action){setBusy(true);setError('');try{const d=await request(`${base}/runs/${run.id}/control`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:run.revision,action,accept_new_request:retryConsent})});setRun(d);setRetryConsent(false);}catch(e){setError(e.message);try{setRun(await request(`${base}/runs/${run.id}`));}catch{}}finally{setBusy(false);}}
 function citations(rows){return rows?.map((c,i)=><blockquote key={i}><button onClick={()=>onLocate(packet.blocks.find(b=>b.id===c.block_id))}>PDF {c.pdf_page} · 回到原文</button><p>{c.quote}</p><small>字符 {c.start}–{c.end}；仅已验证位置</small></blockquote>);}
 const blocked=busy||disabled;
 return <section className="section-workflow" aria-label="分节自动审查任务">
  <header><div className="eyebrow">SECTION WORKFLOW</div><h3>分节运行与对抗审查</h3><p>逐段底稿 → 两个独立审查视角 → 质询 → 原文证据回应 → 逐项裁决。范围仅当前节，模拟审阅角色由同一配置模型执行。</p></header>
  <p>预计最多 {ledger.coverage.pending+5} 次首次请求；已有底稿保留。每次最多 6000 输出 token；请求上限不是费用或总 token 预算，失败也可能计费。</p>
  {disabled&&<p className="reading-hint">请先保存或撤销底稿与复核修改，再操作任务。</p>}
  <div className="workflow-create"><label>本任务请求上限<input aria-label="本任务请求上限" type="number" min={ledger.coverage.pending+5} max="200" value={limit} disabled={blocked||active} onChange={e=>setLimit(e.target.value)}/></label>
   <label className="workflow-consent"><input type="checkbox" checked={consent} disabled={blocked||active} onChange={e=>setConsent(e.target.checked)}/>我同意将本节原文、相邻节内上下文、材料位置、底稿及后续审查记录发给已配置模型；不含原始 PDF 文件和图片。</label>
   <button disabled={blocked||active||!consent||!ledger.model.configured||!!run&&['queued','paused'].includes(run.state)} onClick={create}>准备本节任务</button>
  </div>
  {history.length>1&&<label>历史任务<select aria-label="历史分节任务" value={run?.id||''} disabled={blocked||active} onChange={e=>setRun(history.find(r=>r.id===e.target.value))}>{history.map(r=><option key={r.id} value={r.id}>{r.created_at} · {r.id.slice(-8)}</option>)}</select></label>}
  {error&&<p role="alert" className="reading-error">{error}</p>}
  {run&&<div className="workflow-run"><div className="workflow-progress"><strong>{states[run.state]||run.state}</strong><span>{run.progress.completed} / {run.progress.total} 步 · 已占请求 {run.calls_reserved} / {run.max_calls}</span><progress aria-label="分节任务进度" max={run.progress.total} value={run.progress.completed}/></div>
   <small>任务 {run.id.slice(-8)} · 底稿 v{run.fact_revision} · {run.model_name} · 不自动重发失败请求</small>
   {run.source_preparation?.accepted_unverified_extraction&&!run.source_preparation?.human_structure_verified&&<p className="reading-hint">来源采用自动解析结果：章节、阅读顺序或材料位置尚未完全人工核对。本次结果需保留这些结构不确定项。</p>}
   {run.stale&&<p role="alert">当前来源或底稿已变化。历史结果保留，不适用于当前版本；请新建任务。</p>}
   {run.interrupted&&<p role="alert">任务租约已过期，可能发生进程中断。先恢复检查点；未确认的请求不会自动重发。</p>}
   {run.error&&<p role="alert">{run.error.message}</p>}
   <div className="workflow-controls">
    {run.state==='queued'&&<button disabled={blocked||run.stale} onClick={()=>control('start')}>开始本节自动流程</button>}
    {run.state==='running'&&!run.interrupted&&<button disabled={busy} onClick={()=>control('pause')}>暂停任务</button>}
    {run.state==='paused'&&<button disabled={blocked||run.stale} onClick={()=>control('resume')}>继续任务</button>}
    {run.interrupted&&<button disabled={busy} onClick={()=>control('pause')}>恢复中断检查点</button>}
    {run.state==='failed'&&!run.stale&&<><label><input type="checkbox" checked={retryConsent} onChange={e=>setRetryConsent(e.target.checked)}/>我已核查失败记录，同意新增请求重试</label><button disabled={blocked||!retryConsent||run.calls_reserved>=run.max_calls} onClick={()=>control('retry')}>重试失败步骤</button></>}
    {!['completed','cancelled','cancelling'].includes(run.state)&&<button disabled={busy} onClick={()=>control('cancel')}>取消后续步骤</button>}
   </div>
   <ol className="workflow-steps">{run.tasks.map(t=><li key={t.id}><span>{names[t.kind]}{t.unit_id?` · ${t.unit_id.slice(-6)}`:''}</span><b>{({pending:'待执行',running:'处理中',succeeded:'已保存',failed:'失败'})[t.state]}</b></li>)}</ol>
   {run.tasks.filter(t=>t.output&&t.kind!=='reading').map(t=><details className="workflow-output" key={t.id} open={t.kind==='verdict'}><summary>{names[t.kind]} · 模型意见，语义未独立核验</summary>
    {t.output.assessments?.map(a=><article key={a.fact_id}><h4>{a.fact_id.slice(-12)} · {judgments[a.judgment]}</h4><p>{a.reason}</p>{citations(a.citations)}</article>)}
    {t.output.issues?.map(i=><article key={i.id}><h4>{i.id}</h4><p>{i.concern}</p><p>待补：{i.needed_evidence}</p>{citations(i.citations)}</article>)}
    {t.output.items?.length===0&&<p>本轮没有待处理的问题；不表示论文没有问题。</p>}
    {t.output.items?.map(i=><article key={i.issue_id}><h4>{i.issue_id} {decisions[i.decision]||''}</h4><p>{i.question||i.answer||i.reason}</p>{i.alternative_explanation&&<p>替代解释：{i.alternative_explanation}</p>}{i.status&&<p>回应状态：{({addressed:'模型认为已回应',partial:'部分回应',unresolved:'未解决'})[i.status]}</p>}{(i.missing_evidence||i.remaining_evidence)&&<p>仍需证据：{i.missing_evidence||i.remaining_evidence}</p>}{citations(i.citations)}</article>)}
   </details>)}
   {run.state==='completed'&&<p className="fact-notice">本节流程已完成；技术执行完成不等于科研结论成立。保留 {run.reading_gaps.length} 个有阅读缺口的单元，图表和外部证据仍未验证。</p>}
   <a href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(run,null,2))}`} download={`section-workflow-${run.id}.json`}>导出任务、引文与裁决记录</a>
  </div>}
 </section>;
}
