import React,{useState} from 'react';

export default function PaperStructure({outline,chapters,evidence,onLocate,renderChapter}){
 const [view,setView]=useState('both'),[selected,setSelected]=useState('');
 const sections=outline?.sections||[];
 function links(chapter){
  if(chapter.source_section_ids?.length)return {ids:chapter.source_section_ids,kind:'模型对应 · 语义待核验'};
  const ids=[];
  for(const id of chapter.content.evidence_ids){
   const e=evidence[id];if(!e)continue;
   const order=outline?.block_ids?.indexOf(e.block_id);if(order==null||order<0)continue;
   const inside=(a,b)=>a[0]<b[0]||(a[0]===b[0]&&a[1]<=b[1]);
   const pos=[order,e.start];
   const section=sections.find(s=>inside(s.start_position,pos)&&!inside(s.end_position,pos));
   if(section&&!ids.includes(section.id))ids.push(section.id);
  }
  return {ids,kind:'按引文位置关联 · 非语义确认'};
 }
 return <div className="paper-structure">
  <div className="structure-switch" role="group" aria-label="全文结构视图">{[['both','并列查看'],['outline','原文目录'],['argument','论证脉络']].map(([id,label])=><button key={id} aria-pressed={view===id} onClick={()=>setView(id)}>{label}</button>)}</div>
  <div className={'structure-panels view-'+view}>
   {view!=='argument'&&<section className="structure-outline" aria-label="原文章节目录"><h4>原文章节</h4><p className="structure-note">标题取自原文；层级与顺序为自动候选，可能有遗漏。</p>{sections.length?<ol>{sections.map(s=><li key={s.id} className={'outline-level-'+s.level}><button className="structure-heading" aria-pressed={selected===s.id} onClick={()=>setSelected(selected===s.id?'':s.id)}>{s.title}</button><button className="structure-locate" onClick={()=>onLocate(s.anchor)}>PDF {s.pdf_page} · 查看原文</button></li>)}</ol>:<p className="structure-empty">尚未识别到可靠章节候选。可在原文中核对；右侧为系统归纳的论证脉络，不替代原文目录。</p>}</section>}
   {view!=='outline'&&<section className="structure-argument" aria-label="论文论证脉络"><h4>论证脉络</h4><p className="structure-note">以下节点由系统归纳，说明各部分如何推进论证。</p>{selected&&<div className="structure-selection">已选章节：{sections.find(s=>s.id===selected)?.title}<button onClick={()=>setSelected('')}>清除选择</button></div>}<ol className="report-map">{chapters.map((chapter,i)=>{const mapping=links(chapter);return <li key={i} className={selected&&mapping.ids.includes(selected)?'structure-matched':''}><span>{String(i+1).padStart(2,'0')}</span><div>{renderChapter(chapter,i)}<div className="structure-links">{mapping.ids.length?<><small>{mapping.kind}</small>{mapping.ids.map(id=>{const s=sections.find(v=>v.id===id);return s&&<button key={id} aria-pressed={selected===id} onClick={()=>{setSelected(selected===id?'':id);if(view==='argument')setView('both');}}>{s.title} · P{s.pdf_page}</button>;})}</>:<small>章节对应尚未确定；可通过本节点引文回查。</small>}</div></div></li>;})}</ol></section>}
  </div>
 </div>;
}
