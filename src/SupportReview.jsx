import React,{useEffect,useState} from 'react';

const statuses={not_reviewed:'尚未复核',recorded_supported:'已记录为支持（未独立验证）',pending:'仍待核对',needs_revision:'存在证据缺口或矛盾',incomplete_claim_coverage:'陈述有未覆盖部分'};
const blank=(text,start=0,end=Array.from(text).length)=>({start,end,text:Array.from(text).slice(start,end).join(''),kind:'positive',verdict:'pending',reason:'',citation_indices:[],checked_scope:[]});

export function SupportStatus({check}){return <span>{statuses[check?.status||'not_reviewed']}</span>;}

export default function SupportReview({fact,index,unit,revision,readingRevision,check,disabled,onSave,onDirtyChange,onClose}){
 const [parts,setParts]=useState(()=>check?.parts?.length?structuredClone(check.parts):[blank(fact.statement)]);
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const original=JSON.stringify(check?.parts?.length?check.parts:[blank(fact.statement)]);
 const dirty=JSON.stringify(parts)!==original;
 useEffect(()=>{onDirtyChange(dirty);return ()=>onDirtyChange(false);},[dirty,onDirtyChange]);
 function edit(i,patch){setParts(prev=>prev.map((p,j)=>i===j?{...p,...patch}:p));}
 const spans=unit.source_spans||[{block_id:unit.block_id,start:unit.start,end:unit.end,text:unit.text}];
 async function save(){setBusy(true);setError('');try{await onSave({revision,reading_revision:readingRevision,unit_id:unit.id,fact_index:index,parts});}catch(e){setError(e.message);}finally{setBusy(false);}}
 return <section className="support-review" aria-label={`事实 ${index+1} 证据支持复核`}>
  <h4>逐项核对事实 {index+1}</h4><p>每个分项都要核对主语、条件、数量、比较对象和因果强度。选择“支持”是本地复核记录，不是程序判真。</p>
  <p>已保存状态：<SupportStatus check={check}/></p>
  <button disabled={busy||disabled} onClick={()=>{setParts(Array.from(fact.statement.matchAll(/[^，。；;]+[，。；;]?/gu)).map(m=>{const start=Array.from(fact.statement.slice(0,m.index)).length;return {...blank(m[0]),start,end:start+Array.from(m[0]).length};}));}}>按标点拆分后逐项核对</button>
  {parts.map((p,i)=><fieldset key={i} disabled={busy||disabled}><legend>分项 {i+1}</legend>
   <div className="support-range"><label>陈述起点<input type="number" min="0" aria-label={`分项 ${i+1} 起点`} value={p.start} onChange={e=>{const start=Number(e.target.value);edit(i,{start,text:Array.from(fact.statement).slice(start,p.end).join('')});}}/></label><label>陈述终点<input type="number" min="1" aria-label={`分项 ${i+1} 终点`} value={p.end} onChange={e=>{const end=Number(e.target.value);edit(i,{end,text:Array.from(fact.statement).slice(p.start,end).join('')});}}/></label></div>
   <blockquote>{p.text}</blockquote>
   <label>判断类型<select aria-label={`分项 ${i+1} 判断类型`} value={p.kind} onChange={e=>edit(i,{kind:e.target.value,checked_scope:[]})}><option value="positive">原文表述或阅读推断</option><option value="absence">本段未提供某项内容</option></select></label>
   <label>支持判断<select aria-label={`分项 ${i+1} 支持判断`} value={p.verdict} onChange={e=>edit(i,{verdict:e.target.value})}><option value="pending">待核对</option><option value="supported">支持</option><option value="partial">部分支持</option><option value="unsupported">不支持 / 无充分依据</option><option value="contradicted">与证据矛盾</option></select></label>
   <p>关联已有引文（补充引文须先修订底稿）</p>{fact.citations.map((c,j)=><label className="support-citation" key={j}><input type="checkbox" aria-label={`分项 ${i+1} 关联引文 ${j+1}`} checked={p.citation_indices.includes(j)} onChange={e=>edit(i,{citation_indices:e.target.checked?[...p.citation_indices,j]:p.citation_indices.filter(x=>x!==j)})}/><span>{j+1}. {c.quote}<small>{c.block_id} [{c.start}, {c.end})</small></span></label>)}
   {p.kind==='absence'&&<label className="support-citation"><input type="checkbox" aria-label={`分项 ${i+1} 已检查完整目标段`} checked={p.checked_scope.length>0} onChange={e=>edit(i,{checked_scope:e.target.checked?spans.map(s=>({block_id:s.block_id,start:s.start,end:s.end,quote:s.text})):[]})}/><span>我已对照上方完整目标段逐字检查；此判断不延伸到全文。</span></label>}
   <label className="fact-field">支持依据或缺口<textarea aria-label={`分项 ${i+1} 判断理由`} maxLength={3000} value={p.reason} onChange={e=>edit(i,{reason:e.target.value})}/></label>
  </fieldset>)}
  {error&&<p role="alert" className="reading-error">{error}</p>}
  <div className="fact-save"><button disabled={busy||disabled||!dirty} onClick={save}>保存证据支持复核</button><button disabled={busy} onClick={()=>{setParts(check?.parts?.length?structuredClone(check.parts):[blank(fact.statement)]);onClose();}}>关闭 / 放弃未保存复核</button></div>
 </section>;
}
