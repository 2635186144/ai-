import React,{useEffect,useState} from 'react';
import './paper-report.css';
import PaperStructure from './PaperStructure';
import ReportSupport,{DiscussionQuestions} from './ReportSupport';
import {useSourceVisuals,referencedVisuals,VisualCards,VisualViewer,SourceVisualGallery} from './PaperVisuals';

const states={queued:'准备就绪',running:'正在研读全文',pausing:'保存当前批次后暂停',paused:'已暂停',failed:'解读已停止',completed:'全文文本解读已完成',completed_with_gaps:'解读报告已生成，存在未读缺口',cancelling:'保存当前批次后取消',cancelled:'已取消'};
const blocks=[['01','论文信息','题目、研究方向与研究主旨'],['02','摘要解读','研究背景、方法与主要结果'],['03','全文架构','章节安排与论证关系'],['04','核心概念与方法','方法机制、关键设计与实验证据'],['05','创新点与边界','作者提出的贡献及其证据范围'],['06','阅读重点','阅读顺序、关注问题与必要基础']];

// Typographic emphasis uses only exact phrases already present in the prose.
// Never rewrite quotations or label a highlighted phrase as verified evidence.
function EmphasizedText({text,terms=[]}){
 const quoted=[...text.matchAll(/[“「]([^”」\n]{2,18})[”」]/g)].map(m=>m[1]);
 const candidates=[...new Set([...terms,...quoted])].filter(t=>t.length>=2&&t.length<=24);
 const ranges=[];
 for(const term of candidates){
  const start=text.indexOf(term),end=start+term.length;
  if(start<0||ranges.some(r=>start<r.end&&end>r.start))continue;
  ranges.push({start,end});if(ranges.length===3)break;
 }
 ranges.sort((a,b)=>a.start-b.start);
 const nodes=[];let cursor=0;
 for(const {start,end} of ranges){nodes.push(text.slice(cursor,start),<mark className="report-keyphrase" key={start}>{text.slice(start,end)}</mark>);cursor=end;}
 nodes.push(text.slice(cursor));return nodes;
}

// Existing reports stay intact. The compact view shows complete sentences,
// explicitly labelled as an excerpt, rather than pretending to rewrite them.
function ReadingText({text='',full=false,limit=180,emphasis}){
 const [opened,setOpened]=useState(false);
 const sentences=text.match(/[\s\S]+?(?:[。！？][”’」』]?|\n+|[.!?](?=\s|$)|$)/g)||[text];
 let excerpt='';
 for(const sentence of sentences){if(excerpt.length>=limit)break;excerpt+=sentence;}
 const shortened=excerpt.length<text.length,showAll=full||opened;
 return <div className="report-reading-text"><p className="report-prose">{emphasis?<EmphasizedText text={showAll?text:excerpt} terms={emphasis}/>:showAll?text:excerpt}</p>{shortened&&!full&&<button className="report-expand" aria-expanded={opened} onClick={()=>setOpened(!opened)}>{opened?'收起完整解读':'节选 · 展开完整解读'}<span aria-hidden="true">{opened?' −':' ＋'}</span></button>}</div>;
}

function ReportDetail({full,summary,children}){
 return <details className="report-detail" open={full||undefined}><summary>{summary}</summary>{children}</details>;
}

export default function PaperReport({documentId,extractionId,onLocate,disabled}){
 const [plan,setPlan]=useState(null),[runs,setRuns]=useState([]),[run,setRun]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [consent,setConsent]=useState(false),[retryConsent,setRetryConsent]=useState(false),[limit,setLimit]=useState(20);
 const [splitConsent,setSplitConsent]=useState(false);
 const [fullReading,setFullReading]=useState(false);
 useEffect(()=>{setFullReading(false);},[documentId,run?.id]);
 const base=`/api/v2/documents/${documentId}`;
 async function api(path,options={}){const r=await fetch(base+path,options),d=await r.json();if(!r.ok)throw Error(d.message||'解读请求失败');return d;}
 useEffect(()=>{const c=new AbortController();setPlan(null);setRun(null);setRuns([]);setError('');setConsent(false);setRetryConsent(false);if(!documentId)return ()=>c.abort();
  Promise.all([api('/report-plan',{signal:c.signal}),api('/reports',{signal:c.signal})]).then(([p,h])=>{setPlan(p);setLimit(p.recommended_max_calls||Math.min(500,Math.max(24,(p.calls_needed||18)*2+6)));setRuns(h.items);setRun(h.items[0]||null);}).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return ()=>c.abort();
 },[documentId,extractionId]);
 const active=run&&['running','pausing','cancelling'].includes(run.state);
 useEffect(()=>{setSplitConsent(false);},[run?.id,run?.max_calls,run?.calls_reserved,run?.synthesis_recovery?.minimum_new_limit,run?.report_continuation?.new_limit]);
 useEffect(()=>{if(!run||!active)return;let stop=false,timer;
  async function poll(){try{const d=await api('/reports/'+run.id);if(!stop)setRun(d);}catch(e){if(!stop)setError(e.message);}finally{if(!stop)timer=setTimeout(poll,1400);}}
  timer=setTimeout(poll,500);return ()=>{stop=true;clearTimeout(timer);};
 },[run?.id,active,base]);
 async function control(action,target=run){setBusy(true);setError('');try{const d=await api(`/reports/${target.id}/control`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:target.revision,action,accept_new_request:['split_synthesis','extend_budget'].includes(action)?splitConsent:retryConsent,...(action==='split_synthesis'?{max_calls:target.synthesis_recovery.minimum_new_limit}:action==='extend_budget'?{max_calls:target.report_continuation.new_limit}:{})})});setRun(d);setRetryConsent(false);setSplitConsent(false);}catch(e){setError(e.message);try{setRun(await api('/reports/'+target.id));}catch{}}finally{setBusy(false);}}
 async function begin(){setBusy(true);setError('');try{const d=await api('/reports',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()},body:JSON.stringify({max_calls:Number(limit),consent_to_send:consent,automatic_recovery:true})});setRun(d);setRuns(r=>[d,...r]);await control('start',d);}catch(e){setError(e.message);}finally{setBusy(false);}}
 async function finishExisting(){setBusy(true);setError('');try{setRun(await api(`/reports/${run.id}/control`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:run.revision,action:'retry',automatic_recovery:true,accept_new_request:true,max_calls:run.recommended_completion_limit||Math.min(500,run.calls_reserved+24)})}));}catch(e){setError(e.message);}finally{setBusy(false);}}
 const report=run?.report,evidence=Object.fromEntries((report?.evidence||run?.tasks.filter(t=>t.kind==='reading'&&t.state==='succeeded').flatMap(t=>t.output?.evidence||[])||[]).map(e=>[e.id,e]));
 const visuals=useSourceVisuals(documentId,Boolean(report));
 const [openVisual,setOpenVisual]=useState(null);
 useEffect(()=>setOpenVisual(null),[documentId,run?.id]);
 // Older outputs sometimes append identification commentary after a quoted title.
 // Keep that commentary available, but don't typeset it as part of the heading.
 const quotedTitle=report?.title.text.match(/^(《[^》]+》)\s*([\s\S]+)$/);
 const titleIdentity=run?.title_identity||plan?.title_identity;
 const gapSpans=Object.fromEntries((run?.tasks||[]).flatMap(t=>t.source_spans||[]).map(s=>[s.span_id,s]));
 const gapPages=new Map();
 for(const gap of run?.coverage?.unreadable_spans||[]){const span=gapSpans[gap.span_id];if(!span)continue;const page=gapPages.get(span.pdf_page)||{span,reasons:new Set(),count:0};page.reasons.add(gap.reason);page.count++;gapPages.set(span.pdf_page,page);}
 function refs(ids=[]){const valid=[...new Set(ids)].filter(id=>evidence[id]);if(!valid.length)return null;const buttons=items=>items.map(id=><button key={id} onClick={()=>onLocate(evidence[id])} title={evidence[id].quote}>PDF {evidence[id].pdf_page} · 原文依据</button>);return <div className="report-evidence">{buttons(valid.slice(0,2))}{valid.length>2&&<details><summary>另 {valid.length-2} 处来源</summary>{buttons(valid.slice(2))}</details>}</div>;}
 function insight(item,limit=180,emphasis){return <>{item?.text&&<ReadingText key={item.text} text={item.text} full={fullReading} limit={limit} emphasis={emphasis}/>}{refs(item?.evidence_ids)}</>;}
 function supportNotice(target){const entry=run?.support_review?.entries.find(e=>e.target===target);if(!entry||['model_supported','not_reviewed'].includes(entry.status))return null;return <p className="report-support-caution">此段仍有待核对的推断或条件，请结合下方支持核查与原文判断。</p>;}
 return <section className="paper-report" aria-label="全文解读报告">
  <header className="report-heading"><span className="eyebrow">EVIDENCEMAP / PAPER READER</span><h2>{report?'论文研读札记':'从一篇论文，建立完整理解。'}</h2><p>研究问题 → 方法设计 → 实验证据 → 贡献与边界 → 阅读重点</p><span className="report-edition">全文解读 · 来源可查</span></header>
  {!report&&<div className="report-outline">{blocks.map(([n,title,description])=><div key={n}><span>{n}</span><strong>{title}</strong><p>{description}</p></div>)}</div>}
  {(plan||run)&&<details className="report-process" open={!report}><summary>{report?'阅读记录与生成设置':'全文阅读与生成设置'}<span>{report?`${run.coverage.text_pages.length}/${run.coverage.total_pages} 页文字覆盖`:'确认发送范围后开始'}</span></summary>
  {plan&&<div className="report-start">
   <div><strong>{plan.parsed?`${plan.total_pages} 页原文 · ${plan.chunks} 批全文阅读 + ${plan.synthesis_calls||3} 段报告综合`:'已保存原件，开始时会自动解析全文'}</strong><p>{plan.parsed?`${plan.text_pages.length} 页有提取文字；无需先手工确认章节。`:'系统将自动处理正文、附录和提取缺口。'} 图表和公式仍需保留未视觉核验的边界。</p></div>
   {run?.requires_replan&&<p className="report-replan-notice">上次任务采用旧版大批次，已停止原样重试。请重新确认发送范围和请求上限，按上方的新批次方案生成报告；下方失败记录会保留，不会自动新增请求。</p>}
   {!active&&!run?.split_synthesis_available&&!run?.budget_extension_available&&run?.error?.code!=='report_context_too_large'&&(!run||['completed','completed_with_gaps','cancelled','failed'].includes(run.state))&&<details className="report-start-options" open={!report}>
    <summary>{report?'重新生成解读':'开始全文解读'}</summary>
    <p>预计 {plan.calls_needed||'若干'} 次基础分析，预算最多 {limit} 次，包含长文整合与有限自动修复；只按实际调用使用。</p>
    <details><summary>高级设置：请求预算</summary><label>本次请求次数上限<input type="number" aria-label="全文解读请求上限" min={plan.calls_needed||1} max={plan.max_report_calls||500} value={limit} onChange={e=>setLimit(e.target.value)} disabled={busy||disabled}/></label></details>
    <p className="report-fineprint">按批发送全文提取文字、页码、图表位置与阅读笔记；不发送原始PDF或图片。每次最多6000输出token，次数上限不等于费用上限，失败请求也可能计费。</p>
    <label className="report-consent"><input type="checkbox" checked={consent} disabled={busy||disabled} onChange={e=>setConsent(e.target.checked)}/>我同意以上范围发送给已配置模型，在所示预算内完成解读及有限自动修复。</label>
    <button className="dark-button" disabled={busy||disabled||!consent||!plan.model.configured} onClick={begin}>{busy?'正在准备…':run?.requires_replan?'按新方案分析全文，生成报告':'分析全文，生成解读报告'}</button>
    {!plan.model.configured&&<p>尚未配置模型；可以使用下方原文预览，当前不会生成或伪装解读结果。</p>}
   </details>}
  </div>}
  {error&&<p role="alert" className="reading-error">{error}</p>}
  {run&&<div className="report-progress"><div><strong>{states[run.state]}</strong><span>{run.progress.completed}/{run.progress.total} 步 · 请求 {run.calls_reserved}/{run.max_calls}</span></div><progress aria-label="全文解读进度" max={run.progress.total} value={run.progress.completed}/>
   {run.split_synthesis_available&&<div className="report-start"><strong>正文阅读已完成，只修复报告综合</strong><p>保留已保存的 {run.tasks.filter(t=>t.kind==='reading'&&t.state==='succeeded').length} 批笔记，将综合拆成整体脉络、核心内容与创新、阅读路线与结论。预计新增3次请求，每次最多6000输出token。</p><p>本任务上限将由 {run.max_calls} 调整为 {run.synthesis_recovery.minimum_new_limit} 次；已用 {run.calls_reserved} 次保留。每段都会发送已有笔记与引用，不重读全文，不自动追加重试；次数上限不等于费用上限。</p><label className="report-consent"><input type="checkbox" checked={splitConsent} onChange={e=>setSplitConsent(e.target.checked)}/>我同意上述3段综合请求及新的任务上限</label><button disabled={busy||disabled||!splitConsent} onClick={()=>control('split_synthesis')}>保留已有研读，分段生成报告</button></div>}
   {run.budget_extension_available&&<div className="report-start"><strong>{run.report_continuation.remaining_reading?"已有研读保留，继续未完成步骤":"已有内容保留，只完成剩余报告"}</strong><p>剩余 {run.report_continuation.additional_calls} 步需要请求，上限需从 {run.max_calls} 调整到 {run.report_continuation.new_limit} 次。每次最多6000输出token，已成功的阅读和报告步骤不重复执行。{run.report_continuation.remaining_reading?`其中还有 ${run.report_continuation.remaining_reading} 批阅读。`:""}</p><label className="report-consent"><input type="checkbox" checked={splitConsent} onChange={e=>setSplitConsent(e.target.checked)}/>{run.report_continuation.remaining_reading?"我同意未完成步骤的请求及新的上限":"我同意剩余报告请求及新的上限"}</label><button disabled={busy||disabled||!splitConsent} onClick={()=>control('extend_budget')}>{run.report_continuation.remaining_reading?"保留已读内容，继续未完成步骤":"保留已有内容，继续剩余报告"}</button></div>}
   {run.error&&<p role="alert">{run.error.message}</p>}
   {run.state==='failed'&&run.error?.code==='report_quote_invalid'&&!run.candidate_available&&<p className="report-fineprint">这次失败发生在旧版程序，未保留待核验回复，无法本地恢复。修复后模型将选择程序提供的原文编号，不再重新抄写引文。</p>}
   {run.state==='failed'&&run.candidate_available&&<p className="report-fineprint">已保留待核验的模型回复，尚不是解读结果。可先本地重新校验，不调用模型；通过后保存为检查点，再由你决定是否继续。</p>}
   {run.last_failure&&<p className="report-fineprint">上次诊断：HTTP {run.last_failure.http_status??'未知'} · 结束原因 {run.last_failure.finish_reason||'未知'} · 已报告输出 {run.last_failure.usage?.completion_tokens??'未知'} token · 最终正文 {run.last_failure.content_characters??'未知'} 字符。实际计费以服务商为准。</p>}
   {run.interrupted&&<p>进程可能中断，先恢复检查点；不会自动重发结果未知的请求。</p>}
   <div className="report-controls">
    {run.state==='failed'&&!run.requires_replan&&(run.error?.code==='report_context_too_large'||run.automatic_recovery)&&<div><p>保留已读内容，按修复后的流程完成剩余分析；累计请求预算最多 {run.recommended_completion_limit||Math.min(500,run.calls_reserved+24)} 次，包含有限自动修复，失败请求也可能计费。</p><button className="dark-button" disabled={busy||disabled} onClick={finishExisting}>继续完成解读（保留已读）</button></div>}
    {run.state==='failed'&&run.candidate_available&&<button disabled={busy||disabled} onClick={()=>control('revalidate')}>本地重新校验（不调用模型）</button>}
    {run.state==='queued'&&!run.requires_replan&&<button disabled={busy||disabled} onClick={()=>control('start')}>开始已准备的解读</button>}
    {active&&!run.interrupted&&run.state==='running'&&<button disabled={busy} onClick={()=>control('pause')}>暂停全文解读</button>}
    {run.interrupted&&<button disabled={busy} onClick={()=>control('pause')}>恢复解读检查点</button>}
    {run.state==='paused'&&!run.requires_replan&&<button disabled={busy||disabled} onClick={()=>control('resume')}>继续全文解读</button>}
    {run.state==='failed'&&!run.automatic_recovery&&run.error?.code!=='report_context_too_large'&&!run.requires_replan&&!run.split_synthesis_available&&!run.budget_extension_available&&<><label><input type="checkbox" checked={retryConsent} onChange={e=>setRetryConsent(e.target.checked)}/>核查失败记录后，同意新增请求重试</label><button disabled={busy||disabled||!retryConsent||run.calls_reserved>=run.max_calls} onClick={()=>control('retry')}>重试未完成步骤</button></>}
    {!['completed','completed_with_gaps','cancelled','cancelling'].includes(run.state)&&<button disabled={busy} onClick={()=>control('cancel')}>取消后续分析</button>}
   </div>{run.automatic_recovery&&<p className="report-fineprint">自动完成已开启：长文按需整合；常见格式或引用问题每步最多自动重试一次，已完成内容不重复调用，总预算不会自动超出。{run.automatic_retries>0?` 已执行 ${run.automatic_retries} 次自动修复请求。`:''}</p>}<details><summary>查看全文阅读进度与范围</summary><p>文字覆盖 {run.coverage.text_pages.length}/{run.coverage.total_pages} 页；图表视觉内容未核验。</p><ol>{run.tasks.map(t=><li key={t.id}>{t.kind==='reading'?`阅读第${t.index+1}批 · PDF ${t.pages.join('、')}`:t.kind==='consolidation'?`长文研读整合 · 第${t.level}层`:({overview:'报告：整体脉络与摘要',core:'报告：核心内容与创新',guidance:'报告：阅读路线与结论'})[t.part]||'综合全文：生成解读与阅读路线'} — {({pending:'待处理',running:'处理中',succeeded:'已保存',failed:'失败'})[t.state]}</li>)}</ol></details>
   {run.tasks.some(t=>t.kind==='reading'&&t.state==='succeeded'&&t.output)&&<details><summary>查看已保存的研读笔记（不调用模型）</summary><p>以下是逐批阅读笔记，尚不等同于完整报告；已有内容无需重新付费生成。</p>{run.tasks.filter(t=>t.kind==='reading'&&t.state==='succeeded'&&t.output).map(t=><section key={t.id}><h4>第 {t.index+1} 批 · PDF {t.pages.join('、')}</h4><p>{t.output.overview}</p>{t.output.topics.map((topic,i)=><article key={i}><h4>{topic.heading}</h4><p className="report-prose">{topic.explanation}</p>{[['method_details','方法细节'],['findings','作者报告结果'],['author_contributions','作者声称贡献'],['limitations','限制'],['reading_focus','阅读重点']].map(([key,label])=>topic[key]?.length>0&&<div key={key}><strong>{label}</strong><ul>{topic[key].map((v,j)=><li key={j}>{v}</li>)}</ul></div>)}{refs(topic.evidence_ids)}</article>)}<ul>{t.output.unknowns.map((q,i)=><li key={i}>{q}</li>)}</ul></section>)}</details>}
  </div>}
  </details>}
  {report&&<article className="report-body">
   <section id="paper-identity" className="report-identity">
    <div className="report-kicker"><span>RESEARCH BRIEF</span><span>01 / 论文名片</span></div>
    <h3>{titleIdentity?titleIdentity.text:quotedTitle?quotedTitle[1]:report.title.text}</h3>
    {titleIdentity?<><p className="report-section-note">{titleIdentity.note}</p>{titleIdentity.sources.length>0&&<div className="report-evidence">{titleIdentity.sources.map((source,i)=><button key={source.block_id} onClick={()=>onLocate(source)} title={source.quote}>PDF {source.pdf_page} · 题名原文{titleIdentity.sources.length>1?` ${i+1}`:''}</button>)}</div>}</>:refs(report.title.evidence_ids)}
    {!titleIdentity&&quotedTitle&&<ReportDetail key={String(fullReading)} full={fullReading} summary="题名补充说明"><p className="report-prose">{quotedTitle[2]}</p></ReportDetail>}
    <div className="report-direction"><span>研究方向</span><div>{insight(report.research_direction,90)}</div></div>
    <div className="report-takeaway"><span className="report-takeaway-mark" aria-hidden="true">§</span><div><h4>研究主旨</h4>{insight(report.one_sentence,150)}</div></div>
    {run.state==='completed_with_gaps'&&<div className="report-gap-note"><p>报告存在未完成解释的原文片段；涉及这些内容的定义与结论仍需核对。</p>{gapPages.size>0?<details className="report-gap-detail"><summary>查看阅读缺口 · PDF {[...gapPages.keys()].sort((a,b)=>a-b).join('、')}</summary><ul>{[...gapPages.entries()].sort(([a],[b])=>a-b).map(([page,item])=><li key={page}><button onClick={()=>onLocate({block_id:item.span.block_id,pdf_page:page,start:item.span.start,end:item.span.end})}>PDF {page} · 回查原文</button><span>{[...item.reasons].join('、')} · {item.count} 处提取片段</span></li>)}</ul><p>文字提取覆盖不等于公式、图表已理解；缺口处的指标不应直接当作可复现定义。</p></details>:<p>具体边界见结论与局限。</p>}</div>}
   </section>
   <div className="report-reading-toolbar"><div><strong>选择阅读篇幅</strong><p>精简模式展示解读节选，完整内容可随时展开。</p></div><div className="report-view-switch" role="group" aria-label="阅读篇幅"><button aria-pressed={!fullReading} onClick={()=>setFullReading(false)}>精简阅读</button><button aria-pressed={fullReading} onClick={()=>setFullReading(true)}>完整展开</button></div></div>
   <div className="report-reading-layout">
   <nav className="report-navigation" aria-label="解读报告目录"><span className="report-nav-label">CONTENTS / 阅读目录</span>{[['paper-abstract','研究概览'],['paper-map','全文架构'],['paper-visuals','原文图表'],['paper-core','概念与方法'],['paper-innovation','创新点与边界'],['paper-route','阅读路线'],['paper-conclusion','结论与局限']].map(([id,label],i)=><a key={id} href={'#'+id}><span>{String(i+1).padStart(2,'0')}</span>{label}</a>)}<p>先理解问题与方法，<br/>再判断证据与贡献。</p></nav>
   <div className="report-chapters">
   <section id="paper-abstract"><div className="eyebrow">01 / OVERVIEW</div><h3>研究背景与摘要</h3><p className="report-section-note">背景、解法与结果的连贯解释</p>{insight(report.abstract_explanation,220)}{report.abstract_evidence_ids?.length>0&&<ReportDetail summary="查看作者摘要原文摘录"><p className="report-fineprint">以下为原文引用，保留原有措辞；上方为模型解读。</p>{report.abstract_evidence_ids.map(id=>evidence[id]&&<blockquote key={id}>{evidence[id].quote}{refs([id])}</blockquote>)}</ReportDetail>}</section>
   <section id="paper-map"><div className="eyebrow">02 / ARGUMENT</div><h3>全文结构与论证脉络</h3><p className="report-section-note">每一节承担什么作用，又如何衔接下一步</p><PaperStructure key={run.id} outline={run.source_outline||((!run.extraction_id||run.extraction_id===plan?.extraction_id)?plan?.source_outline:null)} chapters={report.argument_map} evidence={evidence} onLocate={onLocate} renderChapter={(chapter,i)=><><h4>{chapter.heading}</h4><ReadingText text={chapter.role} full={fullReading} limit={100}/><p className="report-connection"><span>论证衔接</span> {chapter.connects_to}</p><ReportDetail key={String(fullReading)+i} full={fullReading} summary="展开本节内容与依据">{insight(chapter.content)}</ReportDetail></>}/></section>
   <section id="paper-visuals" aria-label="原文图表"><div className="eyebrow">SOURCE / FIGURES & TABLES</div><h3>原文图表</h3><SourceVisualGallery documentId={documentId} data={visuals} report={report} onOpen={setOpenVisual}/></section>
   <section id="paper-core"><div className="eyebrow">03 / METHOD & EVIDENCE</div><h3>核心概念与方法</h3><p className="report-section-note">沿着设计理由、实现机制与验证依据来读</p>{report.core_content.map((item,i)=><div className="report-core-item" key={i}><span className="report-item-number">{String(i+1).padStart(2,'0')}</span><div><h4 className="report-method-title">{item.title}</h4>{insight(item.explanation,200,[item.title])}<VisualCards documentId={documentId} items={referencedVisuals(visuals.items,item.title+' '+item.explanation.text).slice(0,2)} compact onOpen={setOpenVisual}/></div></div>)}</section>
   <section id="paper-innovation"><div className="eyebrow">04 / CONTRIBUTION</div><h3>创新点与贡献边界</h3><p className="report-section-note">依据论文自身表述整理，未做外部新颖性检索。</p>{report.innovations.length?report.innovations.map((item,i)=><div className="report-contribution" key={i}><h4><span>{String(i+1).padStart(2,'0')}</span>{item.title}</h4><div className="report-contribution-explanation"><strong>贡献解释</strong>{insight(item.explanation,140,[item.title])}</div><div className="report-contribution-boundary"><strong>证据与边界</strong>{insight(item.boundary,140)}</div><ReportDetail full={fullReading||Boolean(item.baseline||item.change||item.rationale||item.support)} summary="创新点逐项对照"><div className="contribution-comparison" aria-label="创新点逐项对照"><dl>{[['baseline','原有方法或观点'],['change','具体改变'],['rationale','作用机制或论证理由'],['support','文中验证或论据']].map(([key,label])=><div key={key}><dt>{label}</dt><dd>{item[key]?insight(item[key],100):<p className="comparison-missing">{item.baseline||item.change||item.rationale||item.support?'未提供充分材料，暂不作判断。':'此报告尚未生成该对照项；保留已有解释，不自动补写。'}</p>}</dd></div>)}</dl><p className="structure-note">引文定位与语义支持分开核验；有引文不等于已证明创新。</p></div></ReportDetail><ReportDetail key={String(fullReading)+i} full={fullReading} summary="对照作者声称的贡献">{insight(item.author_claim)}</ReportDetail></div>):<p>现有材料不足以识别明确的独立创新贡献，需结合原文与相关工作核对。</p>}</section>
   <section id="paper-route"><div className="eyebrow">05 / READING PATH</div><h3>阅读重点与顺序</h3><p className="report-section-note">面向计算机技术硕士，按理解依赖安排阅读顺序</p>{report.reading_route.map((stop,i)=><div className="report-route-stop" key={i}><div className="report-route-label"><span>{String(i+1).padStart(2,'0')}</span>{({first:'先读',next:'接着读',optional:'按需补读'})[stop.priority]}</div><h4>{stop.section}</h4><ReadingText text={stop.why} full={fullReading} limit={130}/><strong>重点关注</strong><ul>{stop.questions.map((q,j)=><li key={j}>{q}</li>)}</ul>{stop.prerequisites.length>0&&<p className="report-prerequisites">相关基础：{stop.prerequisites.join('；')}</p>}{refs(stop.evidence_ids)}</div>)}</section>
   <section id="paper-conclusion"><div className="eyebrow">06 / TAKEAWAYS</div><h3>主要结论与局限</h3>{supportNotice("conclusion")}{insight(report.conclusion,180)}<div className="report-limitations"><h4>适用边界</h4>{supportNotice("limitations")}{insight(report.limitations,180)}</div><ReportDetail key={String(fullReading)} full={fullReading} summary="待讨论问题"><DiscussionQuestions questions={report.open_questions} review={run.support_review} onLocate={onLocate}/></ReportDetail>{report.reading_unknowns?.length>0&&<ReportDetail summary="各批阅读保留的未知项（未经综合删减）"><ul>{report.reading_unknowns.map((q,i)=><li key={i}>{q}</li>)}</ul></ReportDetail>}<ReportSupport review={run.support_review} onLocate={onLocate}/><p className="report-boundary">模型解读 · 引用已定位原文，语义未独立核验。图像、表格结构、公式推导与实验复现尚未验证。{run.state==='completed_with_gaps'?'本报告存在未读/提取缺口。':''}</p></section>
   </div></div>
  </article>}
  <VisualViewer key={documentId} documentId={documentId} item={openVisual} onClose={()=>setOpenVisual(null)} onLocate={onLocate}/>
  {run&&<footer className="report-export"><a href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(run,null,2))}`} download={`whole-paper-${run.id}.json`}>导出解读与来源记录</a>{runs.length>1&&<label>历史解读<select aria-label="历史全文解读" disabled={active||busy} value={run.id} onChange={e=>setRun(runs.find(r=>r.id===e.target.value))}>{runs.map(r=><option key={r.id} value={r.id}>{r.created_at} · {r.id.slice(-6)}</option>)}</select></label>}</footer>}
 </section>;
}
