const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = (name, cls = '') => `<img src="/icons/${name}.svg" class="${cls}" alt="">`;
const labels = {native: '原生 Codex', orca: 'Orca 托管', both: '同时修改两套'};
const shortLabels = {native: '原生', orca: 'Orca'};
const builtinNames = new Set(['default', 'worker', 'explorer']);
const descriptions = {default:'通用代理 · 处理未指定角色的任务', worker:'执行与实现 · 编码、修复和生产工作', explorer:'代码探索 · 检索、调用链与架构梳理'};
const editable = ['model','model_reasoning_effort','sandbox_mode','description','developer_instructions'];
let state, scope = localStorage.getItem('codex-manager-scope') || 'native', editing, plan, tested = false, toastTimer;
if (!labels[scope]) scope = 'native';

async function api(path, body) {
  const response = await fetch('/api/' + path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}
function keys() { return scope === 'both' ? ['native','orca'] : [scope]; }
function toast(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 5500); }
async function reload() { state = await api('state'); render(); }
function mergedRoles() {
  const roles = new Map();
  for (const key of keys()) for (const r of state.homes[key].roles) {
    if (!roles.has(r.name)) roles.set(r.name, {name:r.name,builtin:r.builtin,variants:{}});
    roles.get(r.name).variants[key] = r;
  }
  return [...roles.values()].sort((a,b) => a.name.localeCompare(b.name));
}
function hasDifference(role) {
  if (scope !== 'both') return false;
  const a = role.variants.native, b = role.variants.orca;
  return !a || !b || JSON.stringify(a.values) !== JSON.stringify(b.values) || a.effective_model !== b.effective_model || a.provider !== b.provider;
}
function render() {
  $$('[data-scope]').forEach(b => b.setAttribute('aria-pressed', b.dataset.scope === scope));
  $('#scope-paths').innerHTML = keys().map(k => `<div><span>${labels[k]}</span>${esc(state.homes[k].path)}</div>`).join('');
  const errors = [...state.errors,...keys().flatMap(k => state.homes[k].errors.map(e => labels[k] + '：' + e))];
  $('#global-errors').innerHTML = errors.map(e => `<div class="error-banner">${esc(e)}</div>`).join('');
  $('#add-agent').disabled = errors.length > 0;
  const all = mergedRoles();
  $('#nav-count').textContent = all.length;
  $('#role-count').textContent = all.length.toString().padStart(2,'0');
  const query = $('#search').value.toLowerCase();
  const roles = all.filter(r => `${r.name} ${Object.values(r.variants).map(v => v.effective_model + ' ' + v.values.description).join(' ')}`.toLowerCase().includes(query));
  $('#roster').innerHTML = [true,false].map(builtin => {
    const group = roles.filter(r => r.builtin === builtin);
    if (!group.length) return '';
    return `<div class="group-heading"><h3>${builtin?'官方内置角色':'自定义角色'}</h3><span>${builtin?'BUILT-IN · GPT ONLY':'CUSTOM · ANY MODEL'}</span><i class="line"></i><span>${String(group.length).padStart(2,'0')}</span></div><div class="role-grid">${group.map((r,i) => card(r,i,errors.length > 0)).join('')}</div>`;
  }).join('') || '<div class="empty">没有匹配的子代理。</div>';
}
function card(r, index, disabled) {
  const variants = Object.values(r.variants), current = variants[0];
  const overridden = variants.some(v => v.overridden);
  const different = hasDifference(r);
  const tag = different ? '配置有差异' : r.builtin ? overridden ? '已覆盖' : '内置默认' : '自定义';
  const incompatible = r.builtin && variants.some(v => v.effective_model && !v.effective_model.toLowerCase().startsWith('gpt-'));
  return `<button class="role-card ${r.builtin?'':'custom'}" data-role="${esc(r.name)}" style="animation-delay:${index*25}ms" ${disabled?'disabled':''} aria-label="编辑 ${esc(r.name)}"><div class="card-top"><span class="role-symbol">${icon(r.builtin?'terminal':'settings-2')}</span><span class="tag ${different?'warning':r.builtin?'':'neutral'}">${tag}</span></div><div class="card-title"><h3>${esc(r.name)}</h3>${icon('arrow-up-right')}</div><p class="card-description">${esc(r.builtin ? descriptions[r.name] : current.values.description)}</p><div class="card-models">${keys().map(k => {const v=r.variants[k];return `<div class="model-line">${scope==='both'?`<span class="home-label">${shortLabels[k]}</span>`:'<span class="status-dot"></span>'}<span class="model-name" title="${esc(v?.effective_model)}">${esc(v?.effective_model || (v?'继承会话模型':'尚未配置'))}</span><span class="effort">${esc(v?.effective_effort || 'auto')}</span></div>`;}).join('')}</div>${incompatible?'<div class="card-footnote">现有非 GPT 配置已保留；编辑时需改为 GPT</div>':''}</button>`;
}
function openEditor(name) {
  const role = name ? mergedRoles().find(r => r.name === name) : null;
  const builtin = role?.builtin || false;
  const primary = role ? role.variants.native || role.variants.orca : null;
  editing = {role, builtin, original:primary?.values || {}, revisions:Object.fromEntries(keys().map(k => [k,state.homes[k].revision]))};
  $('#editor-title').textContent = name ? name : '新增自定义子代理';
  $('#editor-type').textContent = builtin ? 'BUILT-IN AGENT / 官方角色覆盖' : 'CUSTOM AGENT / 自定义角色';
  $('#editor-form').reset();
  $('#agent-name').value = name || '';
  $('#agent-name').disabled = Boolean(name);
  $('#agent-effort').innerHTML = state.efforts.map(v => `<option value="${esc(v)}">${v || '继承 Codex 配置'}</option>`).join('');
  for (const field of editable) {
    const input = $(`[name="${field}"]`);
    input.value = editing.original[field] || '';
    input.required = !builtin && ['description','developer_instructions'].includes(field);
  }
  const suggestions = state.models.filter(m => !builtin || m.toLowerCase().startsWith('gpt-'));
  $('#model-list').innerHTML = suggestions.map(m => `<option value="${esc(m)}"></option>`).join('');
  $('#model-rule').textContent = builtin ? '仅限 GPT 系列' : '不限厂商与模型类别';
  $('#builtin-note').hidden = !builtin;
  $$('.custom-field').forEach(e => e.hidden = builtin);
  $('#delete-agent').hidden = !role || (builtin && !Object.values(role.variants).some(v => v.overridden));
  $('#delete-agent span').textContent = builtin ? '恢复内置默认' : '删除角色';
  $('#editor-scope').textContent = `作用范围：${labels[scope]}${scope==='both'?'\n仅同步修改过的字段；其余差异保留。':''}${role && keys().some(k => !role.variants[k])?'\n缺失的一套将使用当前表单创建角色，预览中会列出新文件。':''}`;
  for (const element of $$('[data-difference]')) {
    const field=element.dataset.difference;
    const a=role?.variants.native?.values[field], b=role?.variants.orca?.values[field];
    element.textContent=scope==='both' && role && a!==b ? (['description','developer_instructions'].includes(field)?'两套内容不同；未编辑此字段时各自保留。':`原生：${a||'继承 / 未配置'} · Orca：${b||'继承 / 未配置'}`):'';
  }
  $('#editor-errors').textContent = '';
  $('#editor').showModal();
}
async function buildPreview(operation) {
  const name = $('#agent-name').value.trim();
  const values = Object.fromEntries(editable.filter(k => !editing.builtin || ['model','model_reasoning_effort'].includes(k)).map(k => [k,$(`[name="${k}"]`).value]));
  const patch = Object.fromEntries(Object.entries(values).filter(([k,v]) => !editing.role || v !== (editing.original[k] || '')));
  const payload = {scope,name,operation,create:!editing.role,patch:operation==='delete'?{}:patch,template:values,revisions:editing.revisions};
  $('#editor-errors').textContent = '';
  const button = operation === 'delete' ? $('#delete-agent') : $('#editor-form [type=submit]');
  button.disabled=true;
  try { showPreview(await api('preview',payload)); } catch(e) { $('#editor-errors').textContent=e.message; }
  finally { button.disabled=false; }
}
function showPreview(result) {
  plan=result; tested=!result.needs_test;
  $('#preview-title').textContent=result.operation==='delete'?(builtinNames.has(result.name)?'恢复官方内置角色':'确认删除子代理'):result.operation==='restore'?'恢复上一次配置':'确认配置变更';
  $('#preview-summary').textContent=`${labels[result.scope]} · ${result.name}\n${result.changes.length} 个文件将发生变化${result.operation==='delete'?'；删除的配置会先备份。':'。'}`;
  $('#preview-diffs').innerHTML=result.changes.map(c=>`<section class="diff-file"><header><strong>${c.action} · ${labels[c.home]}</strong>${esc(c.path)}</header><pre>${c.diff.split('\n').map(l=>`<span class="${l.startsWith('+')?'diff-plus':l.startsWith('-')?'diff-minus':''}">${esc(l)}</span>`).join('\n')}</pre></section>`).join('');
  $('#test-section').hidden=!result.needs_test;
  $('#test-results').innerHTML=''; $('#preview-errors').textContent=''; $('#confirm-change').checked=false;
  $('#confirm-label').textContent=result.operation==='delete'?'我确认移除上述配置文件，并了解影响范围':'我已确认上述文件及影响范围';
  $('#apply-change').textContent=result.operation==='delete'?'确认移除':result.operation==='restore'?'确认恢复':'确认保存';
  updateApply(); $('#preview').showModal();
}
function updateApply() { $('#apply-change').disabled=!tested || !$('#confirm-change').checked; }
function renderBackups() {
  $('#backup-list').innerHTML=state.backups.map((b,i)=>`<div class="backup-entry"><div class="backup-top"><strong>${esc(b.name)} <span class="tag neutral">${{save:'保存',delete:'移除',restore:'恢复'}[b.operation]||esc(b.operation)}</span></strong>${i===0?`<button class="button outline" data-restore="${esc(b.id)}">${icon('rotate-ccw')}恢复此次操作前</button>`:''}</div><p>${esc(b.time)} · ${labels[b.scope]}</p>${b.files.map(f=>`<small>${esc(f)}</small>`).join('')}</div>`).join('') || '<div class="empty">还没有操作记录。首次保存后，备份将显示在这里。</div>';
}

$$('[data-scope]').forEach(b=>b.addEventListener('click',()=>{scope=b.dataset.scope;localStorage.setItem('codex-manager-scope',scope);render();}));
$('#search').addEventListener('input',()=>state&&render());
$('#roster').addEventListener('click',e=>{const button=e.target.closest('[data-role]');if(button)openEditor(button.dataset.role);});
$('#add-agent').addEventListener('click',()=>openEditor());
$('#editor-form').addEventListener('submit',e=>{e.preventDefault();buildPreview('save');});
$('#delete-agent').addEventListener('click',()=>buildPreview('delete'));
$('#confirm-change').addEventListener('change',updateApply);
$$('[data-close]').forEach(b=>b.addEventListener('click',()=>$('#'+b.dataset.close).close()));
$('#test-model').addEventListener('click',async()=>{
  const button=$('#test-model'); button.disabled=true; button.innerHTML=icon('loader-circle','spin')+'测试中…'; tested=false;updateApply();
  $('#test-results').innerHTML='<div class="test-result">正在发送最小请求，每套配置最多等待 45 秒…</div>';
  $('#preview-errors').textContent='';
  try {const result=await api('test',{id:plan.id});tested=result.passed;$('#test-results').innerHTML=result.results.map(r=>`<div class="test-result ${r.ok?'':'failed'}"><strong>${labels[r.home]} · ${r.ok?'通过':'未通过'}</strong>${r.ok?` · ${r.latency_ms} ms`:''}<br>${esc(r.message)}</div>`).join('');}
  catch(e){$('#test-results').innerHTML='';$('#preview-errors').textContent=e.message;}
  finally{button.disabled=false;button.innerHTML=icon('zap')+'重新测试';updateApply();}
});
$('#apply-change').addEventListener('click',async()=>{
  const button=$('#apply-change');button.disabled=true;
  try {await api('apply',{id:plan.id,confirmed:true});await reload();$$('dialog[open]').forEach(d=>d.close());toast('配置已保存并备份。请新开 Codex 会话使用最新配置。');}
  catch(e){$('#preview-errors').textContent=e.message;updateApply();}
});
$('#open-backups').addEventListener('click',async()=>{try{await reload();renderBackups();$('#backups').showModal();}catch(e){toast(e.message);}});
$('#backup-list').addEventListener('click',async e=>{const button=e.target.closest('[data-restore]');if(!button)return;button.disabled=true;try{showPreview(await api('restore-preview',{id:button.dataset.restore}));}catch(err){toast(err.message);}finally{button.disabled=false;}});
$('#nav-agents').addEventListener('click',async()=>{try{await reload();toast('已重新读取配置');}catch(e){toast(e.message);}});
document.addEventListener('keydown',e=>{if(e.key==='/'&&!$('dialog[open]')&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)){e.preventDefault();$('#search').focus();}});
reload().catch(e=>{$('#roster').innerHTML=`<div class="error-banner">无法读取配置：${esc(e.message)}。请确认本地服务正在运行。</div>`;$('#add-agent').disabled=true;});
