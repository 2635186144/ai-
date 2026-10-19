import React,{useEffect,useRef,useState} from 'react';
import './paper-visuals.css';

export function useSourceVisuals(documentId,enabled){
 const [data,setData]=useState(null);
 useEffect(()=>{
  if(!documentId||!enabled)return;
  const controller=new AbortController();
  fetch(`/api/v2/documents/${documentId}/visuals`,{signal:controller.signal}).then(async r=>{
   if(!r.ok)throw Error('preview unavailable');return r.json();
  }).then(value=>setData({documentId,...value})).catch(e=>{if(e.name!=='AbortError')setData({documentId,items:[],error:true});});
  return ()=>controller.abort();
 },[documentId,enabled]);
 return data?.documentId===documentId?data:{items:[],loading:enabled};
}

export function referencedVisuals(items,text){
 const labels=new Set();
 const re=/(图|表|Figure|Fig\.?|Table|Tab\.?)\s*(\d+)(?:\s*(?:至|到|—|–|-)\s*(?:图|表|Figure|Table)?\s*(\d+))?/gi;
 for(const match of text.matchAll(re)){
  const prefix=/^(?:表|tab)/i.test(match[1])?'表':'图',start=Number(match[2]),end=match[3]?Number(match[3]):start;
  if(end>=start&&end-start<=20)for(let i=start;i<=end;i++)labels.add(prefix+i);
 }
 return items.filter(item=>labels.has(item.label));
}

function SourceImage({src,alt,...props}){
 const [failed,setFailed]=useState(false);
 useEffect(()=>setFailed(false),[src]);
 return failed?<p className="visual-image-error">预览暂不可用，请查看原页或打开 PDF。</p>:<img {...props} src={src} alt={alt} onError={()=>setFailed(true)}/>;
}

export function VisualViewer({documentId,item,onClose,onLocate}){
 const dialog=useRef(),[wholePage,setWholePage]=useState(false),[zoomed,setZoomed]=useState(false);
 const prefix=`/api/v2/documents/${documentId}`;
 useEffect(()=>{setWholePage(false);setZoomed(false);if(item)dialog.current?.showModal();else dialog.current?.close();},[item]);
 return <dialog ref={dialog} className="source-visual-dialog" aria-label="原文图表大图" onClose={onClose} onClick={e=>{if(e.target===e.currentTarget)dialog.current.close();}}>{item&&<>
  <header><div><span>原文图表 · PDF {item.pdf_page}</span><h3>{item.caption.split('\n')[0]}</h3></div><button autoFocus onClick={()=>dialog.current.close()} aria-label="关闭图表大图">关闭 ×</button></header>
  <div className="visual-view-actions"><button aria-pressed={!wholePage} onClick={()=>{setWholePage(false);setZoomed(false);}}>图表区域</button><button aria-pressed={wholePage} onClick={()=>{setWholePage(true);setZoomed(false);}}>查看整页</button><button aria-pressed={zoomed} onClick={()=>setZoomed(!zoomed)}>{zoomed?'适应窗口':'放大细节'}</button><a href={`${prefix}/file#page=${item.pdf_page}`} target="_blank" rel="noreferrer">打开原 PDF ↗</a><button onClick={()=>{onLocate({pdf_page:item.pdf_page,bbox:item.caption_bbox,label:item.caption});dialog.current.close();}}>回到原文页面</button></div>
  <div className={'visual-large-scroll'+(zoomed?' is-zoomed':'')}><SourceImage src={wholePage?`${prefix}/pages/${item.pdf_page}/image?scale=2`:`${prefix}/visuals/${item.id}/image?scale=2`} alt={`${item.caption}，PDF 第${item.pdf_page}页${wholePage?'整页原文':'原文区域'}`}/></div>
  <p className="visual-source-caption">{item.caption}</p><p className="visual-boundary">{item.crop_method==='page_fallback'?'图表边界未能可靠定位，展示完整原页。':'原文区域自动裁切，完整注释及上下文可切换整页查看。'} 展示原图不代表已核验图表结论。</p>
 </>}</dialog>;
}

export function VisualCards({documentId,items,onOpen,compact=false}){
 if(!items.length)return null;
 return <div className={'source-visual-grid'+(compact?' is-inline':'')}>{items.map(item=><figure className="source-visual-card" key={item.id}>
  <button className="visual-image-button" onClick={()=>onOpen(item)} aria-label={`放大${item.label} · PDF ${item.pdf_page}`}>
   <SourceImage src={`/api/v2/documents/${documentId}/visuals/${item.id}/image`} loading="lazy" decoding="async" alt={`${item.caption.split('\n')[0]}，PDF 第${item.pdf_page}页原图`}/><span>查看大图 ↗</span>
  </button><figcaption><div><b>{item.label}</b><span>PDF {item.pdf_page}{item.crop_method==='page_fallback'?' · 整页':''}</span></div><p>{item.caption.split('\n')[0].replace(/^(?:图|表|Figure|Fig\.?|Table|Tab\.?)\s*\d+\s*[.:：、]?\s*/i,'')}</p></figcaption>
 </figure>)}</div>;
}

export function SourceVisualGallery({documentId,data,report,onOpen}){
 if(data.loading)return <p className="report-section-note">正在整理原文图表…</p>;
 if(!data.items.length)return <p className="report-section-note">{data.error?'图表预览暂不可用，可通过原文页面查看。':'未识别到带图题的图片或表格；不代表论文没有图表，可回原文核对。'}</p>;
 const text=report.core_content.map(i=>i.title+' '+i.explanation.text).join(' ');
 const mentioned=new Set(referencedVisuals(data.items,text).map(i=>i.id));
 const ranked=[...data.items].sort((a,b)=>Number(b.is_overview_candidate)-Number(a.is_overview_candidate)||Number(mentioned.has(b.id))-Number(mentioned.has(a.id))||a.pdf_page-b.pdf_page);
 const overview=ranked.find(i=>i.is_overview_candidate),available=ranked.filter(i=>i.id!==overview?.id),perKind=overview?1:2;
 const selected=new Set(['figure','table'].flatMap(kind=>available.filter(i=>i.kind===kind).slice(0,perKind)).map(i=>i.id));
 for(const item of available){if(selected.size>=perKind*2)break;selected.add(item.id);}
 const first=available.filter(i=>selected.has(i.id)),rest=available.filter(i=>!selected.has(i.id));
 return <><p className="report-section-note">保留原文图题与页码；结合图表编号选取，点击可放大并回查整页。</p>{overview&&<div className="visual-overview"><span className="visual-overview-label">论文中的流程与框架</span><VisualCards documentId={documentId} items={[overview]} onOpen={onOpen}/></div>}<VisualCards documentId={documentId} items={first} onOpen={onOpen}/>{rest.length>0&&<details className="visual-more"><summary>查看其余 {rest.length} 张原文图表 · 共 {ranked.length} 张</summary><VisualCards documentId={documentId} items={rest} onOpen={onOpen}/></details>}<p className="visual-boundary">图表来自上传的 PDF，本地渲染；自动裁图可能遗漏边缘注释，请对照整页。此处没有重新绘制数据，也未新增视觉模型分析。</p></>;
}
