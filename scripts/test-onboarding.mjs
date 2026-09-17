/** Full first-run flow against an isolated bundled runtime; never uses school accounts. */
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium} from '../integrations/kzkt-browser/node_modules/playwright-core/index.mjs';
const root=resolve(fileURLToPath(new URL('..',import.meta.url)));
const data=mkdtempSync(join(tmpdir(),'sufe-onboarding-test-'));
const runtime=resolve(process.env.SUFE_RUNTIME_DIR||join(root,'runtime'));
const host=spawn(join(runtime,'python/python.exe'),['-B',join(root,'scripts/desktop-host.py')],{cwd:root,windowsHide:true,env:{...process.env,SUFE_DATA_DIR:data,SUFE_RUNTIME_DIR:runtime},stdio:['pipe','pipe','pipe']});
let browser, page;
try{
 const ready=await new Promise((ok,no)=>{let buffer='';const timer=setTimeout(()=>no(Error('Host startup timeout')),100000);host.once('exit',()=>{clearTimeout(timer);no(Error('Host exited before ready'))});host.stdout.on('data',chunk=>{buffer+=chunk;const line=buffer.split('\n')[0];try{const value=JSON.parse(line);clearTimeout(timer);if(value.error)no(Error(value.error));else ok(value)}catch{}})});
 browser=await chromium.launch({headless:true,...(process.env.SUFE_TEST_BROWSER?{executablePath:process.env.SUFE_TEST_BROWSER}:{})});
 page=await browser.newPage({viewport:{width:1280,height:960}});
 page.on('request',r=>{if(process.env.SUFE_TEST_DEBUG&&r.url().endsWith('/api/onboarding')&&r.method()==='PUT'){const v=r.postDataJSON();console.log('SAVE',v.step,v.draft.term_name,v.draft.rows.length)}});
 page.on('response',async r=>{if(process.env.SUFE_TEST_DEBUG&&r.url().endsWith('/api/onboarding')&&r.request().method()==='GET'){const v=await r.json();console.log('READ',v.step,v.draft?.term_name,v.draft?.rows?.length)}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(ready.url);
 const button=name=>page.getByRole('button',{name,exact:true});
 await page.getByRole('heading',{name:'欢迎与检查',exact:true}).waitFor();
 await button('下一步').click();
 await page.getByRole('heading',{name:'资料目录',exact:true}).waitFor();
 await button('检查空间与写入权限').click();
 await page.getByText(/可写入 · 剩余/).waitFor();
 await button('下一步').click();
 await page.getByLabel('学期名称',{exact:true}).fill('虚构引导验收学期');
 await page.getByLabel('第一教学周的周一',{exact:true}).fill('2026-08-31');
 await button('添加课程').click();
 await page.getByLabel('课程编号',{exact:true}).fill('DEMO202');
 await page.getByLabel('课程名称',{exact:true}).fill('虚构引导课程');
 await button('单周').click();
 await page.getByText('草稿已保存到本机',{exact:true}).waitFor();
 // Wait for last edit to reach disk, not just an earlier successful save.
 let saved=false;
 for(let attempt=0;attempt<100;attempt++){
   saved=await page.evaluate(async()=>{const r=await fetch('/api/onboarding');const v=await r.json();return v.step===2&&v.draft.rows?.[0]?.name==='虚构引导课程'&&v.draft.rows?.[0]?.weeks==='1,3,5,7,9,11,13,15,17'});
   if(saved)break;
   await page.waitForTimeout(100);
 }
 assert.equal(saved,true,'Latest draft must be saved before reload');
 await page.reload();
 await page.getByRole('heading',{name:'学期与课表',exact:true}).waitFor();
 assert.equal(await page.getByLabel('课程名称',{exact:true}).inputValue(),'虚构引导课程');
 assert.equal(await button('已核对，确认导入').isDisabled(),true);
 await button('校验并预览').click();
 await button('已核对，确认导入').waitFor();
 assert.equal(await page.evaluate(async()=>{const r=await fetch('/api/academics/courses');return(await r.json()).length}),0);
 await button('已核对，确认导入').click();
 await page.getByText(/已导入，新增 9 节课/).waitFor();
 await button('保存学期并继续').click();
 await button('继续（连接可跳过）').click();
 assert.equal(await page.getByLabel('AI 提供方',{exact:true}).inputValue(),'none');
 await page.getByLabel('AI 提供方',{exact:true}).selectOption('deepseek');
 assert.equal(await page.getByLabel('API 服务地址',{exact:true}).inputValue(),'https://api.deepseek.com');
 await page.getByLabel('看图服务来源',{exact:true}).selectOption('separate');
 await page.getByLabel('看图服务提供方',{exact:true}).selectOption('custom');
 assert.equal(await page.getByLabel('API Key',{exact:true}).count(),2);
 await page.getByLabel('API Key',{exact:true}).first().fill('fictional-secret');
 await page.getByLabel('API 服务地址',{exact:true}).first().fill('https://different.example/v1');
 assert.equal(await page.getByLabel('API Key',{exact:true}).first().inputValue(),'');
 await page.getByLabel('看图服务来源',{exact:true}).selectOption('reuse');
 await page.getByLabel('AI 提供方',{exact:true}).selectOption('none');
 await page.getByRole('radio',{name:/图文模式/}).check();
 assert.equal(await page.getByRole('radio',{name:/文本模式/}).isChecked(),false);
 if(process.env.SUFE_TEST_SCREENSHOTS){mkdirSync(process.env.SUFE_TEST_SCREENSHOTS,{recursive:true});await page.screenshot({path:join(process.env.SUFE_TEST_SCREENSHOTS,'learning-options.png'),fullPage:true});}
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.setViewportSize({width:1280,height:960});
 await button('保存学习设置并继续').click();
 await page.getByRole('heading',{name:'你的工作台已准备好',exact:true}).waitFor();
 assert.equal(await button('启动 Canvas 同步').isDisabled(),true);
 assert.equal(await page.evaluate(async()=>{const r=await fetch('/api/setup');return(await r.json()).settings.replay_mode}),'illustrated');
 const output=process.env.SUFE_TEST_SCREENSHOTS;
 if(output){mkdirSync(output,{recursive:true});await page.screenshot({path:join(output,'onboarding-summary.png'),fullPage:true});await page.setViewportSize({width:390,height:844});await page.screenshot({path:join(output,'onboarding-mobile.png'),fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.setViewportSize({width:1280,height:960})}
 await button('进入工作台').click();
 await page.getByRole('heading',{name:'课程',exact:true}).waitFor();
 await page.reload();
 await page.getByRole('heading',{name:'课程',exact:true}).waitFor();
 await button('设置').click();await button('重新打开设置向导').click();
 await page.getByRole('heading',{name:'准备完成',exact:true}).waitFor();
 assert.deepEqual(errors,[]);
 console.log('PASS: six-step setup, autosave/reload, editable draft, odd weeks, confirmation-only import, optional connections and AI, completion/re-entry, mobile layout.');
}catch(error){if(page){console.error((await page.locator('body').innerText()).slice(0,5000));console.error(await page.evaluate(async()=>{const r=await fetch('/api/onboarding');return r.json()}))}throw error;}finally{
 await browser?.close();
 host.stdin.end('stop\n');
 await new Promise((ok,no)=>{if(host.exitCode!==null)return ok();const timer=setTimeout(()=>{host.kill();no(Error('Host shutdown timeout'))},30000);host.once('exit',()=>{clearTimeout(timer);ok()})});
}
