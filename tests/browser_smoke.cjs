/* Optional dev regression suite. Run with PLAYWRIGHT_MODULE and CHROMIUM_EXECUTABLE
 * when Playwright is not installed in the usual Node module path. No production data. */
const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert/strict');
const {spawn} = require('child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const repo = path.resolve(__dirname, '..');
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'result-viewer-browser-'));
const sid = 'sub-Test_ses-2026_01_02', other = 'sub-Test_ses-2026_01_03';
const checks = [], errors = [];
let browser, server, stderr = '', base;
function check(name, value) { assert.ok(value, name); checks.push(name); }
async function waitFor(fn) { for (let n=0;n<100;n++) { if (await fn()) return; await new Promise(r=>setTimeout(r,50)); } throw Error('Timed out waiting for state'); }
(async()=>{
  server = spawn(process.env.PYTHON || 'python3', ['-m','tests.browser_server',scratch], {cwd:repo, stdio:['ignore','pipe','pipe']});
  server.stderr.on('data',d=>{stderr += d;});
  base = await new Promise((resolve,reject)=>{let output='';const timeout=setTimeout(()=>reject(Error('Scratch server did not start: '+stderr)),10000);server.stdout.on('data',d=>{output+=d;try{const ready=JSON.parse(output.trim().split('\n')[0]);clearTimeout(timeout);resolve('http://127.0.0.1:'+ready.port);}catch{}});server.on('exit',c=>{clearTimeout(timeout);reject(Error('Scratch server exited '+c+': '+stderr));});});
  browser = await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
  const context = await browser.newContext({viewport:{width:1440,height:1000},colorScheme:'light'});
  const page = await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  const writes=[];page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/note'))writes.push(r.postDataJSON());});
  await page.goto(base+'/#s='+sid+'&u=1&v=raster');await page.locator('#title').filter({hasText:'Unit 1'}).waitFor();
  const note=page.locator('#note');
  const notebook=async(s=sid)=>(await (await context.request.get(base+'/api/notebook?sid='+s)).json()).notebook.units;
  const stored=async(unit,s=sid)=>(await notebook(s))[String(unit)]?.note||'';
  const saved=async()=>waitFor(async()=>!(await page.locator('#saveState').innerText()).includes('Saving')&&!(await page.locator('#saveState').innerText()).includes('Editing'));
  check('existing notes are collapsed by default',!await page.locator('#notePanel').isVisible()&&await page.locator('#noteToggle').innerText()==='Edit note');
  check('existing note preview remains visible',(await page.locator('#notePreview').innerText())==='Existing synthetic note');
  await page.locator('#noteToggle').focus();await page.keyboard.press('Space');
  check('native keyboard activation opens a named editor',await page.getByRole('textbox',{name:'Unit note',exact:true}).isVisible()&&await page.locator('#noteToggle').getAttribute('aria-expanded')==='true');
  await note.fill('Autosave survives reload');await waitFor(async()=>await stored(1)==='Autosave survives reload');await saved();
  check('existing debounced autosave writes the scratch notebook',true);
  await page.reload();await page.locator('#noteToggle').filter({hasText:'Edit note'}).waitFor();await page.locator('#noteToggle').click();
  check('saved note reloads from server',await note.inputValue()==='Autosave survives reload');
  await note.fill('Discard this unsent edit');await page.locator('#noteDiscard').click();
  check('discard closes and leaves the saved notebook unchanged',!await page.locator('#notePanel').isVisible()&&await stored(1)==='Autosave survives reload');
  await page.locator('#noteToggle').click();check('discard restores saved text',await note.inputValue()==='Autosave survives reload');
  let blocked=false, fail=false, release=null, terminal=false;
  await page.route('**/api/note',async route=>{if(terminal){terminal=false;return route.fulfill({status:403,contentType:'application/json',body:JSON.stringify({error:'Scratch permission denied'})});}if(fail)return route.abort('failed');if(blocked)await new Promise(r=>release=r);await route.continue();});
  blocked=true;await note.fill('Close keeps queued changes');await page.locator('#noteToggle').click();await waitFor(()=>!!release);
  check('close collapses safely with a queued draft',!await page.locator('#notePanel').isVisible()&&await page.locator('#noteToggle').innerText()==='Resume draft');
  await page.locator('#noteToggle').click();check('reopening retains queued text',await note.inputValue()==='Close keeps queued changes');
  check('discard cannot undo a queued save',await page.locator('#noteDiscard').isDisabled());
  await note.fill('New edit over an in-flight note');await page.locator('#noteDiscard').click();
  check('discarding newer unsent edits keeps the earlier queued text',(await page.locator('#notePreview').innerText())==='Close keeps queued changes');
  blocked=false;release();release=null;await waitFor(async()=>await stored(1)==='Close keeps queued changes');await saved();
  await page.locator('#noteToggle').click();blocked=true;await note.fill('Earlier revision acknowledged while typing');await waitFor(()=>!!release);await note.fill('Discard after earlier acknowledgement');blocked=false;release();release=null;await waitFor(async()=>await stored(1)==='Earlier revision acknowledged while typing');await page.locator('#noteDiscard').click();
  check('discard after an earlier acknowledgement does not resurrect a stale draft',await page.locator('#noteToggle').innerText()==='Edit note'&&await stored(1)==='Earlier revision acknowledged while typing');
  await page.locator('#noteToggle').click();await note.fill('Save button waits for persistence');await page.locator('#noteSaveClose').click();await waitFor(async()=>!await page.locator('#notePanel').isVisible());
  check('Save and close waits for the write',await stored(1)==='Save button waits for persistence');
  await page.locator('#next').click();check('empty unit offers Add note and stays collapsed',await page.locator('#title').innerText()==='Unit 2'&&await page.locator('#noteToggle').innerText()==='Add note'&&!await page.locator('#notePanel').isVisible());
  await page.locator('#noteToggle').click();await note.fill('Unit two only');await page.locator('#prev').click();await waitFor(async()=>await stored(2)==='Unit two only');
  check('unit changes flush to the original unit',await stored(1)==='Save button waits for persistence'&&await page.locator('#title').innerText()==='Unit 1');
  await page.locator('#noteToggle').click();await note.fill('First session only');await page.locator('#session').selectOption(other);await page.locator('#title').filter({hasText:'Unit 1'}).waitFor();
  await waitFor(async()=>await stored(1)==='First session only');check('session changes flush to original session without leaking',await stored(1,other)===''&&await page.locator('#noteToggle').innerText()==='Add note');
  await page.locator('#session').selectOption(sid);await page.locator('#noteToggle').filter({hasText:'Edit note'}).waitFor();await page.locator('#noteToggle').click();
  fail=true;await note.fill('Offline draft remains available');await page.locator('#noteToggle').click();await waitFor(async()=>(await page.locator('#saveState').innerText()).includes('Offline'));
  await page.locator('#next').click();check('old unit save state does not leak to another unit',!(await page.locator('#saveState').innerText()).includes('Offline'));
  await page.locator('#prev').click();await page.locator('#noteToggle').click();check('offline close and unit changes retain the right draft',await note.inputValue()==='Offline draft remains available');
  fail=false;await waitFor(async()=>await stored(1)==='Offline draft remains available');await saved();await page.locator('#noteToggle').click();
  terminal=true;await page.locator('#noteToggle').click();await note.fill('Retry after terminal rejection');await page.locator('#noteSaveClose').click();await waitFor(async()=>(await page.locator('#saveState').innerText()).includes('Not saved'));
  check('terminal save error keeps editor and text',await page.locator('#notePanel').isVisible()&&await note.inputValue()==='Retry after terminal rejection');
  await page.locator('#noteSaveClose').click();await waitFor(async()=>!await page.locator('#notePanel').isVisible());check('explicit save retries a rejected draft',await stored(1)==='Retry after terminal rejection');
  const beforeHelp=writes.length;await page.locator('#helpBtn').click();await page.keyboard.press('Tab');check('Help traps focus',await page.locator('#helpClose').evaluate(el=>el===document.activeElement));await page.keyboard.press('Escape');
  check('Help close restores focus without note writes',await page.locator('#helpBtn').evaluate(el=>el===document.activeElement)&&writes.length===beforeHelp);
  await page.keyboard.press('n');await note.fill('j k f 123 ? are note text');await note.press('j');check('navigation shortcuts stay inactive during typing',await page.locator('#title').innerText()==='Unit 1'&&(await note.inputValue()).endsWith('j'));await note.press('Escape');
  check('Escape closes and keeps changes',!await page.locator('#notePanel').isVisible());await waitFor(async()=>await stored(1)==='j k f 123 ? are note textj');await saved();
  check('figure alt text and scientific units remain',await page.locator('#img').getAttribute('alt')==='Raster · unit 1'&&(await page.locator('#badges').innerText()).includes('Hz')&&(await page.locator('#unitMeta').innerText()).includes('µm'));
  check('provenance disclosure remains',await page.locator('#filesBox').count()===1);
  const shotDir=process.env.SCREENSHOT_DIR;if(shotDir){fs.mkdirSync(shotDir,{recursive:true});await page.screenshot({path:path.join(shotDir,'desktop-scratch.png')});}
  for(const width of [390,320]){
    const ctx=await browser.newContext({viewport:{width,height:844},colorScheme:'light'}),phone=await ctx.newPage();phone.on('pageerror',e=>errors.push(e.message));phone.on('dialog',d=>d.accept());await phone.goto(base+'/#s='+sid+'&u=1');await phone.locator('#title').filter({hasText:'Unit 1'}).waitFor();
    check(width+'px collapsed layout has no page overflow',await phone.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)&&!await phone.locator('#notePanel').isVisible());
    check(width+'px navigation is one centered group',await phone.locator('.nav').evaluate(el=>{const r=el.getBoundingClientRect();return Math.abs(r.x+r.width/2-innerWidth/2)<2;}));
    check(width+'px electro_gui keeps its real button and handler',await phone.locator('#egMobile #egBtn').count()===1);
    if(shotDir&&width===390)await phone.screenshot({path:path.join(shotDir,'mobile-scratch.png'),fullPage:true});
    await phone.locator('#noteToggle').click();check(width+'px explicit activation reveals the labeled note',await phone.getByRole('textbox',{name:'Unit note',exact:true}).isVisible());await ctx.close();
  }
  check('no browser script errors',errors.length===0);
  check('all writes stayed within scratch sessions',writes.every(w=>[sid,other].includes(w.sid)&&[1,2,3,4].includes(w.unit)));
  console.log(JSON.stringify({passed:checks.length,checks,errors,scratch},null,2));
})().catch(e=>{console.error(e);console.error(JSON.stringify({passed:checks.length,checks,errors,serverError:stderr.slice(-1500)},null,2));process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(server)server.kill('SIGTERM');});
