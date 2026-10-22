import React from 'react';

const labels={model_supported:'模型自查：有支持',model_partial:'模型自查：部分支持',needs_check:'仍需核对',withdrawn:'暂不采纳'};
function Status({entry}){return entry.status==='not_reviewed'?null:<span className="report-support-status">{labels[entry.status]}</span>;}
function Reason({entry}){return entry.status==='not_reviewed'?null:<p className="report-support-reason">{entry.reason}</p>;}
function Sources({entry,onLocate}){
 const locations=entry.locations||[],candidates=entry.candidate_sources||[];
 return <>{locations.length>0&&<div className="report-evidence">{locations.map((s,i)=><button key={i} title={s.quote} onClick={()=>onLocate(s)}>PDF {s.pdf_page} · 核查原文 {i+1}</button>)}</div>}
 {candidates.length>0&&<details className="report-support-candidates"><summary>相关原文线索（关键词匹配）</summary><p>匹配到相关文字，不代表已经支持或推翻这个判断。</p>{candidates.map(s=><blockquote key={s.id}>{s.quote}<div className="report-evidence"><button onClick={()=>onLocate(s)}>PDF {s.pdf_page} · 查看线索</button></div></blockquote>)}</details>}</>;
}

export function DiscussionQuestions({questions,review,onLocate}){
 if(!review)return <ul>{questions.map((q,i)=><li key={i}>{q}</li>)}</ul>;
 const entries=review.entries.filter(e=>e.target.startsWith('open_questions.'));
 const ready=entries.filter(e=>e.status==='not_reviewed'||(['model_supported','model_partial'].includes(e.status)&&e.kind==='reading_question'));
 const pending=entries.filter(e=>!ready.includes(e));
 return <div className="report-discussion">
 {ready.length>0&&<ul>{ready.map(e=><li key={e.target}>{e.text}<Sources entry={e} onLocate={onLocate}/></li>)}</ul>}
 {!ready.length&&<p>{pending.length?'需核对的疑问及未采纳记录保留在下方。':'当前没有新增讨论问题。'}</p>}
 {pending.length>0&&<details className="report-pending-questions"><summary>待核对或暂不采纳的疑问 · {pending.length} 项</summary><p>以下保留生成记录，尚不能据此认定论文存在缺陷。</p>{pending.map(e=><article key={e.target}><Status entry={e}/><p>{e.text}</p><Reason entry={e}/><Sources entry={e} onLocate={onLocate}/></article>)}</details>}
 </div>;
}

export default function ReportSupport({review,onLocate}){
 if(!review)return null;
 const hasChecks=review.entries.some(e=>e.status!=='not_reviewed');
 return <details className="report-support"><summary>{hasChecks?'关键结论与疑问的支持核查':'原文回查与人工复核'}</summary>
 {hasChecks?<><p className="report-support-scope">仅覆盖主要结论、局限与待讨论问题。同次生成的模型自查，尚无独立审阅；不代表整份报告已经核验。</p>
 <p>自查记录覆盖 {review.recorded_targets??review.entries.filter(e=>e.status!=='not_reviewed').length} / {review.total_targets??review.entries.length} 项。</p>
 <p>{review.notice}</p></>:<p className="report-support-scope">可结合以下内容与原文自行复核。</p>}
 {review.entries.map(e=><article key={e.target}><header><strong>{e.target==='conclusion'?'主要结论':e.target==='limitations'?'适用边界':`讨论问题 ${Number(e.target.split('.')[1])+1}`}</strong><Status entry={e}/></header><p>{e.text}</p><Reason entry={e}/>{e.absence_or_conflict_language&&<p className="report-support-caution">涉及缺失或冲突的判断，原文片段中未出现不能证明全文没有。</p>}<Sources entry={e} onLocate={onLocate}/></article>)}
 </details>;
}
