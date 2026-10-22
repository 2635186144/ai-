import {test,expect} from '@playwright/test';

test('synthetic UI workflow shows progress, pauses, resumes, exports and survives reload',async({page})=>{
 test.setTimeout(45000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/?workspace=evidence&tools=1');
 const pending=page.waitForResponse(r=>r.url().endsWith('/api/v2/documents')&&r.request().method()==='POST');
 await page.locator('input[type=file]').setInputFiles('tests/fixtures/reading-sample.pdf');
 const doc=await(await pending).json();await expect(page.getByRole('status')).toContainText('原始文件已登记并保存');
 const base='/api/v2/documents/'+doc.id;
 let meta=await(await page.request.get(base)).json();
 if(!meta.extraction)await page.request.post(base+'/parse',{headers:{'Idempotency-Key':crypto.randomUUID()}});
 let prep=await(await page.request.get(base+'/reading')).json();
 async function update(action,values={}){const r=await page.request.post(base+'/reading',{headers:{'Idempotency-Key':crypto.randomUUID()},data:{revision:prep.revision,action,...values}});expect(r.ok()).toBeTruthy();prep=await r.json();}
 await update('set_sections',{sections:[prep.sections[0]]});
 for(const p of prep.pages)await update('confirm_page',{page:p.page,status:'confirmed'});
 for(const m of prep.materials)await update('mark_material',{material_id:m.id,status:'indexed'});
 await update('confirm_sections');
 const section=base+'/reading/sections/'+prep.sections[0].start_block_id;
 const sheet=await(await page.request.get(section+'/facts?reading_revision='+prep.revision)).json();
 // Browser transport stubs only. No model configuration or live model request.
 await page.route('**/facts?reading_revision=*',async route=>{
  const r=await route.fetch(),data=await r.json();data.model={...data.model,configured:true,model_name:'模拟数据：浏览器替身'};
  await route.fulfill({json:data});
 });
 const tasks=[...sheet.units.filter(u=>!u.draft).map(u=>({id:'read:'+u.id,kind:'reading',state:'pending',attempts:0})),...['review_rigor','review_skeptic','challenge','response','verdict'].map(kind=>({id:kind,kind,state:'pending',attempts:0}))];
 let run=null;
 await page.route('**/runs?reading_revision=*',route=>route.fulfill({json:{items:run?[run]:[]}}));
 await page.route('**/reading/sections/*/runs',async route=>{
  const sent=route.request().postDataJSON();expect(sent.consent_to_send).toBe(true);
  run={id:'sectionrun_browser_synthetic',created_at:'模拟数据',revision:1,state:'queued',tasks,max_calls:sent.max_calls,calls_reserved:0,fact_revision:sheet.revision,model_name:'模拟数据：浏览器替身',progress:{completed:0,total:tasks.length,percent:0},reading_gaps:[],semantic_verified:false};
  await route.fulfill({status:201,json:run});
 });
 await page.route('**/runs/sectionrun_browser_synthetic',route=>route.fulfill({json:run}));
 await page.route('**/runs/sectionrun_browser_synthetic/control',async route=>{
  const {action}=route.request().postDataJSON();run.revision++;
  if(action==='start')run.state='running';
  if(action==='pause'){run.state='paused';run.tasks[0].state='succeeded';run.progress.completed=1;run.calls_reserved=1;}
  if(action==='resume'){
   run.state='completed';run.tasks.forEach(t=>t.state='succeeded');run.progress.completed=tasks.length;run.calls_reserved=tasks.length;
   run.tasks.at(-1).output={items:[{issue_id:'synthetic:issue:0',decision:'undetermined',reason:'模拟数据：用于验证界面，不代表真实科研审查效果。',remaining_evidence:'仍需真实评估',citations:[{...sheet.units[0].source_spans[0],quote:sheet.units[0].source_spans[0].text,pdf_page:sheet.units[0].region.pdf_page}]}]};
  }
  await route.fulfill({json:run});
 });
 async function open(){await page.reload();await page.locator('.evidence-document[title="reading-sample.pdf"]').filter({hasText:'主论文'}).click();await page.getByRole('button',{name:'打开分节原文包',exact:true}).click();}
 await open();const panel=page.getByRole('region',{name:'分节自动审查任务'});
 await expect(panel.getByRole('button',{name:'准备本节任务'})).toBeDisabled();
 await panel.getByRole('checkbox',{name:/我同意将本节原文/}).check();
 await panel.getByRole('button',{name:'准备本节任务'}).click();
 await panel.getByRole('button',{name:'开始本节自动流程'}).click();
 await expect(page.getByLabel('本段解释',{exact:true})).toBeDisabled();
 await panel.getByRole('button',{name:'暂停任务'}).click();await expect(panel.getByRole('button',{name:'继续任务'})).toBeVisible();
 await open();await expect(panel.getByRole('button',{name:'继续任务'})).toBeVisible();
 await panel.getByRole('button',{name:'继续任务'}).click();
 await expect(panel).toContainText('模拟数据：用于验证界面');
 await expect(panel).toContainText('证据不足，暂不裁决');
 const href=await panel.getByRole('link',{name:'导出任务、引文与裁决记录'}).getAttribute('href');
 const exported=JSON.parse(decodeURIComponent(href.slice(href.indexOf(',')+1)));
 expect(exported.state).toBe('completed');expect(exported.semantic_verified).toBe(false);
 await panel.screenshot({path:'test-results/section-workflow-desktop.png'});
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 await panel.screenshot({path:'test-results/section-workflow-mobile.png'});
 expect(errors).toEqual([]);
});
