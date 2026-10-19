import React, {useEffect, useState} from 'react';
import './facts.css';
import SupportReview,{SupportStatus} from './SupportReview';
import SectionWorkflow from './SectionWorkflow';

const categories={problem:'研究问题',concept:'概念与定义',method:'方法步骤',derivation:'推导与公式',experiment:'实验设计',result:'作者报告结果',limitation:'限定与边界'};
const dispositions={analysed:'已记录',non_substantive:'无需事实条目',blocked:'待补证据',pending:'待研读'};
const emptyDraft=()=>({disposition:'analysed',explanation:'',facts:[],unknowns:[],unresolved_dependencies:[]});

export default function FactWorksheet({packet,onLocate,onDirtyChange}){
 const [ledger,setLedger]=useState(null),[uid,setUid]=useState(''),[draft,setDraft]=useState(emptyDraft),[error,setError]=useState(''),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const [reviewIndex,setReviewIndex]=useState(null),[reviewDirty,setReviewDirty]=useState(false);
 const [workflowActive,setWorkflowActive]=useState(false);
 const path=`/api/v2/documents/${packet.document_version_id}/reading/sections/${packet.section.id}/facts`;
 async function api(suffix='',options={}){const r=await fetch(path+suffix,options),data=await r.json();if(!r.ok)throw Error(data.message||'底稿请求失败');return data;}
 useEffect(()=>{const c=new AbortController();api('?reading_revision='+packet.reading_revision,{signal:c.signal}).then(d=>{setLedger(d);setUid(d.units[0]?.id||'');}).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return ()=>c.abort();},[packet.document_version_id,packet.section.id,packet.reading_revision]);
 const unit=ledger?.units.find(u=>u.id===uid);
 useEffect(()=>{setDraft(unit?.draft?structuredClone(unit.draft):emptyDraft());},[uid,ledger?.revision]);
 useEffect(()=>{setMessage('');setReviewIndex(null);},[uid]);
 const dirty=!!unit&&JSON.stringify(draft)!==JSON.stringify(unit.draft||emptyDraft());
 useEffect(()=>{onDirtyChange(dirty||reviewDirty);return ()=>onDirtyChange(false);},[dirty,reviewDirty,onDirtyChange]);
 const editorBusy=busy||workflowActive;
 const locked=editorBusy||dirty||reviewDirty;
 async function saveReview(command){const result=await api('/support-review',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()},body:JSON.stringify(command)});setLedger(result);setReviewIndex(null);setMessage('证据支持复核已记录；这不是独立语义验证。');}
 function changeDraft(patch){setDraft(prev=>({...prev,...patch}));setMessage('');}
 function editFact(i,patch){changeDraft({facts:draft.facts.map((f,j)=>i===j?{...f,...patch}:f)});}
 function sourceSpans(){return unit.source_spans||[{block_id:unit.block_id,start:unit.start,end:unit.end,text:unit.text}];}
 function isTargetCitation(c){return sourceSpans().some(s=>c.block_id===s.block_id&&c.start>=s.start&&c.end<=s.end);}
 function addFact(){const s=sourceSpans()[0];changeDraft({facts:[...draft.facts,{category:'method',basis:'author_statement',statement:'',reasoning:'',conditions:[],citations:[{block_id:s.block_id,start:s.start,end:s.end,quote:s.text}]}]});}
 function setQuote(i,value){const citations=[...draft.facts[i].citations],index=citations.findIndex(isTargetCitation),s=sourceSpans().find(s=>s.text.includes(value))||sourceSpans()[0];const utf16Index=s.text.indexOf(value),relative=utf16Index<0?-1:Array.from(s.text.slice(0,utf16Index)).length;
  citations[index<0?0:index]={block_id:s.block_id,start:relative<0?s.start:s.start+relative,end:(relative<0?s.start:s.start+relative)+Array.from(value).length,quote:value};editFact(i,{citations});}
 async function mutate(generate=false){setBusy(true);setError('');setMessage('');
  try{const command={reading_revision:packet.reading_revision,revision:ledger.revision,unit_id:uid};if(!generate)command.draft=draft;
   const result=await api(generate?'/generate':'',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()},body:JSON.stringify(command)});setLedger(result);setMessage(generate?'本段模型底稿已保存；有引文的条目已检查定位，语义仍待核验。':'本段记录已保存；有引文的条目已检查定位，语义仍待核验。');
  }catch(e){setError(e.message);}finally{setBusy(false);}
 }
 async function reload(){setBusy(true);setError('');try{setLedger(await api('?reading_revision='+packet.reading_revision));}catch(e){setError(e.message);}finally{setBusy(false);}}
 if(!ledger)return <section className="fact-sheet" aria-label="逐段事实底稿"><h3>逐段事实底稿</h3><p role={error?'alert':undefined}>{error||'正在按原文范围建立待研读清单…'}</p></section>;
 const c=ledger.coverage;
 return <section className="fact-sheet" aria-label="逐段事实底稿">
  <header className="fact-header"><div><div className="eyebrow">PARAGRAPH FACT WORKSHEET</div><h3>逐段读清，逐条留证。</h3><p>作者陈述、阅读推断与未知分开记录。原文单元按空行或 PDF 文本块划分，段落边界仍需核查。</p></div><span>底稿版本 {ledger.revision}<small>阅读结构版本 {ledger.reading_revision}</small></span></header>
  <div className="fact-counts"><span><b>{c.analysed}</b>已记录</span><span><b>{c.pending}</b>待研读</span><span><b>{c.blocked}</b>待补证据</span><span><b>{c.non_substantive}</b>无需事实条目</span></div>
  <p className="fact-scope">{ledger.model.configured?`可使用 ${ledger.model.model_name} 生成单段底稿；将发送本段及相邻文本和材料位置。`:'尚未配置模型，可先手动记录；当前不会发送论文到模型服务。'} 图表语义与事实正确性尚未核验。</p>
  {error&&<p className="reading-error" role="alert">{error}</p>}{message&&<p className="fact-notice" role="status">{message}</p>}
  <SectionWorkflow packet={packet} ledger={ledger} disabled={busy||dirty||reviewDirty} onRefresh={reload} onActiveChange={setWorkflowActive} onLocate={onLocate}/>
  <div className="fact-layout"><nav className="fact-units" aria-label="原文单元清单">{ledger.units.map((u,i)=><button key={u.id} disabled={editorBusy||((dirty||reviewDirty)&&u.id!==uid)} className={(u.id===uid?'selected ':'')+(u.draft?.disposition||'pending')} onClick={()=>{setUid(u.id);setError('');}}><span>{String(i+1).padStart(2,'0')} · PDF {u.region.pdf_page}<small>{dispositions[u.draft?.disposition||'pending']}</small></span><p>{u.text.slice(0,100)}{u.text.length>100?'…':''}</p></button>)}</nav>
  {unit&&<div className="fact-editor"><div className="fact-source"><div><strong>本段原文 · PDF {unit.region.pdf_page}</strong><button onClick={()=>onLocate(packet.blocks.find(b=>b.id===unit.block_id))}>回到原文位置</button></div><p>{unit.text}</p><small>{sourceSpans().length>1?`由 ${sourceSpans().length} 个原文块组合，各块独立引用；位置按钮定位首块`:`字符范围 ${unit.start}–${unit.end} · 区域定位精度为整个文本块`}</small></div>
   <div className="fact-editor-tools"><label>记录状态<select aria-label="本段记录状态" disabled={editorBusy||reviewDirty} value={draft.disposition} onChange={e=>changeDraft({disposition:e.target.value})}><option value="analysed">实质内容，建立事实</option><option value="non_substantive">标题 / 题录等，无需事实条目</option><option value="blocked">缺少证据，暂无法读清</option></select></label><button disabled={locked||!!unit.draft||!ledger.model.configured} onClick={()=>mutate(true)}>{busy?'正在处理…':'生成本段事实底稿'}</button></div>
   <label className="fact-field">本段解释 / 论证作用 / 处理理由<textarea aria-label="本段解释" value={draft.explanation} disabled={editorBusy||reviewDirty} maxLength={6000} onChange={e=>changeDraft({explanation:e.target.value})} placeholder="解释这一段说了什么、为何出现在这里、与前文怎样衔接；无法读清时说明原因。"/></label>
   <div className="fact-subheading"><h4>事实条目</h4><button disabled={editorBusy||reviewDirty||draft.disposition==='non_substantive'||draft.facts.length>=30} onClick={addFact}>添加事实条目</button></div>
   {draft.facts.map((f,i)=>{const primary=f.citations.find(isTargetCitation)||f.citations[0];return <article className="fact-entry" key={i}>
    <div className="fact-entry-controls"><select aria-label={`事实 ${i+1} 类型`} disabled={editorBusy||reviewDirty} value={f.category} onChange={e=>editFact(i,{category:e.target.value})}>{Object.entries(categories).map(([key,name])=><option key={key} value={key}>{name}</option>)}</select><select aria-label={`事实 ${i+1} 依据类型`} disabled={editorBusy||reviewDirty} value={f.basis} onChange={e=>editFact(i,{basis:e.target.value})}><option value="author_statement">作者陈述（未独立证实）</option><option value="reader_inference">阅读推断（需解释推理）</option></select><button aria-label={`删除事实 ${i+1}`} disabled={editorBusy||reviewDirty} onClick={()=>changeDraft({facts:draft.facts.filter((_,j)=>i!==j)})}>删除</button></div>
    <label className="fact-field">单条陈述<textarea aria-label={`事实 ${i+1} 陈述`} disabled={editorBusy||reviewDirty} value={f.statement} maxLength={3000} onChange={e=>editFact(i,{statement:e.target.value})}/></label>
    <label className="fact-field">推理说明{f.basis==='reader_inference'?'（必填）':' / 补充解释'}<textarea aria-label={`事实 ${i+1} 推理`} disabled={editorBusy||reviewDirty} value={f.reasoning} maxLength={4000} onChange={e=>editFact(i,{reasoning:e.target.value})}/></label>
    <label className="fact-field">适用条件与限制（每行一项）<textarea aria-label={`事实 ${i+1} 限定条件`} disabled={editorBusy||reviewDirty} value={f.conditions.join('\n')} onChange={e=>editFact(i,{conditions:e.target.value.split('\n').filter(Boolean)})}/></label>
    <label className="fact-field">本段逐字引文<textarea className="fact-quote" aria-label={`事实 ${i+1} 原文引文`} disabled={editorBusy||reviewDirty} value={primary?.quote||''} maxLength={12000} onChange={e=>setQuote(i,e.target.value)}/></label>
    <small className="fact-citation-note">保存时核对文字与位置；引文存在不表示陈述一定成立。{f.citations.length>1?`另保留 ${f.citations.length-1} 条上下文引文，详见导出。`:''}</small>
    <p className="support-status">证据支持：{dirty?'底稿有未保存修改，保存后需重新复核':<SupportStatus check={unit.support_checks?.[i]}/>}</p><button disabled={locked||!unit.draft} onClick={()=>setReviewIndex(i)}>核对事实 {i+1} 的证据支持</button>
   </article>;})}
   {reviewIndex!==null&&unit.draft?.facts[reviewIndex]&&<SupportReview key={`${uid}-${reviewIndex}-${ledger.revision}`} fact={unit.draft.facts[reviewIndex]} index={reviewIndex} unit={unit} revision={ledger.revision} readingRevision={packet.reading_revision} check={unit.support_checks?.[reviewIndex]} disabled={editorBusy||dirty} onSave={saveReview} onDirtyChange={setReviewDirty} onClose={()=>setReviewIndex(null)}/>}
   <div className="fact-subheading"><h4>未知与缺失证据</h4><button disabled={editorBusy||reviewDirty||draft.unknowns.length>=20} onClick={()=>changeDraft({unknowns:[...draft.unknowns,{question:'',why_unresolved:'',needed_evidence:''}]})}>记录未知项</button></div>
   {draft.unknowns.map((u,i)=><article className="fact-unknown" key={i}>{[['question','未知问题'],['why_unresolved','为何目前不能判断'],['needed_evidence','需要补充什么证据']].map(([key,label])=><label key={key} className="fact-field">{label}<textarea aria-label={`未知 ${i+1} ${label}`} disabled={editorBusy||reviewDirty} value={u[key]} maxLength={2000} onChange={e=>changeDraft({unknowns:draft.unknowns.map((x,j)=>i===j?{...x,[key]:e.target.value}:x)})}/></label>)}<button disabled={editorBusy||reviewDirty} onClick={()=>changeDraft({unknowns:draft.unknowns.filter((_,j)=>i!==j)})}>删除未知项</button></article>)}
   <label className="fact-field">尚未核对的跨节依赖（每行一项）<textarea aria-label="跨节依赖" disabled={editorBusy||reviewDirty} value={draft.unresolved_dependencies.join('\n')} onChange={e=>changeDraft({unresolved_dependencies:e.target.value.split('\n').filter(Boolean)})} placeholder="例如：本段依赖附录中的超参数定义，尚未核对。"/></label>
   {unit.producer&&<p className="fact-provenance">来源：{unit.producer.kind==='model'?`模型 ${unit.producer.model_name}`:'本地手动记录（未认证身份）'} · {unit.citation_check==='no_fact_citations'?'本段无事实引文':'引文已定位'} · 语义未核验</p>}
   {dirty&&<p className="reading-hint">有未保存修改，请保存或撤销后切换段落。</p>}
   <div className="fact-save"><button className="dark-button" disabled={editorBusy||reviewDirty||!dirty} onClick={()=>mutate(false)}>保存本段底稿</button><button disabled={editorBusy||!dirty} onClick={()=>setDraft(unit.draft?structuredClone(unit.draft):emptyDraft())}>撤销本段修改</button><button disabled={locked} onClick={reload}>刷新底稿</button><button disabled={locked||!ledger.units.some(u=>!u.draft&&u.id!==uid)} onClick={()=>{setUid(ledger.units.find(u=>!u.draft&&u.id!==uid).id);setError('');}}>下一待研读段</button></div>
  </div>}</div>
  <footer className="fact-footer"><span>{c.text_recording_complete?'本节文本单元均已有处理记录；不代表语义正确或整节深读完成。':`${c.total} 个原文单元均在清单中；待读和阻塞项不会算成完成。`}</span><a href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(ledger,null,2))}`} download={`paragraph-facts-r${ledger.reading_revision}-v${ledger.revision}.json`}>导出带来源的事实底稿</a></footer>
 </section>;
}
