import {test,expect} from '@playwright/test';

test('reading preparation gates packets, invalidates edits, persists and fits mobile',async({page})=>{
 test.setTimeout(60000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/?workspace=evidence&tools=1');
 await page.getByLabel('上传材料类型').selectOption('supplement');
 const registeredPromise=page.waitForResponse(r=>r.url().endsWith('/api/v2/documents')&&r.request().method()==='POST');
 await page.locator('input[type=file]').setInputFiles('tests/fixtures/reading-sample.pdf');
 const registered=await(await registeredPromise).json();
 await expect(page.getByRole('status')).toContainText('原始文件已登记并保存');
 const base='/api/v2/documents/'+registered.id,metadata=await(await page.request.get(base)).json();
 if(!metadata.extraction)await page.getByRole('button',{name:'解析结构',exact:true}).click();
 const prep=page.getByRole('region',{name:'章节与覆盖准备'});
 await expect(prep.getByRole('heading',{name:'选择章节，开始阅读'})).toBeVisible();
 // Reset only this fixture's manual coverage to support repeated local runs.
 let plan=await(await page.request.get(base+'/reading')).json();
 for(const p of plan.pages){const r=await page.request.post(base+'/reading',{headers:{'Idempotency-Key':crypto.randomUUID()},data:{revision:plan.revision,action:'confirm_page',page:p.page,status:'pending'}});expect(r.ok()).toBeTruthy();plan=await r.json();}
 for(const m of plan.materials){const r=await page.request.post(base+'/reading',{headers:{'Idempotency-Key':crypto.randomUUID()},data:{revision:plan.revision,action:'mark_material',material_id:m.id,status:'pending'}});expect(r.ok()).toBeTruthy();plan=await r.json();}
 await page.reload();await page.locator('.evidence-document[title="reading-sample.pdf"]').filter({hasText:'补充材料'}).click();await expect(prep.locator('.reading-state')).toContainText('版本 '+plan.revision);
 await expect(prep.getByRole('button',{name:'打开分节原文包'})).toBeEnabled();
 await prep.getByText('校正章节、阅读顺序和图表（可选）',{exact:true}).click();
 async function mutate(button){const pending=page.waitForResponse(r=>r.url().endsWith('/reading')&&r.request().method()==='POST');await button.click();const response=await pending;expect(response.ok()).toBeTruthy();const result=await response.json();await expect(prep.locator('.reading-state')).toContainText('版本 '+result.revision);return result;}
 await mutate(prep.getByRole('button',{name:'下移阅读块 1',exact:true}));
 await expect(prep.getByRole('button',{name:'确认整篇章节边界',exact:true})).toBeEnabled();
 await mutate(prep.getByRole('button',{name:'确认整篇章节边界',exact:true}));
 for(const p of plan.pages){
  await prep.getByRole('button',{name:`核对 PDF 第 ${p.page} 页`,exact:true}).click();
  await mutate(prep.getByRole('button',{name:'确认本页顺序与材料清点',exact:true}));
  const candidates=prep.getByRole('button',{name:'已核对位置',exact:true});
  for(let i=0;i<await candidates.count();i++){if(await candidates.nth(i).isEnabled())await mutate(candidates.nth(i));}
 }
 await expect(prep.locator('.reading-state')).toContainText('结构已核对');
 await prep.getByRole('button',{name:'打开分节原文包'}).click();
 const packet=prep.getByRole('region',{name:'分节原文包'});
 await expect(packet).toContainText('尚未调用模型');
 await expect(packet.getByRole('link',{name:'下载含来源与排除记录的材料包'})).toBeVisible();
 await prep.screenshot({path:'test-results/reading-preparation-desktop.png'});
 await page.reload();await page.locator('.evidence-document[title="reading-sample.pdf"]').filter({hasText:'补充材料'}).click();await expect(prep.locator('.reading-state')).toContainText('结构已核对');
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 await prep.screenshot({path:'test-results/reading-preparation-mobile.png'});
 await prep.getByText('校正章节、阅读顺序和图表（可选）',{exact:true}).click();
 await mutate(prep.getByRole('button',{name:'上移阅读块 2',exact:true}));
 await expect(prep.getByRole('button',{name:'打开分节原文包'})).toBeEnabled();
 expect(errors).toEqual([]);
});
