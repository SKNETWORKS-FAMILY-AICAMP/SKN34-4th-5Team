// Observed DOM only. CDP uses upstream transport, never the model/browser agent.
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { readFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { CdpSocket } from './jev/src/cdp/socket.ts';

const [url, profile] = process.argv.slice(2);
const chrome = spawn(process.env.CHROME_PATH || '/usr/bin/chromium', [
  '--headless', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
  ...(process.env.JEV_CHROME_ARGS || '').split(/\s+/).filter(Boolean), 'about:blank',
], { stdio: 'ignore' });
let socket;
const frames = [], limitations = [], contexts = new Map();
const extract = `(() => {
 const candidates = [...document.querySelectorAll('article,main,[role="main"],.se-main-container,#postViewArea,.entry-content,.article-view,.tt_article_useless_p_margin')];
 const root = candidates.sort((a,b) => b.innerText.length-a.innerText.length)[0] || document.body;
 const body = root?.innerText || '';
 const excluded = [...(root?.querySelectorAll('nav,header,footer,aside,[role="navigation"],.adsbygoogle') || [])];
 const styles = excluded.map(n=>[n,n.style.display]);
 excluded.forEach(n=>n.style.display='none');
 const text = root?.innerText || '';
 styles.forEach(([n,display])=>n.style.display=display);
 const links = [...document.querySelectorAll('iframe')].slice(0,24).map(n=>{
   const label=n.id+' '+n.name+' '+n.className+' '+n.title+' '+n.src;
   const auxiliary=!n.src || n.src==='about:blank' || /(?:^|[^a-z0-9])(?:ad[sx]?|advertisement|doubleclick|banner|twitter|facebook)(?:$|[^a-z0-9])/i.test(label);
   const material=!candidates.length || root?.contains(n) || /article|postview|mainframe/i.test(label);
   return {url:n.src,role:auxiliary?'auxiliary':/map/i.test(label)?'map':material?'article':'auxiliary'};
 });
 return {body:text.slice(0,2097153),url:location.href,title:document.title,article:!!candidates.length,links,ready:document.readyState};
})()`;
try {
  let port;
  for (let i=0;i<100;i++) {
    try { port=(await readFile(`${profile}/DevToolsActivePort`,'utf8')).split('\n')[0]; break; }
    catch { if(chrome.exitCode!==null) throw Error('error'); await delay(100); }
  }
  if(!port) throw Error('timeout');
  const info=await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  socket=await CdpSocket.connect(info.webSocketDebuggerUrl);
  socket.onEvent('Runtime.executionContextCreated',(p,s)=> {
    if(p.context.auxData?.isDefault) contexts.set(`${s}:${p.context.id}`,{session:s,id:p.context.id,frame:p.context.auxData.frameId});
  });
  socket.onEvent('Runtime.executionContextDestroyed',(p,s)=>contexts.delete(`${s}:${p.executionContextId}`));
  socket.onEvent('Runtime.executionContextsCleared',(_,s)=> {for(const [k,c] of contexts) if(c.session===s) contexts.delete(k);});
  const attached = new Set();
  async function attach(target) {
    if(attached.has(target.targetId)) return;
    attached.add(target.targetId);
    const {sessionId}=await socket.call('Target.attachToTarget',{targetId:target.targetId,flatten:true});
    await socket.call('Page.enable',{},sessionId);
    await socket.call('Runtime.enable',{},sessionId);
    return sessionId;
  }
  const {targetInfos}=await socket.call('Target.getTargets');
  const session=await attach(targetInfos.find(t=>t.type==='page'));
  await socket.call('Browser.setDownloadBehavior',{behavior:'deny'});
  const nav=await socket.call('Page.navigate',{url},session);
  if(nav.errorText) throw Error('error');
  for(let i=0;i<60;i++) {
    const ready=await socket.call('Runtime.evaluate',{expression:'document.readyState',returnByValue:true},session);
    if(ready.result?.value==='complete') break;
    if(i===59) limitations.push('document_load_timeout');
    await delay(250);
  }
  // Bounded lazy-content settling, not link traversal or viewport-only extraction.
  for(let pass=0;pass<6;pass++) {
    const targets=await socket.call('Target.getTargets');
    for(const t of targets.targetInfos.filter(t=>t.type==='iframe').slice(0,24)) {
      try { await attach(t); }
      catch { frames.push({role:'unknown',status:'error',url:t.url||'',title:''}); }
    }
    for(const c of [...contexts.values()].slice(0,24)) {
      await socket.call('Runtime.evaluate',{contextId:c.id,expression:'window.scrollBy(0,window.innerHeight)',returnByValue:true},c.session).catch(()=>{});
    }
    await delay(300);
  }
  const used=new Set(), expected=[], depths=new Map();
  function recordDepth(tree, depth=0) {
    if(!tree?.frame) return;
    depths.set(tree.frame.id,depth);
    for(const child of tree.childFrames||[]) recordDepth(child,depth+1);
  }
  const tree=await socket.call('Page.getFrameTree',{},session);
  recordDepth(tree.frameTree);
  for(const c of [...contexts.values()].slice(0,24)) {
    if((depths.get(c.frame)||0)>6) {limitations.push('frame_depth_limit');continue;}
    try {
      const response=await socket.call('Runtime.evaluate',{contextId:c.id,expression:extract,returnByValue:true},c.session);
      const data=response.result?.value;
      if(!data || typeof data.body!=='string') throw Error('invalid_context');
      expected.push(...data.links.map(link=>({...link,parent_url:data.url})));
      const role=data.article?'article':/map/i.test(data.url)?'map':c.frame===nav.frameId?'document':'auxiliary';
      const blocked=/^(?:\s*(?:access denied|403 forbidden|verify you are human|로봇이 아닙니다))/i.test(data.body) || /chrome-error:\/\//.test(data.url) || /^(ERROR: The requested URL could not be retrieved)/.test(data.title);
      frames.push({role,status:blocked?'blocked':'ok',url:data.url,title:data.title,body:blocked?'':data.body});
    } catch { frames.push({role:c.frame===nav.frameId?'document':'unknown',status:'error',url:''}); }
  }
  if(contexts.size>24) limitations.push('frame_limit');
  for(const frame of expected) {
    const matches=frames.filter(f=>f.url===frame.url || f.url.split('#')[0]===frame.url.split('#')[0]);
    const observed=matches.find(f=>f.status==='ok') || matches[0];
    if(observed && frame.role==='article' && observed.status==='ok') observed.role='article';
    if(observed && frame.role==='map' && observed.status==='ok') observed.role='map';
    const container=expected.some(child=>child.parent_url===frame.url && child.role==='article');
    if(frame.url && (!observed || observed.status!=='ok' || (frame.role==='article' && !observed.body && !container))) {
      frames.push({...frame,status:observed?.status==='blocked'?'blocked':'missing'});
      if(frame.role==='article') limitations.push('material_article_frame_missing');
    }
  }
  const articles=frames.filter(f=>f.role==='article' && f.status==='ok');
  const selected=frames.filter(f=>f.status==='ok' && typeof f.body==='string' && (f.role==='article'||f.role==='map'||(!articles.length && f.role==='document')));
  const body=selected.map(f=>f.body).filter(text=>{if(!text||used.has(text))return false;used.add(text);return true;}).join('\n\n');
  if(frames.some(f=>f.role==='document' && f.status==='error')) limitations.push('main_context_failed');
  const main=frames.find(f=>f.role==='document') || selected[0];
  if(selected.some(f=>f.body.length>2097152)) throw Error('overflow');
  if(Buffer.byteLength(body)>2097152) throw Error('overflow');
  if(!body && frames.some(f=>f.status==='blocked')) throw Error('blocked');
  if(!body) limitations.push('no_observed_body');
  const result={schema_version:1,extractor_version:'jev-dom-v1',status:limitations.length?'partial':'ok',requested_url:url,final_url:main?.url||url,source_url:main?.url||url,title:main?.title||'',body,source_kind:'rendered_dom_snapshot',collected_at:new Date().toISOString(),frames:frames.map(({body,...f})=>({...f,chars:body?.length||0})),limitations};
  if(Buffer.byteLength(JSON.stringify(result))>2097152) throw Error('overflow');
  console.log(JSON.stringify(result));
} catch(error) {
  console.log(JSON.stringify({status:/timed out/i.test(error.message)?'timeout':['timeout','overflow','blocked'].includes(error.message)?error.message:'error',source_url:url}));
} finally {
  socket?.close();
  if(chrome.exitCode===null) { const exited=once(chrome,'exit'); chrome.kill('SIGKILL'); await exited; }
}
