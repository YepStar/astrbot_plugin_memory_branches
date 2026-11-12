import { renderGraph } from './graph.js?v=1.2.0';
import { importanceBand, timelineRows } from './memory-presentation.js?v=1.2.4';
import { summaryText, uniqueFacts, buildMemoryGraph } from './memory-detail.js?v=1.1.0';
const $=id=>document.getElementById(id);
const esc=text=>String(text??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date=t=>new Date(t*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
const statuses={active:'使用中',dormant:'休眠',archived:'已归档',superseded:'已被替代',expired:'已过期',forgotten:'已遗忘'};
const kinds={episodic:'事件',factual:'事实',relational:'关系',preference:'偏好',planned:'约定',unknown:'其他'};
let bridge, selected='', data={}, generation=0, memoryLimit=30, rollbackPlan, editing, polling, graphHandle, toastTimer;
let mutation=false, lastSignature='', detailGraphHandle, importanceSorted=false, styleToDelete;
function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,5000);}
function feedback(text,error=false){$('feedback').textContent=text;$('feedback').hidden=!text;$('feedback').classList.toggle('error',error);}
function unwrap(response){if(response?.status==='error')throw Error(response.message);return response?.status==='ok'?response.data:response;}
async function api(method,payload={}){
  if(bridge){return unwrap(await (method==='state'?bridge.apiGet('page/state',payload):bridge.apiPost('page/action',payload)));}
  if(location.hostname==='127.0.0.1'&&location.search.includes('demo=1')){
    const response=await fetch('/'+method+(method==='state'?'?'+new URLSearchParams(payload):''),{method:method==='state'?'GET':'POST',headers:{'Content-Type':'application/json'},body:method==='state'?undefined:JSON.stringify(payload)});
    return unwrap(await response.json());
  }
  throw Error('请从 AstrBot 插件面板打开本页。');
}
async function action(action,params={}){return api('action',{action,bank:selected,...params});}
function setMutation(value){mutation=value;$('summarize').disabled=value||!selected||data.state?.recovering||data.job?.status==='running';}
async function refresh({quiet=false}={}){
  const request=++generation,bank=selected;
  try{
    const next=await api('state',bank?{bank}:{});
    if(request!==generation||bank!==selected)return;
    data=next;
    if(!selected&&next.banks?.length){selected=next.banks[0].id;return refresh();}
    render();
    if(!quiet)if(!data.state?.recovering&&!data.job?.status?.includes('error'))feedback('');
    const job=data.job;
    if(job?.status==='running')feedback(job.message);
    else if(job?.status==='error')feedback(job.message,true);
    else if(data.state?.recovering)feedback('正在恢复未完成的回退，暂时停止写入。请检查插件日志并刷新。',true);
    if(job?.status==='running'||data.style_job==='running')schedulePoll();
  }catch(error){if(request===generation)feedback(error.message,true);}
}
function schedulePoll(){clearTimeout(polling);polling=setTimeout(()=>refresh({quiet:true}),1800);}
function render(){
  $('banks').innerHTML=(data.banks||[]).map(b=>`<button class="bank-button ${b.id===selected?'active':''}" data-bank="${esc(b.id)}"><span>◈ &nbsp;${esc(b.name)}</span><small>${b.memory_count} 条记忆 · ${b.pending_count} 条待总结</small></button>`).join('')||'<p class="muted">还没有记忆空间</p>';
  const s=data.state;if(!s){setMutation(mutation);return;}
  $('bank-name').textContent=s.name;
  $('route-label').textContent=s.routes.join(' / ');
  $('bank-description').textContent=`${s.message_count} 条聊天原文独立保存 · ${s.statuses.active||0} 条记忆使用中`;
  $('stat-memories').textContent=s.memory_count;$('stat-pending').textContent=s.pending_count;$('stat-nodes').textContent=s.nodes.length;
  $('stat-auto').textContent=s.settings.auto_summary?`${s.settings.trigger_rounds} 轮`:'已关闭';
  $('auto-detail').textContent=s.settings.auto_summary?'未总结对话达到阈值后触发':'仍可手动总结最新内容';
  renderMemories();renderStyles();
  // Do not overwrite in-progress settings edits during background polling.
  if(document.activeElement?.closest('#settings-form')==null){
    const form=$('settings-form');form.elements.name.value=s.name;
    for(const [key,value] of Object.entries(s.settings)){
      const input=form.elements.namedItem(key);if(!input)continue;
      if(input.type==='checkbox')input.checked=Boolean(value);else input.value=value;
    }
    form.elements.style_learning.checked=s.settings.style_learning!==false;
  }
  const signature=selected+':'+s.revision+':'+s.memory_count;
  if(signature!==lastSignature){lastSignature=signature;drawGraph();}
  setMutation(mutation);
}
function renderMemories(){
  const state=data.state||{};
  const rows=timelineRows(state.memories||[],state.nodes||[],{
    status:$('status-filter').value,query:$('memory-search').value,byImportance:importanceSorted,
    textOf:m=>summaryText(m)+' '+uniqueFacts(m).join(' '),
  });
  $('memory-list').innerHTML=rows.slice(0,memoryLimit).map(row=>{
    const n=row.node;
    const rollback=n?`<button type="button" class="text-danger" data-rollback="${esc(n.id)}" aria-label="删除节点 ${esc(n.id)} 并回退">删除并回退</button>`:'<span class="muted">无回退节点</span>';
    if(row.type==='checkpoint')return `<article class="memory-card checkpoint-card"><time>${date(n.created).replace(' ','<br>')}</time><div><p>${esc(n.title)}</p><div class="memory-meta"><span class="pill inactive">回退记录</span><span>节点 #${esc(n.id)}</span></div><small class="muted">本次总结未新增独立记忆，保留此处用于回退。</small></div><span class="importance-placeholder" aria-hidden="true">—</span><div class="memory-actions">${rollback}</div></article>`;
    const m=row.memory;
    return `<article class="memory-card"><time>${date(m.created).replace(' ','<br>')}</time><div class="memory-open" role="button" tabindex="0" data-memory="${esc(m.id)}" aria-label="查看记忆详情"><p>${esc(summaryText(m))}</p><div class="memory-meta"><span class="pill ${m.status==='active'?'':'inactive'}">${esc(statuses[m.status]||m.status)}</span><span>${esc(kinds[m.kind]||m.kind)}</span><span>节点 #${esc(m.node??'—')}</span>${n?`<span>${n.trigger==='migration'?'迁移基线':n.trigger==='manual'?'手动总结':'自动总结'}</span>`:''}</div></div><div class="importance importance-${importanceBand(m.importance)}" title="重要性 ${(m.importance*10).toFixed(1)} / 10">${(m.importance*10).toFixed(1)}<small>/10</small></div><div class="memory-actions">${rollback}</div></article>`;
  }).join('')||'<div class="empty">这里还没有匹配的记忆或回退记录</div>';
  $('load-more').hidden=rows.length<=memoryLimit;
}
function openMemory(id){
  const m=data.state?.memories.find(m=>m.id===id);if(!m)return;
  detailGraphHandle?.destroy();detailGraphHandle=null;
  editing=id;$('memory-content').textContent=summaryText(m);
  const facts=uniqueFacts(m);
  $('memory-facts-section').hidden=!facts.length;
  $('memory-facts').innerHTML='<ul>'+facts.map(f=>`<li>${esc(f)}</li>`).join('')+'</ul>';
  $('memory-edit').elements.importance.value=(m.importance*10).toFixed(1);$('memory-edit').elements.status.value=m.status;
  const graph=buildMemoryGraph(m,data.state.memories);
  $('memory-graph-section').hidden=!graph.edges.length;
  $('memory-graph-selection').textContent='';
  if(!$('memory-dialog').open)$('memory-dialog').showModal();
  if(graph.edges.length){
    $('memory-graph-description').textContent=`这条记忆与 ${graph.nodes.length-1-graph.related_memories} 个实体及 ${graph.related_memories} 条相关记忆相连。按可读字号铺开，拖动画布查看更多；点击节点查看。`;
    detailGraphHandle=renderGraph($('memory-graph-svg'),graph,node=>{
      if(node.memory_id && node.memory_id!==editing)openMemory(node.memory_id);
      else $('memory-graph-selection').textContent=node.label;
    });
  }
}
$('memory-dialog').addEventListener('cancel',event=>{event.preventDefault();$('memory-dialog').close();});
$('memory-dialog').addEventListener('close',()=>{detailGraphHandle?.destroy();detailGraphHandle=null;});
$('memory-graph-reset').onclick=()=>detailGraphHandle?.reset();
function drawGraph(){
  graphHandle?.destroy();
  graphHandle=renderGraph($('graph-svg'),data.graph||{nodes:[],edges:[]},node=>{
    const memory=node.memory_id&&data.state.memories.find(m=>m.id===node.memory_id);
    $('graph-detail').innerHTML=`<article class="result-card"><p>${esc((memory?summaryText(memory):node.label))}</p><small>${node.kind==='memory'?'记忆片段':'关联实体'}</small></article>`;
  });
  const stats=graphHandle.stats;
  $('graph-count').textContent=`显示 ${stats.shown_nodes} / ${stats.total_nodes} 个节点、${stats.shown_edges} 条去重后的可见连线（原始连线 ${stats.total_edges} 条）；覆盖 ${stats.shown_memories} / ${data.graph?.total_memories||0} 条使用中的记忆。节点显示上限为 700；悬停或聚焦节点可突出直接关联。`;
}
function renderStyles(){
  const styles=data.styles||{};$('style-enabled').checked=styles.enabled!==false;
  $('learn-style').disabled=data.style_job==='running';$('learn-style').textContent=data.style_job==='running'?'正在学习…':'学习最新表达';
  $('styles-list').innerHTML=[...(styles.patterns||[])].reverse().map(p=>`<article class="style-card"><div><p>${esc(p.description)}</p><blockquote>${esc(p.example)}</blockquote></div><div class="style-actions"><label class="switch-line"><input type="checkbox" data-style="${esc(p.id)}" ${p.enabled?'checked':''}>启用</label><button type="button" class="text-danger" data-delete-style="${esc(p.id)}">删除</button></div></article>`).join('')||'<div class="empty">还没有学习到说话习惯<small>新的用户发言会逐渐成为表达参考，不从助手的回复自我模仿。</small></div>';
}
async function mutate(callback){if(mutation)return;setMutation(true);try{await callback();}catch(error){feedback(error.message,true);}finally{setMutation(false);}}
$('banks').addEventListener('click',e=>{const button=e.target.closest('[data-bank]');if(!button||mutation)return;selected=button.dataset.bank;clearTimeout(polling);memoryLimit=30;$('recall-results').innerHTML='';$('graph-detail').textContent='';feedback('');refresh();});
$('refresh').onclick=()=>refresh();
$('new-bank').onclick=()=>$('create-dialog').showModal();
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
$('create-form').onsubmit=e=>{e.preventDefault();mutate(async()=>{const form=e.target;const result=await action('create',{name:form.elements.name.value,routes:form.elements.routes.value.split('\n').map(s=>s.trim()).filter(Boolean)});selected=result.bank;$('create-dialog').close();await refresh();toast('独立记忆空间已创建');});};
$('summarize').onclick=()=>mutate(async()=>{const result=await action('summarize');toast(result.message||'总结任务已开始');await refresh({quiet:true});});
document.querySelector('.tabs').onclick=e=>{const b=e.target.closest('[data-tab]');if(!b)return;document.querySelectorAll('[data-tab]').forEach(x=>x.classList.toggle('selected',x===b));document.querySelectorAll('.tab-panel').forEach(p=>p.hidden=p.id!==b.dataset.tab);if(b.dataset.tab==='graph')drawGraph();};
async function previewRollback(node){
  await mutate(async()=>{const bank=selected;const preview=await action('rollback-preview',{node:Number(node)});rollbackPlan={...preview,bank};$('rollback-message').textContent=`将删除 ${preview.removed_nodes} 个总结节点、${preview.removed_messages} 条聊天原文，回到“${preview.target_title}”。仅影响“${data.state.name}”。`;$('rollback-dialog').showModal();});
}
$('confirm-rollback').onclick=()=>mutate(async()=>{if(!rollbackPlan)return;const plan=rollbackPlan;await action('rollback',{bank:plan.bank,node:plan.node,revision:plan.revision});$('rollback-dialog').close();rollbackPlan=null;lastSignature='';await refresh();toast('已恢复到上一总结节点');});
$('memory-list').onclick=e=>{const button=e.target.closest('[data-rollback]');if(button){previewRollback(button.dataset.rollback);return;}const card=e.target.closest('[data-memory]');if(card)openMemory(card.dataset.memory);};
$('memory-list').onkeydown=e=>{if(e.key==='Enter'||e.key===' '){const card=e.target.closest('[data-memory]');if(card){e.preventDefault();openMemory(card.dataset.memory);}}};
$('importance-sort').onclick=()=>{importanceSorted=!importanceSorted;$('importance-sort').setAttribute('aria-pressed',String(importanceSorted));$('importance-sort').textContent=importanceSorted?'重要性 ↓ · 已排序':'重要性 ↓';memoryLimit=30;renderMemories();};
$('memory-search').oninput=()=>{memoryLimit=30;renderMemories();};$('status-filter').onchange=()=>{memoryLimit=30;renderMemories();};$('load-more').onclick=()=>{memoryLimit+=30;renderMemories();};
$('memory-edit').onsubmit=e=>{e.preventDefault();mutate(async()=>{await action('update-memory',{id:editing,importance:Number(e.target.elements.importance.value)/10,status:e.target.elements.status.value});$('memory-dialog').close();await refresh();toast('记忆状态已保存');});};
$('settings-form').onsubmit=e=>{e.preventDefault();mutate(async()=>{const form=e.target,settings={};for(const input of form.elements){if(!input.name||input.name==='name')continue;settings[input.name]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value;}await action('configure',{name:form.elements.name.value,settings});await refresh();toast('规则已保存');});};
$('recall-form').onsubmit=async e=>{e.preventDefault();const bank=selected,query=$('recall-query').value,button=e.target.querySelector('button');button.disabled=true;try{const result=await action('recall',{query});if(bank!==selected)return;$('recall-results').innerHTML=`<p class="muted">在 ${result.searched} 条使用中的记忆中找到 ${result.matches.length} 条相关片段；${result.excluded} 条其他状态的记忆未参与。相关分数用于排序，不代表答案真实程度。</p>`+(result.matches.length?result.matches.map((m,i)=>`<article class="result-card"><span class="tag">相关片段 ${i+1}</span><p style="margin-top:14px">${esc(summaryText(m))}</p><small>为什么想起：${esc(m.reason)}</small><div class="matched">${m.matched_words.map(w=>`<span class="pill">${esc(w)}</span>`).join('')}</div></article>`).join(''):'<div class="empty">这次没有想起相关片段<small>试着换成更具体的关键词，或先总结最新对话。已归档、休眠和过期的记忆不会被使用。</small></div>');}catch(error){feedback(error.message,true);}finally{button.disabled=false;}};
document.querySelectorAll('[data-query]').forEach(b=>b.onclick=()=>{$('recall-query').value=b.dataset.query;$('recall-query').focus();});
$('graph-reset').onclick=()=>graphHandle?.reset();
$('learn-style').onclick=()=>mutate(async()=>{await action('learn-style');toast('已开始学习最新表达');await refresh({quiet:true});});
$('style-enabled').onchange=()=>mutate(async()=>{await action('styles',{enabled:$('style-enabled').checked});await refresh();});
$('styles-list').onchange=e=>{if(e.target.dataset.style)mutate(async()=>{await action('styles',{id:e.target.dataset.style,pattern_enabled:e.target.checked});await refresh();});};
try{bridge=window.AstrBotPluginView||window.AstrBotPluginPage;if(bridge)await bridge.ready();await refresh();}catch(error){feedback(error.message,true);}

$('styles-list').onclick=e=>{
  const button=e.target.closest('[data-delete-style]');if(!button||mutation)return;
  const pattern=data.styles?.patterns?.find(p=>p.id===button.dataset.deleteStyle);if(!pattern)return;
  styleToDelete={bank:selected,id:pattern.id};$('style-delete-description').textContent=pattern.description;
  $('style-delete-dialog').showModal();
};
$('confirm-style-delete').onclick=()=>mutate(async()=>{
  if(!styleToDelete)return;const item=styleToDelete;
  await action('delete-style',item);$('style-delete-dialog').close();styleToDelete=null;
  await refresh({quiet:true});toast('已删除这项说话习惯');
});
