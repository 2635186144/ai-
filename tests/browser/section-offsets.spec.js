import {test,expect} from '@playwright/test';

test('one canonical block splits into chapters and scoped facts through the UI',async({page})=>{
 test.setTimeout(45000);
 await page.goto('/?workspace=evidence&tools=1');
 const response=page.waitForResponse(r=>r.url().endsWith('/api/v2/documents')&&r.request().method()==='POST');
 await page.locator('input[type=file]').setInputFiles('tests/fixtures/section-offsets.pdf');
 const doc=await(await response).json(),base='/api/v2/documents/'+doc.id;
 await expect(page.getByRole('status')).toContainText('原始文件已登记并保存');
 let detail=await(await page.request.get(base)).json();
 if(!detail.extraction){await page.getByRole('button',{name:'解析结构',exact:true}).click();await expect(page.getByRole('region',{name:'章节与覆盖准备'})).toBeVisible();}
 let plan=await(await page.request.get(base+'/reading')).json();
 async function update(action,fields={}){const r=await page.request.post(base+'/reading',{headers:{'Idempotency-Key':crypto.randomUUID()},data:{revision:plan.revision,action,...fields}});expect(r.ok()).toBeTruthy();plan=await r.json();}
 await update('set_sections',{sections:[{start_block_id:plan.pages[0].block_ids[0],title:'Front',level:1}]});
 for(const p of plan.pages)await update('confirm_page',{page:p.page,status:'confirmed'});
 for(const m of plan.materials)await update('mark_material',{material_id:m.id,status:'indexed'});
 await update('confirm_sections');
 await page.reload();
 await page.locator('.evidence-document').filter({hasText:'section-offsets'}).click();
 const prep=page.getByRole('region',{name:'章节与覆盖准备'}),bid=plan.pages[0].block_ids[0];
 const block=plan.block_previews.find(b=>b.id===bid);
 const method=block.line_starts.find(l=>l.text==='2.1 Method'),evaluation=block.line_starts.find(l=>l.text==='2.2 Evaluation');
 await prep.getByText('校正章节、阅读顺序和图表（可选）',{exact:true}).click();
 await prep.getByLabel('新增章节的起始文本块',{exact:true}).selectOption(bid);
 await prep.getByLabel('选择块内标题行',{exact:true}).selectOption(String(method.offset));
 await expect(prep.getByLabel('新增章节名称',{exact:true})).toHaveValue('2.1 Method');
 await prep.getByRole('button',{name:'补充章节',exact:true}).click();
 await prep.getByLabel('选择块内标题行',{exact:true}).selectOption(String(evaluation.offset));
 await prep.getByRole('button',{name:'补充章节',exact:true}).click();
 await prep.getByRole('button',{name:'保存章节修改',exact:true}).click();
 await expect(prep.getByLabel('章节 2 块内起点',{exact:true})).toHaveValue(String(method.offset));
 await expect(prep.getByRole('button',{name:'打开分节原文包',exact:true})).toBeEnabled();
 await prep.getByRole('button',{name:'确认整篇章节边界',exact:true}).click();
 await expect(prep.getByRole('button',{name:'打开分节原文包',exact:true})).toBeEnabled();
 await prep.getByRole('combobox',{name:'分节材料包章节'}).selectOption(bid+'~'+method.offset);
 await prep.getByRole('button',{name:'打开分节原文包',exact:true}).click();
 const packet=prep.getByRole('region',{name:'分节原文包'});

 await expect(packet.locator('details')).toContainText('unique method evidence');
 await expect(packet.locator('details')).not.toContainText('Algorithm tail');
 await expect(packet.locator('details')).not.toContainText('evaluation-only');
 const sheet=page.getByRole('region',{name:'逐段事实底稿'});
 await expect(sheet.locator('.fact-source>p')).not.toContainText('evaluation-only');
 await sheet.getByLabel('本段解释',{exact:true}).fill('Synthetic fixture: only the method range is being read.');
 await sheet.getByRole('button',{name:'添加事实条目',exact:true}).click();
 await sheet.getByLabel('事实 1 陈述',{exact:true}).fill('Synthetic method text is scoped to the selected chapter.');
 await sheet.getByRole('button',{name:'保存本段底稿',exact:true}).click();
 await expect(sheet.getByRole('status')).toContainText('本段记录已保存');
 const href=await sheet.getByRole('link',{name:'导出带来源的事实底稿'}).getAttribute('href');
 const ledger=JSON.parse(decodeURIComponent(href.split(',').slice(1).join(',')));
 expect(ledger.units[0].draft.facts[0].citations[0].start).toBe(method.offset);
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 await prep.locator('.reading-chapters').screenshot({path:'test-results/section-offsets-mobile.png'});
});
