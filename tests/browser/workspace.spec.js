import {test,expect} from '@playwright/test';

test('sample comparison, evidence modal, question and mobile layout', async({page})=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/?workspace=legacy');
  await expect(page.getByRole('heading',{name:'研究资料'})).toBeVisible();
  await page.getByRole('button',{name:'体验示例',exact:true}).click();
  await expect(page.getByText('模拟示例',{exact:true})).toBeVisible();
  await expect(page.locator('tbody tr')).toHaveCount(5);
  await page.getByRole('button',{name:'第 1 页'}).first().click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('dialog')).toContainText('不来自真实文献');
  await page.getByRole('button',{name:'关闭证据'}).click();
  await page.getByRole('button',{name:'证据问答',exact:true}).last().click();
  await page.getByRole('textbox',{name:'文献问题'}).fill('这些研究的分数能直接比较吗？');
  await page.getByRole('button',{name:'查找证据'}).click();
  await expect(page.getByText('模拟回答',{exact:true})).toBeVisible();
  await expect(page.locator('.answer')).toContainText('不能根据分数直接判断');
  await page.getByRole('button',{name:'结构化对比',exact:true}).click();
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:'test-results/workspace-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole('heading',{name:'研究资料'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:'test-results/workspace-mobile.png',fullPage:true});
  expect(errors).toEqual([]);
});

test('real PDF upload, retrieval, source and deletion',async({page,request})=>{
  await page.goto('/?workspace=legacy');
  await page.locator('input[type=file]').setInputFiles('tests/fixtures/retrieval-sample.pdf');
  await expect(page.getByText('成功解析 1 篇论文，页码已保留。')).toBeVisible();
  const paper=page.locator('.paper').filter({hasText:'EvidenceMap automated fixture'});
  await expect(paper).toBeVisible();
  const status=await (await request.get('/api/status')).json();
  if(!status.model_configured){await expect(page.getByRole('button',{name:'生成对比'})).toBeDisabled();}
  await page.getByRole('button',{name:'证据问答',exact:true}).last().click();
  await page.getByRole('textbox',{name:'文献问题'}).fill('SyntheticQA dataset');
  await page.getByRole('button',{name:'查找证据'}).click();
  await expect(page.locator('.answer-sources button').first()).toBeVisible();
  await page.locator('.answer-sources button').first().click();
  await expect(page.getByRole('dialog')).toContainText('SyntheticQA');
  const link=page.getByRole('link',{name:'打开 PDF 对应页面'});
  await expect(link).toHaveAttribute('href',/\/pdf#page=2$/);
  const href=await link.getAttribute('href');
  const response=await request.get(href.split('#')[0]);expect(response.status()).toBe(200);
  expect(response.headers()['content-type']).toContain('application/pdf');
  await page.getByRole('button',{name:'关闭证据'}).click();
  await paper.getByRole('button',{name:/删除/}).click();
  await expect(paper).toHaveCount(0);
});
