const $ = id => document.getElementById(id);
const escapeHtml = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const post = data => ({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message || data.error || '请求失败');
  return data;
}
function show(id) {
  document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
  $(id).classList.add('active');
  ({home:loadStatus,forum:loadForum,graph:loadGraph,config:loadConfig}[id] || (()=>{}))();
}
async function loadStatus() {
  const d = await api('/api/status');
  $('sys').textContent = `${d.system_status} · 在线 Worker ${d.workers.filter(w=>w.online).length}`;
  $('errors').textContent = d.recent_errors.join('；');
  $('apps').innerHTML = Object.entries(d.apps).map(([n,v])=>`<div class="card"><h3>${escapeHtml(n)}</h3><p class="status ${escapeHtml(v.status)}">${escapeHtml(v.status)}</p><button class="primary" onclick="app('${n}','start')">启动</button> <button onclick="app('${n}','stop')">停止</button><pre>${escapeHtml(v.output || '暂无输出')}</pre></div>`).join('');
}
async function app(name, action) {
  try {await api(`/api/${action}/${name}`); await loadStatus();} catch(e) {alert(e.message);}
}
async function system(url) {
  try {await api(url, post({})); await loadStatus();} catch(e) {alert(e.message);}
}
let searchActive = false;
async function search() {
  if(searchActive) return;
  searchActive = true;
  try {
    const task = await api('/api/search', post({query:$('query').value,source_types:$('types').value.split(',').map(x=>x.trim()).filter(Boolean),async:true}));
    $('result').textContent = `任务 ${task.task_id} 已排队，等待 Worker…`;
    const until = Date.now() + 120000;
    while(Date.now() < until) {
      const state = await api(task.status_url);
      if(state.status === 'failed') throw new Error(state.error);
      if(state.status === 'succeeded') {
        const d = state.result;
        $('result').innerHTML = `<h3>报告 ${escapeHtml(d.report_id)}</h3><p>${escapeHtml(d.summary)}</p><p>来源 ${d.sources.length} 条；正向 ${d.sentiment.positive}% / 中性 ${d.sentiment.neutral}% / 负向 ${d.sentiment.negative}%</p><pre>${escapeHtml(JSON.stringify(d.sources,null,2))}</pre>`;
        return;
      }
      $('result').textContent = `任务 ${task.task_id}：${state.status}；执行节点：${state.worker_id || '等待分配'}`;
      await new Promise(resolve=>setTimeout(resolve,1000));
    }
    $('result').textContent = `任务仍在后台执行，可查询 ${task.status_url}。`;
  } catch(e) { $('result').textContent = e.message; }
  finally { searchActive = false; }
}
async function forum(url) {try {await api(url); await loadForum();} catch(e) {alert(e.message);}}
async function loadForum() {const d = await api('/api/forum/log'); $('fstatus').textContent=d.status; $('flog').textContent=d.logs.map(x=>`${x.time} [${x.task_status}] ${x.message_text}`).join('\n');}
async function loadGraph() {
  const rid = location.pathname.startsWith('/graph-viewer/') ? decodeURIComponent(location.pathname.split('/').pop()) : 'latest';
  $('graphout').textContent = JSON.stringify(await api('/api/graph/' + encodeURIComponent(rid)),null,2);
}
async function loadConfig() {$('cfg').value = JSON.stringify(await api('/api/config'),null,2);}
async function saveConfig() {try {await api('/api/config',post(JSON.parse($('cfg').value))); alert('配置已保存；端口变更需重启 API。');} catch(e) {alert(e.message);}}
loadStatus().catch(e=>$('errors').textContent=e.message);
if(location.pathname.startsWith('/graph-viewer')) show('graph');
setInterval(()=>{if($('home').classList.contains('active'))loadStatus().catch(()=>{});},3000);
