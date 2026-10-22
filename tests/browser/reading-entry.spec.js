import {test,expect} from '@playwright/test';

test('unconfirmed source opens as preview then enters reading without fake confirmations',async({page})=>{
 test.setTimeout(45000);
 await page.goto('/?workspace=evidence&tools=1');
 const pending=page.waitForResponse(r=>r.url().endsWith('/api/v2/documents')&&r.request().method()==='POST');
 await page.locator('input[type=file]').setInputFiles('tests/fixtures/reading-sample.pdf');
 const doc=await(await pending).json();await expect(page.getByRole('status')).toContainText('原始文件已登记并保存');
 const base='/api/v2/documents/'+doc.id;
 const meta=await(await page.request.get(base)).json();
 if(!meta.extraction)await page.request.post(base+'/parse',{headers:{'Idempotency-Key':crypto.randomUUID()}});
 let plan=await(await page.request.get(base+'/reading')).json();
 async function update(action,values={}){const r=await page.request.post(base+'/reading',{headers:{'Idempotency-Key':crypto.randomUUID()},data:{revision:plan.revision,action,...values}});expect(r.ok()).toBeTruthy();plan=await r.json();}
 await update('set_sections',{sections:[plan.sections[0]]});
 for(const p of plan.pages)await update('confirm_page',{page:p.page,status:'pending'});
 for(const m of plan.materials)await update('mark_material',{material_id:m.id,status:'pending'});
 await page.reload();await page.locator('.evidence-document[title="reading-sample.pdf"]').filter({hasText:'主论文'}).click();
 const prep=page.getByRole('region',{name:'章节与覆盖准备'});
 await expect(prep.getByRole('heading',{name:'选择章节，开始阅读'})).toBeVisible();
 await expect(prep.getByLabel('新增章节名称')).not.toBeVisible();
 await prep.getByRole('button',{name:'打开分节原文包',exact:true}).click();
 await expect(prep.getByRole('region',{name:'分节原文包'})).toContainText('当前仅预览');
 await expect(page.getByRole('region',{name:'逐段事实底稿'})).toHaveCount(0);
 await prep.getByRole('button',{name:'采用当前解析结果，进入深读',exact:true}).click();
 await expect(page.getByRole('region',{name:'逐段事实底稿'})).toBeVisible();
 await expect(prep.locator('.reading-state')).toContainText('按自动解析结果阅读');
 const after=await(await page.request.get(base+'/reading')).json();
 expect(after.readiness.can_read).toBe(true);expect(after.readiness.ready).toBe(false);
 expect(after.pages.every(p=>p.status==='pending')).toBe(true);expect(after.sections_confirmed).toBe(false);
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 await prep.locator('.reading-heading').scrollIntoViewIfNeeded();
 await page.screenshot({path:'test-results/reading-entry-mobile.png'});
  await prep.locator('.reading-start').screenshot({path:'test-results/reading-entry-controls-mobile.png'});
});
