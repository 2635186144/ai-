import React, {useEffect, useState} from 'react';
import './reading.css';
import FactWorksheet from './FactWorksheet';

const kinds={figure_caption:'图注',table_caption:'表注',image:'图像',appendix_heading:'附录标题',equation_candidate:'公式'};
const statusNames={pending:'待核对',confirmed:'已核对',indexed:'位置已核对',excluded:'已排除',needs_source:'待补材料'};

export default function ReadingPreparation({documentId, extractionId, page, onPage, onBlock, onMaterial,onUnsavedChange}){
 const [plan,setPlan]=useState(null),[sections,setSections]=useState([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [note,setNote]=useState(''),[packet,setPacket]=useState(null),[sectionId,setSectionId]=useState('');
 const [newAnchor,setNewAnchor]=useState(''),[newTitle,setNewTitle]=useState(''),[newOffset,setNewOffset]=useState(0);
 const [factDirty,setFactDirty]=useState(false);
 const [correctionsOpen,setCorrectionsOpen]=useState(false);
 const path=`/api/v2/documents/${documentId}/reading`;
 async function api(suffix='',options={}){const r=await fetch(path+suffix,options),d=await r.json();if(!r.ok)throw Error(d.message||'读取失败');return d;}
 useEffect(()=>{const c=new AbortController();setPlan(null);setPacket(null);setError('');setNote('');api('',{signal:c.signal}).then(setPlan).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return ()=>c.abort();},[documentId,extractionId]);
 useEffect(()=>{if(plan){setSections(plan.sections);setSectionId(prev=>plan.section_index.some(s=>s.id===prev)?prev:plan.section_index[0]?.id||'');}},[plan]);
 useEffect(()=>{setNote('');setNewAnchor('');setNewOffset(0);},[page]);
 const canonical=items=>JSON.stringify(items.map(s=>({...s,start_offset:s.start_offset||0})));
 const dirty=!!plan&&canonical(sections)!==canonical(plan.sections);
 useEffect(()=>{onUnsavedChange(!!dirty||factDirty);return ()=>onUnsavedChange(false);},[dirty,factDirty,onUnsavedChange]);
 async function update(action,values={}){
  if(factDirty){setError('事实底稿有未保存修改，请先保存或撤销，再修改阅读结构。');return;}
  setBusy(true);setError('');setPacket(null);
  try{setPlan(await api('',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()},body:JSON.stringify({revision:plan.revision,action,...values})}));}
  catch(e){setError(e.message);if(e.message.includes('刷新')){setPlan(await api());}}
  finally{setBusy(false);}
 }
 if(!plan)return <section className="reading-prep"><p role={error?'alert':undefined}>{error||'正在建立章节、顺序与覆盖清单…'}</p></section>;
 const p=plan.pages.find(p=>p.page===page),info=plan.page_info.find(p=>p.pdf_page===page);
 const byId=Object.fromEntries(plan.block_previews.map(b=>[b.id,b]));
 const pageMaterials=plan.material_index.filter(m=>m.pdf_page===page);
 const coveredPages=plan.pages.filter(p=>['confirmed','excluded'].includes(p.status)).length;
 const coveredMaterials=plan.materials.filter(m=>['indexed','excluded'].includes(m.status)).length;
 const locked=busy||dirty||factDirty;
 function move(index,direction){const ids=[...p.block_ids];[ids[index],ids[index+direction]]=[ids[index+direction],ids[index]];update('set_order',{page,block_ids:ids});}
 function spatialOrder(mode){
  const ids=[...p.block_ids],mid=info.width/2;
  ids.sort((a,b)=>{const A=byId[a].bbox,B=byId[b].bbox;return mode==='columns'?((A[0]>=mid?1:0)-(B[0]>=mid?1:0)||A[1]-B[1]||A[0]-B[0]):A[1]-B[1]||A[0]-B[0];});
  update('set_order',{page,block_ids:ids});
 }
 function addSection(e){e.preventDefault();if(!newAnchor||!newTitle.trim())return;
  const ordered=plan.pages.flatMap(p=>p.block_ids),next=[...sections.filter(s=>s.start_block_id!==newAnchor||(s.start_offset||0)!==newOffset),{start_block_id:newAnchor,start_offset:newOffset,title:newTitle.trim(),level:1}];
  next.sort((a,b)=>ordered.indexOf(a.start_block_id)-ordered.indexOf(b.start_block_id)||(a.start_offset||0)-(b.start_offset||0));setSections(next);setNewTitle('');
 }
 async function openPacket(){setBusy(true);setError('');try{setPacket(await api(`/sections/${sectionId}/packet?revision=${plan.revision}&preview=true`));}catch(e){setError(e.message);}finally{setBusy(false);}}
 async function enterReading(){
  setBusy(true);setError('');setPacket(null);let next=plan;
  try{
   async function post(action,values={}){next=await api('',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()},body:JSON.stringify({revision:next.revision,action,...values})});setPlan(next);}
   if(dirty)await post('set_sections',{sections});
   await post('use_extracted');
   const id=next.section_index.some(s=>s.id===sectionId)?sectionId:next.section_index[0]?.id;
   if(id){setSectionId(id);setPacket(await api(`/sections/${id}/packet?revision=${next.revision}`));}
  }catch(e){setError(e.message);}finally{setBusy(false);}
 }
 return <section className="reading-prep" aria-label="章节与覆盖准备">
  <header className="reading-heading"><div><div className="eyebrow">READING PREPARATION</div><h2>选择章节，开始阅读</h2><p>系统已把可提取的全文文字按章节整理。你可以先打开原文，再进入逐段深读；发现标题、双栏顺序或图表遗漏时，再展开下方校正。</p></div><span className={'reading-state '+(plan.readiness.ready?'ready':'')}>{plan.readiness.ready?'结构已核对':plan.readiness.can_read?'按自动解析结果阅读':'自动解析 · 尚未人工核对'} · 版本 {plan.revision}</span></header>
  <div className="reading-purpose"><strong>这一页有什么用？</strong><p>它是论文的目录和解析检查入口。章节决定每次深读的范围；原文顺序帮助避免双栏串读；图表清单用来保留尚未读懂或提取不到的内容。它本身不会分析论文，也不会调用模型。</p><p>整篇文件已经保留，但“有文字”不代表图表、公式已被完整理解。逐页核对是可选的质量检查，不必先手工确认所有页面才能开始。</p></div>
  <div className="reading-progress"><span><b>{plan.sections_confirmed?'已确认':'待确认'}</b>章节边界</span><span><b>{coveredPages} / {plan.pages.length}</b>页面与顺序</span><span><b>{coveredMaterials} / {plan.materials.length}</b>材料位置</span><span><b>{plan.readiness.excluded_pages.length}</b>排除页，保留原因</span></div>
  {error&&<p className="reading-error" role="alert">{error}</p>}
  {dirty&&<p className="reading-hint">章节有未保存修改，请先保存或撤销，再确认页面和材料。</p>}
  <section className="reading-next reading-start"><div><div className="eyebrow">READ / START HERE</div><h3>先看原文，再逐段读清</h3><p>{plan.readiness.ready?'已核对结构，可直接建立底稿。':plan.readiness.can_read?'已选择按当前解析结果深读；未核对项仍会传入阅读任务。':'原文预览随时可打开。进入深读时可采用当前解析结果，不会把未核对的页面记成已确认。'}</p>{dirty&&<p className="reading-hint">章节有修改：预览使用已保存的目录；进入深读会先保存你的修改。</p>}</div><div className="reading-packet-controls"><label>想先读哪一节？<select aria-label="分节材料包章节" disabled={busy||factDirty} value={sectionId} onChange={e=>{setSectionId(e.target.value);setPacket(null);}}>{plan.section_index.map(s=><option key={s.id} value={s.id}>{s.title} · P{s.start_page}–{s.end_page}</option>)}</select></label><button className="dark-button" disabled={busy||factDirty||!sectionId} onClick={openPacket}>打开分节原文包</button>{!plan.readiness.can_read&&<button disabled={busy||factDirty||!sectionId||sections.some(s=>!s.title.trim())} onClick={enterReading}>{dirty?'保存章节并采用当前解析结果，进入深读':'采用当前解析结果，进入深读'}</button>}</div></section>
  <details className="reading-corrections" open={correctionsOpen} onToggle={e=>setCorrectionsOpen(e.currentTarget.open)}><summary>校正章节、阅读顺序和图表（可选）</summary><p className="reading-hint">只有发现解析有误时才需要调整。修改后会建立新版本，旧底稿保留在历史中。这里的确认只记录结构，不表示已经读懂论文。</p>
  <div className="reading-grid">
   <section className="reading-chapters"><h3>01 / 章节边界</h3><p className="reading-hint">标题为自动候选。可从同一文本块的不同字符位置建立章节；从下方选择标题行可自动填起点。字符按Unicode计数，整块原文和坐标保留。</p>
    <div className="reading-section-list">{sections.map((s,i)=><div className="reading-section-row" key={`${s.start_block_id}-${i}`}>
     <button className="reading-anchor" onClick={()=>onBlock({...byId[s.start_block_id],region:{pdf_page:byId[s.start_block_id].page,bbox:byId[s.start_block_id].bbox}})} title="定位章节起点">P{byId[s.start_block_id]?.page}</button>
     <input aria-label={`章节 ${i+1} 名称`} value={s.title} maxLength={300} disabled={busy||factDirty} onChange={e=>setSections(sections.map((x,j)=>i===j?{...x,title:e.target.value}:x))}/>
     <select aria-label={`章节 ${i+1} 层级`} value={s.level} disabled={busy||factDirty} onChange={e=>setSections(sections.map((x,j)=>i===j?{...x,level:Number(e.target.value)}:x))}><option value={1}>一级</option><option value={2}>二级</option><option value={3}>三级</option></select>
     <button aria-label={`删除章节 ${i+1}`} disabled={busy||factDirty||i===0} onClick={()=>setSections(sections.filter((_,j)=>i!==j))}>×</button>
     <label className="reading-offset">块内起点<input aria-label={`章节 ${i+1} 块内起点`} type="number" step="1" min="0" max={byId[s.start_block_id]?.text_length-1} disabled={busy||factDirty||i===0} value={s.start_offset||0} onChange={e=>setSections(sections.map((x,j)=>i===j?{...x,start_offset:Number(e.target.value)}:x))}/></label>
    </div>)}</div>
    <form className="reading-add-section" onSubmit={addSection}><select aria-label="新增章节的起始文本块" value={newAnchor} onChange={e=>{setNewAnchor(e.target.value);setNewOffset(0);}}><option value="">选择当前页章节起点</option>{p.block_ids.map((id,i)=><option key={id} value={id}>{i+1}. {byId[id].text.slice(0,65)}</option>)}</select>{newAnchor&&<><select aria-label="选择块内标题行" value={String(newOffset)} onChange={e=>{const offset=Number(e.target.value);setNewOffset(offset);const line=byId[newAnchor].line_starts?.find(l=>l.offset===offset);if(line)setNewTitle(line.text);}}><option value="0">从字符0开始</option>{byId[newAnchor].line_starts?.filter(l=>l.offset>0).map(l=><option key={l.offset} value={l.offset}>字符{l.offset} · {l.text}</option>)}</select><label className="reading-offset">或指定字符起点<input aria-label="新增章节块内起点" type="number" min="0" step="1" max={byId[newAnchor].text_length-1} value={newOffset} onChange={e=>setNewOffset(Number(e.target.value))}/></label></>}<input aria-label="新增章节名称" placeholder="章节名称" value={newTitle} maxLength={300} onChange={e=>setNewTitle(e.target.value)}/><button disabled={busy||factDirty||!newAnchor||!newTitle.trim()}>补充章节</button></form>
    <div className="reading-actions"><button disabled={busy||!dirty||sections.some(s=>!s.title.trim())} onClick={()=>update('set_sections',{sections})}>保存章节修改</button><button disabled={busy||!dirty} onClick={()=>setSections(plan.sections)}>撤销修改</button><button disabled={locked||plan.sections_confirmed} onClick={()=>update('confirm_sections')}>{plan.sections_confirmed?'章节已确认':'确认整篇章节边界'}</button><button disabled={locked} onClick={()=>update('refresh_sections')}>重新识别候选并撤销章节确认</button></div>
   </section>
   <section><h3>02 / 逐页阅读顺序与覆盖</h3><div className="reading-pages">{plan.pages.map(x=><button key={x.page} className={(x.page===page?'selected ':'')+x.status} onClick={()=>onPage(x.page)} title={`PDF ${x.page} · ${statusNames[x.status]}${x.note?' · '+x.note:''}`} aria-label={`核对 PDF 第 ${x.page} 页`}>{x.page}<small>{statusNames[x.status]}</small></button>)}</div>
    <div className="reading-page-title"><strong>PDF {page} · {statusNames[p.status]}</strong><span>{p.block_ids.length} 文本块 · {pageMaterials.length} 材料候选</span></div>
    <p className="reading-hint">请对照上方整页预览，检查页眉页脚、跨栏段落和未识别材料。双栏建议只生成候选顺序，跨栏标题仍须手动调整。</p>
    {info.warnings.length>0&&<p className="reading-hint">本页有解析提示，请打开上方「解析边界与本页提示」核查。</p>}
    <div className="reading-actions"><button disabled={locked} onClick={()=>spatialOrder('rows')}>按纵向位置建议排序</button><button disabled={locked} onClick={()=>spatialOrder('columns')}>按左栏→右栏建议排序</button></div>
    <ol className="reading-order">{p.block_ids.map((id,i)=><li key={id}><button className="reading-block-link" onClick={()=>onBlock({...byId[id],region:{pdf_page:page,bbox:byId[id].bbox}})}>{byId[id].text}</button><div><button disabled={locked||i===0} aria-label={`上移阅读块 ${i+1}`} onClick={()=>move(i,-1)}>↑</button><button disabled={locked||i===p.block_ids.length-1} aria-label={`下移阅读块 ${i+1}`} onClick={()=>move(i,1)}>↓</button></div></li>)}</ol>
    {!p.block_ids.length&&<p className="reading-error">无可提取文字，需补充可读材料或说明排除原因。</p>}
    <label className="reading-note">本页备注 / 缺失或排除原因<textarea aria-label="覆盖确认备注" value={note} maxLength={2000} onChange={e=>setNote(e.target.value)} placeholder="例如：图 3 分辨率不足，需补充高清图；或说明本页排除范围与理由。"/></label>
    <div className="reading-actions"><button disabled={locked||info.text_status!=='text_available'} onClick={()=>update('confirm_page',{page,status:'confirmed',note})}>确认本页顺序与材料清点</button><button disabled={locked||!note.trim()} onClick={()=>update('confirm_page',{page,status:'needs_source',note})}>标记待补材料</button><button disabled={locked||!note.trim()} onClick={()=>update('confirm_page',{page,status:'excluded',note})}>说明后排除此页</button><button disabled={locked||p.status==='pending'} onClick={()=>update('confirm_page',{page,status:'pending'})}>撤销本页确认</button></div>
    {p.note&&<p className="reading-hint">已记录：{p.note}</p>}
    <div className="reading-materials">{pageMaterials.map(m=>{const state=plan.materials.find(x=>x.id===m.id);return <article key={m.id}><button className="reading-material-link" onClick={()=>onMaterial(m)}><small>{kinds[m.kind]} · {statusNames[state.status]}</small>{m.label.slice(0,160)}</button><div className="reading-actions"><button disabled={locked||state.status==='indexed'} onClick={()=>update('mark_material',{material_id:m.id,status:'indexed'})}>已核对位置</button><button disabled={locked||!note.trim()} onClick={()=>update('mark_material',{material_id:m.id,status:'needs_source',note})}>待补材料</button><button disabled={locked||!note.trim()} onClick={()=>update('mark_material',{material_id:m.id,status:'excluded',note})}>按备注排除</button><button disabled={locked||state.status==='pending'} onClick={()=>update('mark_material',{material_id:m.id,status:'pending'})}>撤销</button></div>{state.note&&<small>{state.note}</small>}</article>;})}</div>
    <p className="reading-hint">“已核对位置”只确认原图/表/公式在材料中可定位；尚未解释其内容。清单可能漏检，整页核查发现缺口时请标记待补材料。</p>
   </section>
  </div>

  </details>
  {packet&&<section className="reading-packet" aria-label="分节原文包"><h3>{packet.section.title}</h3><p>原文包已打开 · 阅读结构版本 {packet.reading_revision} · {packet.blocks.length} 个原文块 · 尚未调用模型</p><p className="reading-hint">{packet.preview_only?'当前仅预览；点击上方“采用当前解析结果，进入深读”后可建立底稿。':!packet.source_preparation?.human_structure_verified?'当前按自动解析结果阅读：章节、顺序及材料位置未完全核对，深读需保留这些不确定项。':'结构已核对，语义仍待深读。'} 仅展示本节字符范围；下载包保留整块原文供定位。以下仅为原文，不是深读结论。</p><ul>{packet.reading_tasks.map(t=><li key={t}>{t}</li>)}</ul><a href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(packet,null,2))}`} download={`section-packet-r${packet.reading_revision}.json`}>下载含来源与排除记录的材料包</a><details open><summary>查看本节完整原文（按当前保存顺序）</summary>{(packet.source_spans||packet.blocks.map(b=>({block_id:b.id,start:0,end:Array.from(b.text).length,text:b.text}))).map(s=><article key={`${s.block_id}-${s.start}`}><small>PDF {packet.blocks.find(b=>b.id===s.block_id).region.pdf_page} · 字符[{s.start}, {s.end})</small><p>{s.text}</p></article>)}</details></section>}
  {packet&&!packet.preview_only&&<FactWorksheet key={`${packet.section.id}-${packet.reading_revision}`} packet={packet} onLocate={onBlock} onDirtyChange={setFactDirty}/>}
 </section>;
}
