import React, {useEffect, useRef, useState} from 'react';
import {ArrowUpRight, BookOpen, ChevronLeft, ChevronRight, FileText, Layers, LoaderCircle, Search, Upload} from 'lucide-react';
import './evidence.css';
import ReadingPreparation from './ReadingPreparation';
import PaperReport from './PaperReport';

const prefix='/api/v2/documents';
const kindNames={figure_caption:'图注候选',table_caption:'表注候选',image:'嵌入图像',appendix_heading:'附录标题候选',equation_candidate:'公式候选'};
async function request(path='',options={}){
 const response=await fetch(prefix+path,options);
 const data=await response.json();
 if(!response.ok)throw Error(data.message||(typeof data.detail==='string'?data.detail:'请求失败，请检查文件或输入'));
 return data;
}
function pageWarning(value){return ({possible_multicolumn_reading_order_unverified:'此页可能有多栏，提取顺序尚未复核',text_outside_visible_page:'存在可见页范围之外的文字',text_region_clipped_to_visible_page:'部分文字区域按可见页面边界裁切'})[value]||value;}

export default function EvidenceWorkspace(){
 const [documents,setDocuments]=useState([]),[selectedId,setSelectedId]=useState(''),[detail,setDetail]=useState(null);
 const [page,setPage]=useState(1),[blockPage,setBlockPage]=useState({items:[],total:0}),[materials,setMaterials]=useState([]);
 const [selection,setSelection]=useState(null),[query,setQuery]=useState(''),[draft,setDraft]=useState(''),[offset,setOffset]=useState(0);
 const [tab,setTab]=useState('blocks'),[busy,setBusy]=useState(''),[error,setError]=useState(''),[message,setMessage]=useState('');
 const [role,setRole]=useState('main'),[imageError,setImageError]=useState(false),[loadingBlocks,setLoadingBlocks]=useState(false);
 const [unsaved,setUnsaved]=useState(false);
 const [toolsOpen,setToolsOpen]=useState(()=>new URLSearchParams(window.location.search).get('tools')==='1');
 const input=useRef();
 const parsed=detail?.document.id===selectedId?detail.extraction:null;
 const pageInfo=parsed?.pages.find(p=>p.pdf_page===page);
 async function refresh(preferred){const result=await request();setDocuments(result.items);if(preferred)setSelectedId(preferred);else setSelectedId(prev=>prev||result.items[0]?.document.id||'');}
 useEffect(()=>{refresh().catch(e=>setError(e.message));},[]);
 useEffect(()=>{
  if(!selectedId)return;
  const controller=new AbortController();setDetail(null);setSelection(null);setMaterials([]);setBlockPage({items:[],total:0});setPage(1);setOffset(0);setQuery('');setDraft('');setImageError(false);
  request('/'+selectedId,{signal:controller.signal}).then(async d=>{
   setDetail(d);if(d.extraction){const m=await request('/'+selectedId+'/materials',{signal:controller.signal});setMaterials(m.items);}
  }).catch(e=>{if(e.name!=='AbortError')setError(e.message);});
  return ()=>controller.abort();
 },[selectedId]);
 useEffect(()=>{
  if(!parsed)return;
  const controller=new AbortController();setLoadingBlocks(true);
  const params=new URLSearchParams({page:String(page),q:query,limit:'50',offset:String(offset)});
  request('/'+selectedId+'/blocks?'+params,{signal:controller.signal}).then(setBlockPage).catch(e=>{if(e.name!=='AbortError')setError(e.message);}).finally(()=>{if(!controller.signal.aborted)setLoadingBlocks(false);});
  return ()=>controller.abort();
 },[selectedId,parsed?.id,page,query,offset]);
 useEffect(()=>{setImageError(false);},[selectedId,page]);
 async function upload(files){
  if(!files?.length)return;setBusy('upload');setError('');setMessage('');
  try{const form=new FormData();form.append('file',files[0]);form.append('material_role',role);
   const result=await request('',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()},body:form});
   await request('/'+result.id+'/parse',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()}});
   const existing=await request('/'+result.id);
   await refresh(result.id);setMessage('原始文件已登记并保存，已自动准备全文文字。确认模型发送范围后，即可开始全文解读。');
  }catch(e){setError(e.message);}finally{setBusy('');if(input.current)input.current.value='';}
 }
 async function parse(){
  setBusy('parse');setError('');setMessage('');
  try{await request('/'+selectedId+'/parse',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()}});
   const d=await request('/'+selectedId);setDetail(d);
   const m=await request('/'+selectedId+'/materials');setMaterials(m.items);
   await refresh(selectedId);setMessage('结构解析已完成。当前展示的是原文与位置候选，尚未进行语义研读或专家审查。');
  }catch(e){setError(e.message);}finally{setBusy('');}
 }
 async function locateReportEvidence(c){setToolsOpen(true);try{if(c.block_id){const block=await request('/'+selectedId+'/blocks/'+c.block_id);selectBlock(block);}else if(c.pdf_page){setPage(c.pdf_page);setOffset(0);setSelection(c.bbox?{bbox:c.bbox,label:c.label||'原文图表',page:c.pdf_page}:null);}setTimeout(()=>document.querySelector('.source-tools')?.scrollIntoView({behavior:'smooth',block:'start'}),80);}catch(e){setError(e.message);}}
 function changePage(next){setPage(next);setOffset(0);setSelection(null);}
 function selectBlock(block){setPage(block.region.pdf_page);setSelection({bbox:block.region.bbox,label:block.text,block_id:block.id,page:block.region.pdf_page,hash:block.text_sha256});}
 function selectMaterial(material){setPage(material.pdf_page);setOffset(0);setSelection({bbox:material.bbox,label:material.label,block_id:material.block_id,page:material.pdf_page});}
 const overlay=selection&&pageInfo&&selection.page===page?{left:selection.bbox[0]/pageInfo.width*100+'%',top:selection.bbox[1]/pageInfo.height*100+'%',width:(selection.bbox[2]-selection.bbox[0])/pageInfo.width*100+'%',height:(selection.bbox[3]-selection.bbox[1])/pageInfo.height*100+'%'}:null;
 return <div className="evidence-app">
  <header className="evidence-header"><a className="evidence-brand" href="/"><span><Layers size={22}/></span><div>研证<small>EvidenceMap</small></div></a><div className="evidence-stage"><span className="dot green"/>全文解读与阅读指引</div></header>
  <div className="evidence-intro"><div><div className="eyebrow">FROM PAPER TO UNDERSTANDING</div><h1>上传一篇论文，读懂它的核心。</h1><p>研究什么、如何展开、创新在哪里，以及你该重点读哪里。</p></div><div className="evidence-intro-note"><strong>你的阅读路径</strong><span>上传论文 → 全文研读 → 解读报告</span><small>原文依据随时可查，疑点与审查按需展开</small></div></div>
  {(error||message)&&<div className={'evidence-alert '+(error?'is-error':'')} role={error?'alert':'status'}>{error||message}</div>}
  <div className="evidence-layout">
   <div className="evidence-main-row">
   <aside className="evidence-library"><div className="evidence-panel-title"><h2>资料版本</h2><span>{documents.length}</span></div><select aria-label="上传材料类型" value={role} onChange={e=>setRole(e.target.value)} disabled={!!busy||unsaved}><option value="main">主论文</option><option value="supplement">补充材料</option></select><button className="dark-button evidence-upload" disabled={!!busy||unsaved} onClick={()=>input.current.click()}>{busy==='upload'?<LoaderCircle className="spin" size={15}/>:<Upload size={15}/>}上传 PDF</button><input ref={input} hidden type="file" accept="application/pdf,.pdf" onChange={e=>upload(e.target.files)}/><p className="evidence-hint">{unsaved?'有未保存修改，请先保存或撤销后切换资料。':'单篇 ≤ 20 MB、100 页。文件按版本保留；新资料独立存储。'}</p>
   <div className="evidence-document-list">{documents.map(item=><button key={item.document.id} className={'evidence-document '+(selectedId===item.document.id?'selected':'')} disabled={!!busy||unsaved} onClick={()=>{setError('');setSelectedId(item.document.id);}}><FileText size={17}/><span><strong>{item.title}</strong><small>{item.document.page_count} 页 · {item.document.material_role==='main'?'主论文':'补充材料'}</small><em>{item.extraction?'已解析结构':'待解析'}</em></span></button>)}</div>
   {!documents.length&&<div className="evidence-library-empty">上传论文后，原件和解析版本会显示在这里。</div>}
   {detail&&<details className="evidence-version"><summary>文件版本信息</summary><p>{detail.filename}</p><p>SHA-256<br/><code>{detail.document.sha256}</code></p>{parsed&&<p>{parsed.parser_version}</p>}</details>}</aside>
   <PaperReport documentId={selectedId} extractionId={parsed?.id} onLocate={locateReportEvidence} disabled={!!busy||unsaved}/>
   </div>
   <details className="source-tools" open={toolsOpen} onToggle={e=>setToolsOpen(e.currentTarget.open)}><summary>查看原文与辅助工具：章节校正、证据定位、分节深读和审查</summary><div className="source-tools-inner">
   <section className="evidence-reader"><div className="evidence-panel-title"><h2>原文页面</h2>{detail&&<a className="text-button" href={`${prefix}/${selectedId}/file#page=${page}`} target="_blank" rel="noreferrer">打开 PDF<ArrowUpRight size={13}/></a>}</div>
    {!detail?<div className="evidence-empty"><BookOpen size={35}/><h3>{selectedId?'正在读取资料…':'建立你的第一份原文档案'}</h3><p>上传一篇论文，先核查页面与材料覆盖。</p></div>:!parsed?<div className="evidence-empty"><Layers size={35}/><h3>原件已就绪</h3><p>解析将保留文本块坐标，列出图表与附录候选位置。</p><button className="dark-button" disabled={!!busy} onClick={parse}>{busy==='parse'?<LoaderCircle className="spin" size={15}/>:<Search size={15}/>}解析结构</button></div>:<>
    <div className="evidence-page-controls"><button aria-label="上一页" disabled={page<=1} onClick={()=>changePage(page-1)}><ChevronLeft size={17}/></button><label>PDF 第 <select aria-label="查看 PDF 页码" value={page} onChange={e=>changePage(Number(e.target.value))}>{parsed.pages.map(p=><option key={p.pdf_page} value={p.pdf_page}>{p.pdf_page}</option>)}</select> / {parsed.pages.length} 页</label><button aria-label="下一页" disabled={page>=parsed.pages.length} onClick={()=>changePage(page+1)}><ChevronRight size={17}/></button><span>{pageInfo?.text_status==='text_available'?'有可提取文字':'需人工核查'}</span></div>
    <div className="evidence-paper-scroll"><div className="evidence-paper" style={{aspectRatio:pageInfo?`${pageInfo.width}/${pageInfo.height}`:undefined}}>{imageError?<p role="alert">页面预览失败，请检查原件完整性或打开 PDF 核查。</p>:<img key={`${selectedId}-${page}`} src={`${prefix}/${selectedId}/pages/${page}/image`} alt={`论文原文 PDF 第 ${page} 页`} onError={()=>setImageError(true)}/>} {!imageError&&overlay&&<div className="evidence-highlight" style={overlay} aria-label="当前证据区域"/>}</div></div>
    <div className="evidence-reader-note">预览采用未旋转坐标，与文本块位置一致。PDF 页码不等同于识别出的印刷页码。</div>
    </>}
   </section>
   <section className="evidence-inspector"><div className="evidence-panel-title"><h2>证据索引</h2><span>{parsed?parsed.block_count+' 块':'等待解析'}</span></div>
   {parsed?<><div className="evidence-stat-row"><span><strong>{parsed.pages.length}</strong>已提取页面</span><span><strong>{parsed.material_count}</strong>材料候选</span><span><strong>0</strong>模型调用</span></div><div className="evidence-tabs"><button className={tab==='blocks'?'on':''} onClick={()=>setTab('blocks')}>当前页文本</button><button className={tab==='materials'?'on':''} onClick={()=>setTab('materials')}>全篇材料候选</button></div>
   {tab==='blocks'?<><form className="evidence-search" onSubmit={e=>{e.preventDefault();setQuery(draft);setOffset(0);}}><input aria-label="当前页原文关键词" value={draft} onChange={e=>setDraft(e.target.value)} placeholder="当前页原文关键词" maxLength={500}/><button aria-label="检索当前页原文"><Search size={16}/></button></form><div className="evidence-index-list" aria-busy={loadingBlocks}>{loadingBlocks?<p className="evidence-hint">读取文本块…</p>:blockPage.items.map((block,i)=><button key={block.id} className={'evidence-block '+(selection?.block_id===block.id?'chosen':'')} onClick={()=>selectBlock(block)}><span>PDF {block.region.pdf_page} · 块 {block.order_in_page+1}<ArrowUpRight size={12}/></span><p>{block.text.slice(0,230)}{block.text.length>230?'…':''}</p></button>)}{!loadingBlocks&&!blockPage.total&&<p className="evidence-hint">未找到文本块。检查页码、关键词，或核查是否为图像页；此结果不代表论文没有相关信息。</p>}</div><div className="evidence-pagination"><button disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-50))}>上一组</button><span>{blockPage.total} 个匹配文本块</span><button disabled={offset+50>=blockPage.total} onClick={()=>setOffset(offset+50)}>下一组</button></div></>:<div className="evidence-index-list">{materials.map(m=><button key={m.id} className="evidence-material" onClick={()=>selectMaterial(m)}><span>{kindNames[m.kind]} · PDF {m.pdf_page}</span><p>{m.label.slice(0,140)}</p></button>)}{!materials.length&&<p className="evidence-hint">未识别到候选，不等于论文没有图表或附录。</p>}</div>}
   {selection&&<div className="evidence-selection"><div>当前定位 · PDF {selection.page}</div><p>{selection.label}</p><small>区域：{selection.bbox.map(x=>x.toFixed(1)).join(', ')}</small>{selection.block_id&&<a className="text-button" href={`${prefix}/${selectedId}/blocks/${selection.block_id}/image`} target="_blank" rel="noreferrer">打开文本块区域图<ArrowUpRight size={12}/></a>}</div>}
   <details className="evidence-quality"><summary>解析边界与本页提示</summary><p>结构解析不等于理解正确。图表、公式和附录目前均为候选位置；未提取表格单元格，未执行 OCR、语义精读或审稿。</p>{pageInfo?.warnings.map((w,i)=><p key={i}>{pageWarning(w)}</p>)}<p>提取顺序尚未按双栏复核，候选需人工确认。</p></details>
   </>:<div className="evidence-empty small"><FileText size={25}/><p>解析完成后，可点击文本块或材料候选，在原文中查看对应区域。</p></div>}
   </section>
  {parsed&&<ReadingPreparation key={selectedId} documentId={selectedId} extractionId={parsed.id} page={page} onPage={changePage} onBlock={selectBlock} onMaterial={selectMaterial} onUnsavedChange={setUnsaved}/>}</div></details></div><footer className="evidence-footer"><span>研证 EvidenceMap · 全文解读</span><span>上传 → 全文研读 → 综合解读 → 阅读路线 <b>关键解释可回查原文</b></span></footer>
 </div>;
}
