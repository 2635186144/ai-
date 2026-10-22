import {test,expect} from '@playwright/test';

test('whole-paper report is primary, explains the paper and links a reading route to source',async({page})=>{
 test.setTimeout(45000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));let run=null,readyReport=null,localIdentity=null;
 // Explicit transport fixtures, no model service. The test server also has no Key.
 // First exercise historical APIs without title_identity, then new local-title
 // presentation with the real fixture's parsed source, still without an LLM.
 await page.route('**/report-plan',async route=>{const r=await route.fetch(),d=await r.json();localIdentity=d.title_identity;delete d.title_identity;d.model={...d.model,configured:true,model_name:'模拟数据：浏览器替身'};await route.fulfill({json:d});});
 await page.route('**/reports',async route=>{
  if(route.request().method()==='GET'){await route.fulfill({json:{items:run?[run]:[]}});return;}
  const command=route.request().postDataJSON();expect(command.consent_to_send).toBe(true);expect(command.automatic_recovery).toBe(true);
  run={id:'report_ui_synthetic',created_at:'模拟数据',revision:1,state:'queued',model_name:'模拟数据：浏览器替身',max_calls:command.max_calls,calls_reserved:0,semantic_verified:false,report:null,
       progress:{completed:0,total:2},coverage:{total_pages:2,text_pages:[1,2],page_gaps:[],material_count:2,visual_understanding_performed:false},
       tasks:[{id:'read_0',kind:'reading',index:0,pages:[1,2],state:'pending'},{id:'synthesis',kind:'synthesis',state:'pending'}]};
  await route.fulfill({status:201,json:run});
 });
 await page.route('**/reports/report_ui_synthetic/control',async route=>{expect(route.request().postDataJSON().action).toBe('start');run={...run,state:'running',revision:2};await route.fulfill({json:run});});
 await page.route('**/reports/report_ui_synthetic',async route=>{run={...run,state:'completed',revision:5,report:readyReport,calls_reserved:2,progress:{completed:2,total:2}};run.tasks.forEach(t=>t.state='succeeded');await route.fulfill({json:run});});
 await page.goto('/');await expect(page.getByRole('heading',{name:'上传一篇论文，读懂它的核心。'})).toBeVisible();
 const uploaded=page.waitForResponse(r=>r.url().endsWith('/api/v2/documents')&&r.request().method()==='POST');
 await page.locator('input[type=file]').setInputFiles('tests/fixtures/reading-sample.pdf');
 const doc=await(await uploaded).json();await expect(page.getByRole('status')).toContainText('原始文件已登记并保存');
 const blockPage=await(await page.request.get(`/api/v2/documents/${doc.id}/blocks?limit=50&offset=0`)).json();
 const b=blockPage.items[0],last=blockPage.items.at(-1);
 const evidence=[{id:'e1',block_id:b.id,pdf_page:b.region.pdf_page,start:0,end:b.text.length,quote:b.text},{id:'e2',block_id:last.id,pdf_page:last.region.pdf_page,start:0,end:last.text.length,quote:last.text}];
 const insight=text=>({text:'模拟数据：'+text,evidence_ids:['e1']});
 const longExplanation='输入是论文提取文字，阅读后保存有来源的“研读笔记”，再综合说明方法和证据。'+
  '模拟数据：这一方法首先把研究问题、方法设计和实验结果对应起来，让读者能够辨认作者在每个环节需要解决什么问题，以及后面的章节为什么接着讨论特定的技术选择。'+
  '模拟数据：在实际理解机制时，还要区分输入条件、处理过程和输出结果，不能因为术语相似就认为两种方法完全相同，也不能用图注的描述代替尚未完成的图像核对。'+
  '模拟数据：实验依据需要回到作者给出的任务设置和比较对象，确认结果支持的是哪一个局部结论，并保留没有测试的条件与未能从提取文字中恢复的内容。'+
  '模拟数据：完整解释保留这一尾段，任何阅读篇幅切换都不应该修改导出的原始解读，也不应该新增模型调用。';
 readyReport={title:{text:'《模拟数据：用于演示解读流程的测试论文》——模拟数据：这段是题名识别说明，不属于论文题目。',evidence_ids:['e1']},research_direction:insight('计算机技术中的论文理解与证据定位'),one_sentence:insight('根据全文内容建立连贯解释，并提供阅读指引。'),
   abstract_explanation:insight('研究动机、方法和实验应联系起来理解，测试夹具不代表真实研究效果。'),abstract_evidence_ids:[],
   argument_map:[{heading:'研究问题与方法',role:'定义问题并解释设计选择。',content:insight('方法回答前面提出的问题，实验用于检查边界。'),connects_to:'从方法假设走向实验检验。'}],
   core_content:[{title:'方法如何工作',explanation:insight(longExplanation)}],
   innovations:[{title:'贡献边界示例',author_claim:insight('作者声称提供新的理解方式。'),explanation:insight('需明确具体改动及其原因。'),boundary:insight('未做真实质量评估，不能宣称优于现有方法。'),baseline:insight('模拟对照：原有方式只展示局部笔记。'),change:insight('模拟改动：增加有来源的整体解释。'),rationale:null,support:insight('模拟依据仅用于界面测试，不是效果实验。')}],
   reading_route:[{priority:'first',section:'方法与附录',why:'模拟数据：先核对输入输出和设定，再判断实验支持范围。',questions:['方法假设是什么？','实验是否覆盖这些假设？'],prerequisites:['基础机器学习概念'],evidence_ids:['e2']}],
   conclusion:insight('读者应掌握问题、方法与证据的联系。'),limitations:insight('图表、公式和实验均未独立核验。'),open_questions:['模拟夹具不能证明科研效果。'],reading_unknowns:['模拟数据：跨批公式尚未核验。'],evidence,semantic_verified:false};
 const panel=page.getByRole('region',{name:'全文解读报告'});
 await expect(page.locator('.evidence-reader')).not.toBeVisible();
 await expect(panel.getByRole('button',{name:'分析全文，生成解读报告'})).toBeDisabled();
 await panel.getByRole('checkbox',{name:/我同意以上范围/}).check();
 await panel.getByRole('button',{name:'分析全文，生成解读报告'}).click();
 await expect(panel.getByRole('heading',{name:'《模拟数据：用于演示解读流程的测试论文》',exact:true})).toBeVisible();
 await expect(panel.getByText('——模拟数据：这段是题名识别说明，不属于论文题目。',{exact:true})).not.toBeVisible();
 await panel.getByText('题名补充说明',{exact:true}).click();
 await expect(panel.getByText('——模拟数据：这段是题名识别说明，不属于论文题目。',{exact:true})).toBeVisible();
 await panel.getByText('题名补充说明',{exact:true}).click();
 await expect(panel.getByRole('navigation',{name:'解读报告目录'})).toBeVisible();
 const structure=panel.locator('#paper-map');
 await expect(structure.getByRole('region',{name:'原文章节目录'})).toBeVisible();
 await expect(structure.getByRole('region',{name:'论文论证脉络'})).toBeVisible();
 await expect(structure).toContainText('自动候选');
 await structure.getByRole('button',{name:'论证脉络',exact:true}).click();
 await expect(structure.getByRole('region',{name:'原文章节目录'})).toHaveCount(0);
 await structure.getByRole('button',{name:'原文目录',exact:true}).click();
 await expect(structure.getByRole('region',{name:'论文论证脉络'})).toHaveCount(0);
 const sectionButton=structure.locator('.structure-heading').last();
 await expect(sectionButton).toBeVisible();await sectionButton.click();
 await structure.getByRole('button',{name:'并列查看',exact:true}).click();
 await expect(structure.locator('.structure-selection')).toContainText('已选章节');
 await structure.getByRole('button',{name:'清除选择',exact:true}).click();
 await expect(panel.locator('.contribution-comparison dt')).toHaveCount(4);
 await expect(panel.locator('.contribution-comparison')).toContainText('未提供充分材料');
 await expect(panel.locator('#paper-core')).toContainText('输入是论文提取文字');
 await expect(panel.locator('#paper-route')).toContainText('实验是否覆盖这些假设');
 await expect(panel.locator('#paper-innovation')).toContainText('未做真实质量评估');
 await expect(panel.locator('.report-process')).not.toHaveAttribute('open');
 const tail='模拟数据：完整解释保留这一尾段';
 const core=panel.locator('#paper-core');
 await expect(panel.getByRole('heading',{name:'研究主旨',exact:true})).toBeVisible();
 await expect(panel.getByText('一句话读懂',{exact:true})).toHaveCount(0);
 await expect(core.locator('mark.report-keyphrase')).toHaveText('研读笔记');
 expect(await core.locator('.report-prose').evaluate(el=>parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(17);
 await expect(core.locator('.report-prose')).not.toContainText(tail);
 await core.getByRole('button',{name:'节选 · 展开完整解读'}).click();
 await expect(core.locator('.report-prose')).toContainText(tail);
 await core.getByRole('button',{name:'收起完整解读'}).click();
 await expect(core.locator('.report-prose')).not.toContainText(tail);
 await panel.getByRole('button',{name:'完整展开',exact:true}).click();
 await expect(core.locator('.report-prose')).toContainText(tail);
 await expect(panel.locator('#paper-map .report-detail')).toHaveAttribute('open');
 await panel.getByRole('button',{name:'精简阅读',exact:true}).click();
 await expect(core.locator('.report-prose')).not.toContainText(tail);
 await expect(panel.locator('#paper-map .report-detail')).not.toHaveAttribute('open');
 const href=await panel.getByRole('link',{name:'导出解读与来源记录'}).getAttribute('href');
 const exported=JSON.parse(decodeURIComponent(href.slice(href.indexOf(',')+1)));expect(exported.semantic_verified).toBe(false);
 expect(exported.report.core_content[0].explanation.text).toBe('模拟数据：'+longExplanation);
 await panel.getByText('各批阅读保留的未知项（未经综合删减）',{exact:true}).click();
 await expect(panel.locator('#paper-conclusion')).toContainText('跨批公式尚未核验');
 const gallery=panel.getByRole('region',{name:'原文图表',exact:true});
 await expect(gallery.locator('.visual-image-button').first()).toBeVisible();
 await gallery.locator('.visual-image-button').first().click();
 const visualDialog=page.getByRole('dialog',{name:'原文图表大图'});
 await expect(visualDialog).toBeVisible();
 await visualDialog.getByRole('button',{name:'放大细节',exact:true}).click();
 await expect(visualDialog.locator('.visual-large-scroll')).toHaveClass(/is-zoomed/);
 await visualDialog.getByRole('button',{name:'适应窗口',exact:true}).click();
 await visualDialog.getByRole('button',{name:'查看整页',exact:true}).click();
 await expect(visualDialog.locator('img')).toHaveAttribute('src',/\/pages\/\d+\/image\?scale=2/);
 await page.keyboard.press('Escape');await expect(visualDialog).not.toBeVisible();
 await gallery.locator('.visual-image-button').first().click();
 await visualDialog.getByRole('button',{name:'回到原文页面',exact:true}).click();
 await expect(page.locator('.evidence-reader')).toBeVisible();
 await panel.screenshot({path:'test-results/whole-paper-report-desktop.png'});
 await panel.locator('#paper-route').getByRole('button',{name:/PDF.*原文依据/}).click();
 await expect(page.getByLabel('当前证据区域')).toBeVisible();
 await expect(page.locator('.evidence-selection')).toContainText(last.text.slice(0,35));
 // Sticky panels must stop with the report row, not float over full-width tools.
 for(const height of [900,480]){
  await page.setViewportSize({width:1280,height});
  await page.locator('.reading-prep').evaluate(el=>el.scrollIntoView({block:'start',behavior:'instant'}));
  const positions=await page.evaluate(()=>{
   const bounds=selector=>{const r=document.querySelector(selector).getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height};};
   return {library:bounds('.evidence-library'),nav:bounds('.report-navigation'),tools:bounds('.source-tools')};
  });
  expect(positions.library.bottom).toBeLessThanOrEqual(positions.tools.top+1);
  expect(positions.nav.bottom).toBeLessThanOrEqual(positions.tools.top+1);
  expect(positions.library.height).toBeLessThanOrEqual(height-40+1);
 }
 await page.reload();await page.locator('.evidence-document[title="reading-sample.pdf"]').filter({hasText:'主论文'}).click();
 await expect(panel.getByRole('heading',{name:'《模拟数据：用于演示解读流程的测试论文》',exact:true})).toBeVisible();
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 await panel.locator('#paper-route').screenshot({path:'test-results/whole-paper-reading-route-mobile.png'});
 // The old model title is deliberately wrong. Local source locators provide
 // the visible title without rewriting the exported historical model output.
 run.title_identity=localIdentity;
 run.report={...run.report,title:{text:'未能从所提供材料中识别原文题名',evidence_ids:['e1']}};
 await page.reload();
 await page.locator('.evidence-document[title="reading-sample.pdf"]').filter({hasText:'主论文'}).click();
 await expect(panel.locator('#paper-identity h3')).toHaveText(localIdentity.text);
 await expect(panel.locator('#paper-identity')).not.toContainText('未能从所提供材料中识别原文题名');
 await panel.getByRole('button',{name:/题名原文/}).first().click();
 await expect(page.locator('.evidence-selection')).toContainText(localIdentity.sources[0].quote.slice(0,35));
 const localHref=await panel.getByRole('link',{name:'导出解读与来源记录'}).getAttribute('href');
 const localExport=JSON.parse(decodeURIComponent(localHref.slice(localHref.indexOf(',')+1)));
 expect(localExport.report.title.text).toBe('未能从所提供材料中识别原文题名');
 expect(localExport.title_identity.status).toBe('source_located');
 run.title_identity={text:'模拟数据：文件名备用题名',status:'filename_only',sources:[],note:'暂按文件名显示，尚未与原文题名核对。'};
 await page.reload();
 await page.locator('.evidence-document[title="reading-sample.pdf"]').filter({hasText:'主论文'}).click();
 await expect(panel.locator('#paper-identity h3')).toHaveText('模拟数据：文件名备用题名');
 await expect(panel.locator('#paper-identity')).toContainText('暂按文件名显示');
 await expect(panel.locator('#paper-identity > .report-evidence')).toHaveCount(0);
 run.report={...run.report,open_questions:['模拟数据：作者未给出公式？','模拟数据：这个样本范围为什么重要？']};
 run.support_review={notice:'模拟数据：仅位置校验，语义未经独立验证。',entries:[
  {target:'conclusion',text:run.report.conclusion.text,status:'model_supported',kind:'explanation',reason:'模拟数据：保留限定条件。',locations:[evidence[0]],candidate_sources:[]},
  {target:'open_questions.0',text:run.report.open_questions[0],status:'needs_check',kind:'criticism',reason:'模拟数据：缺失性断言尚无充分依据。',absence_or_conflict_language:true,locations:[evidence[0]],candidate_sources:[]},
  {target:'open_questions.1',text:run.report.open_questions[1],status:'model_supported',kind:'reading_question',reason:'模拟数据：与限定样本直接相关。',locations:[evidence[1]],candidate_sources:[]}
 ]};
 await page.reload();
 await page.locator('.evidence-document[title="reading-sample.pdf"]').filter({hasText:'主论文'}).click();
 await panel.getByText('待讨论问题',{exact:true}).click();
 await expect(panel.locator('.report-discussion li').filter({hasText:'模拟数据：这个样本范围为什么重要？'})).toBeVisible();
 await expect(panel.getByText('模拟数据：作者未给出公式？',{exact:true}).first()).not.toBeVisible();
 await panel.getByText('待核对或暂不采纳的疑问 · 1 项',{exact:true}).click();
 await expect(panel.locator('.report-pending-questions')).toContainText('尚不能据此认定论文存在缺陷');
 await panel.locator('.report-pending-questions').getByRole('button',{name:/核查原文/}).click();
 await expect(page.locator('.evidence-selection')).toContainText(evidence[0].quote.slice(0,35));
 await panel.getByText('关键结论与疑问的支持核查',{exact:true}).click();
 await expect(panel.locator('.report-support')).toContainText('同次生成的模型自查，尚无独立审阅');
 expect(errors).toEqual([]);
});

test('failed reading requires explicit budget confirmation and retains checkpoints',async({page})=>{
 let run={id:'reading_budget_fixture',created_at:'模拟数据',revision:4,state:'failed',max_calls:11,calls_reserved:3,
  budget_extension_available:true,report_continuation:{additional_calls:9,new_limit:12,remaining_reading:6},
  progress:{completed:2,total:11},coverage:{total_pages:11,text_pages:[1,2,3,4,5],page_gaps:[]},
  error:{code:'report_reading_evidence_missing',message:'模拟数据：表格已读片段缺少引用。'},
  tasks:[...Array.from({length:8},(_,i)=>({id:'read_'+i,kind:'reading',index:i,pages:[i+1],state:i<2?'succeeded':i===2?'failed':'pending'})),...['overview','core','guidance'].map(part=>({id:'synthesis_'+part,kind:'synthesis',part,state:'pending'}))]};
 const calls=[];
 await page.route('**/reports',route=>route.fulfill({json:{items:[run]}}));
 await page.route('**/reports/reading_budget_fixture',route=>route.fulfill({json:run}));
 await page.route('**/reports/reading_budget_fixture/control',async route=>{
  const command=route.request().postDataJSON();calls.push(command);
  expect(command).toMatchObject({action:'extend_budget',accept_new_request:true,max_calls:12});
  run={...run,state:'running',revision:5,max_calls:12,budget_extension_available:false,error:null};
  await route.fulfill({json:run});
 });
 await page.goto('/');
 const uploaded=page.waitForResponse(r=>r.url().endsWith('/api/v2/documents')&&r.request().method()==='POST');
 await page.locator('input[type=file]').setInputFiles('tests/fixtures/reading-sample.pdf');await uploaded;
 await expect(page.getByRole('status')).toContainText('原始文件已登记并保存');
 const panel=page.getByRole('region',{name:'全文解读报告'});
 await expect(panel).toContainText('其中还有 6 批阅读');
 await expect(panel.getByRole('button',{name:'重试未完成步骤'})).toHaveCount(0);
 const button=panel.getByRole('button',{name:'保留已读内容，继续未完成步骤'});
 await expect(button).toBeDisabled();expect(calls).toEqual([]);
 await panel.getByRole('checkbox',{name:'我同意未完成步骤的请求及新的上限'}).check();
 await button.click();await expect(panel).toContainText('请求 3/12');
 expect(calls).toHaveLength(1);expect(run.tasks.filter(t=>t.state==='succeeded').map(t=>t.id)).toEqual(['read_0','read_1']);
});

test('old empty-content failure directs a fresh bounded plan without automatically retrying',async({page})=>{
 const old={id:'legacy_failed_fixture',created_at:'模拟数据',revision:5,state:'failed',model_name:'fixture-only',max_calls:7,calls_reserved:2,
   requires_replan:true,progress:{completed:0,total:5},coverage:{total_pages:12,text_pages:[1,2],page_gaps:[]},
   tasks:[{id:'read_0',kind:'reading',index:0,pages:[1,2],state:'failed'}],
   error:{code:'report_output_truncated',message:'模拟数据：上次请求已用满输出token上限，回复被截断，请按修复后的较小批次重新规划。'},
   last_failure:{http_status:200,finish_reason:'length',content_characters:0,usage:{completion_tokens:6000}}};
 let outbound=0;
 page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/reports'))outbound++;});
 await page.route('**/reports',route=>route.fulfill({json:{items:[old]}}));
 await page.route('**/report-plan',async route=>{const r=await route.fetch(),p=await r.json();p.model={configured:true,model_name:'fixture-only'};p.calls_needed=10;p.recommended_max_calls=32;p.chunks=9;p.parsed=true;p.total_pages=12;p.text_pages=[1,2];await route.fulfill({json:p});});
 await page.goto('/');
 const pending=page.waitForResponse(r=>r.url().endsWith('/api/v2/documents')&&r.request().method()==='POST');
 await page.locator('input[type=file]').setInputFiles('tests/fixtures/reading-sample.pdf');await pending;
 await expect(page.getByRole('status')).toContainText('原始文件已登记并保存');
 const panel=page.getByRole('region',{name:'全文解读报告'});
 await expect(panel).toContainText('上次任务采用旧版大批次');
 await expect(panel).toContainText('已报告输出 6000 token');
 await expect(panel.getByRole('button',{name:'重试未完成步骤'})).toHaveCount(0);
 const button=panel.getByRole('button',{name:'按新方案分析全文，生成报告'});
 await expect(button).toBeDisabled();
 await panel.getByText('高级设置：请求预算',{exact:true}).click();
 await expect(panel.getByRole('spinbutton',{name:'全文解读请求上限'})).toHaveValue('32');
 await panel.getByRole('checkbox',{name:/我同意以上范围/}).check();await expect(button).toBeEnabled();
 expect(outbound).toBe(0);
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 await panel.screenshot({path:'test-results/report-failure-replan-mobile.png'});
});

test('long-paper failure offers one-click completion with a visible full budget',async({page})=>{
 let run={id:'long_context_fixture',created_at:'模拟数据',revision:5,state:'failed',max_calls:17,calls_reserved:12,
  recommended_completion_limit:36,requires_replan:false,progress:{completed:12,total:15},
  coverage:{total_pages:22,text_pages:Array.from({length:22},(_,i)=>i+1),page_gaps:[]},
  tasks:[...Array.from({length:12},(_,i)=>({id:'read_'+i,kind:'reading',index:i,pages:[i+1],state:'succeeded'})),...['overview','core','guidance'].map(part=>({id:'synthesis_'+part,kind:'synthesis',part,state:'pending'}))],
  error:{code:'report_context_too_large',message:'模拟数据：笔记已保留，综合上下文过长。'}};
 const actions=[];
 await page.route('**/reports',route=>route.fulfill({json:{items:[run]}}));
 await page.route('**/reports/long_context_fixture',route=>route.fulfill({json:run}));
 await page.route('**/reports/long_context_fixture/control',async route=>{
  const command=route.request().postDataJSON();actions.push(command);
  expect(command).toMatchObject({action:'retry',automatic_recovery:true,accept_new_request:true,max_calls:36});
  run={...run,state:'running',automatic_recovery:true,max_calls:36,error:null,revision:6};
  await route.fulfill({json:run});
 });
 await page.goto('/');
 const uploaded=page.waitForResponse(r=>r.url().endsWith('/api/v2/documents')&&r.request().method()==='POST');
 await page.locator('input[type=file]').setInputFiles('tests/fixtures/reading-sample.pdf');await uploaded;
 const panel=page.getByRole('region',{name:'全文解读报告'});
 await expect(panel).toContainText('累计请求预算最多 36 次');
 await expect(panel.getByRole('button',{name:'重试未完成步骤'})).toHaveCount(0);
 await expect(panel.getByRole('button',{name:'分析全文，生成解读报告'})).toHaveCount(0);
 await panel.getByRole('button',{name:'继续完成解读（保留已读）',exact:true}).click();
 await expect(panel).toContainText('请求 12/36');
 await expect(panel).toContainText('自动完成已开启');
 expect(actions).toHaveLength(1);
 expect(run.tasks.filter(t=>t.kind==='reading'&&t.state==='succeeded')).toHaveLength(12);
});

test('retained candidate offers local revalidation before any paid retry',async({page})=>{
 let current={id:'retained_candidate_fixture',created_at:'模拟数据',revision:4,state:'failed',model_name:'fixture-only',max_calls:11,calls_reserved:1,
  candidate_available:true,requires_replan:false,progress:{completed:0,total:2},coverage:{total_pages:2,text_pages:[1,2],page_gaps:[]},
  tasks:[{id:'read_0',kind:'reading',index:0,pages:[1,2],state:'failed'},{id:'synthesis',kind:'synthesis',state:'pending'}],
  error:{code:'report_quote_invalid',message:'模拟数据：引文定位未通过，已保留待核验回复。'}};
 const actions=[];
 await page.route('**/reports',route=>route.fulfill({json:{items:[current]}}));
 await page.route('**/reports/retained_candidate_fixture/control',async route=>{
  const data=route.request().postDataJSON();actions.push(data.action);expect(data.action).toBe('revalidate');
  current={...current,revision:5,state:'paused',candidate_available:false,error:null,progress:{completed:1,total:2}};
  current.tasks[0].state='succeeded';await route.fulfill({json:current});
 });
 await page.goto('/');
 const uploaded=page.waitForResponse(r=>r.url().endsWith('/api/v2/documents')&&r.request().method()==='POST');
 await page.locator('input[type=file]').setInputFiles('tests/fixtures/reading-sample.pdf');await uploaded;
 await expect(page.getByRole('status')).toContainText('原始文件已登记并保存');
 const panel=page.getByRole('region',{name:'全文解读报告'});
 await expect(panel).toContainText('可先本地重新校验，不调用模型');
 await panel.getByRole('button',{name:'本地重新校验（不调用模型）',exact:true}).click();
 await expect(panel.getByRole('button',{name:'继续全文解读'})).toBeVisible();
 await expect(panel).toContainText('请求 1/11');
 expect(actions).toEqual(['revalidate']);
});

test('synthesis recovery preserves reading notes and requires explicit extra-call budget',async({page})=>{
 const reads=Array.from({length:8},(_,i)=>({id:`read_${i}`,kind:'reading',index:i,pages:[i+1],state:'succeeded',
  output:{overview:'模拟数据：已经保存的全文研读笔记。',topics:[{heading:'模拟数据：方法机制',explanation:'已经读到的方法解释无需重新生成。',method_details:[],findings:[],author_contributions:[],limitations:[],reading_focus:[],evidence_ids:[]}],unknowns:[],evidence:[]}}));
 let run={id:'synthesis_failure_fixture',created_at:'模拟数据',revision:10,state:'failed',max_calls:11,calls_reserved:10,requires_replan:false,split_synthesis_available:true,
  synthesis_recovery:{additional_calls:3,old_limit:11,minimum_new_limit:13},progress:{completed:8,total:9},coverage:{total_pages:12,text_pages:Array.from({length:12},(_,i)=>i+1),page_gaps:[]},
  tasks:[...reads,{id:'synthesis',kind:'synthesis',state:'failed'}],error:{code:'report_output_truncated',message:'模拟数据：全文笔记已保存，综合输出截断。'}};
 const actions=[];
 await page.route('**/reports',route=>route.fulfill({json:{items:[run]}}));
 await page.route('**/reports/synthesis_failure_fixture',route=>route.fulfill({json:run}));
 await page.route('**/reports/synthesis_failure_fixture/control',async route=>{
  const command=route.request().postDataJSON();actions.push(command);
  expect(command.action).toBe('split_synthesis');expect(command.accept_new_request).toBe(true);expect(command.max_calls).toBe(13);
  run={...run,state:'running',split_synthesis_available:false,max_calls:13,revision:11,progress:{completed:8,total:11},error:null,
   tasks:[...reads,...['overview','core','guidance'].map(part=>({id:'synthesis_'+part,kind:'synthesis',part,state:'pending'}))]};
  await route.fulfill({json:run});
 });
 await page.goto('/');
 const uploaded=page.waitForResponse(r=>r.url().endsWith('/api/v2/documents')&&r.request().method()==='POST');
 await page.locator('input[type=file]').setInputFiles('tests/fixtures/reading-sample.pdf');await uploaded;
 await expect(page.getByRole('status')).toContainText('原始文件已登记并保存');
 const panel=page.getByRole('region',{name:'全文解读报告'});
 await expect(panel).toContainText('保留已保存的 8 批笔记');
 await expect(panel.getByRole('button',{name:'重试未完成步骤'})).toHaveCount(0);
 await panel.getByText('查看已保存的研读笔记（不调用模型）',{exact:true}).click();
 await expect(panel.getByText('已经读到的方法解释无需重新生成。').first()).toBeVisible();
 expect(actions).toEqual([]);
 const button=panel.getByRole('button',{name:'保留已有研读，分段生成报告'});await expect(button).toBeDisabled();
 await panel.getByRole('checkbox',{name:'我同意上述3段综合请求及新的任务上限'}).check();
 await button.click();await expect(panel).toContainText('请求 10/13');
 expect(actions).toHaveLength(1);expect(run.tasks.filter(t=>t.kind==='reading'&&t.state==='succeeded')).toHaveLength(8);
});
