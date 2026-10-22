import {renderMarkdown} from './markdown.js';

const $ = id => document.getElementById(id);
const TOKEN = document.querySelector('meta[name="agent-token"]').content;
let busy = false, readingImages = false, currentJob = null, lastStatus = null;
let logCount = 0, pendingImages = [], projectItems = [], toastTimer;

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.classList.add('icon'); svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', '#i-' + name); svg.append(use); return svg;
}
async function api(path, body) {
  const options = body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Agent-Token': TOKEN}, body: JSON.stringify(body)};
  const response = await fetch(path, options), data = await response.json();
  if (!response.ok) {
    const error = Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
    error.status = response.status; throw error;
  }
  return data;
}
function toast(text) {
  clearTimeout(toastTimer); $('toast').textContent = text; $('toast').classList.remove('hidden');
  toastTimer = setTimeout(() => $('toast').classList.add('hidden'), 5500);
}
function scrollChat(force = false) {
  const chat = $('chat');
  if (force || chat.scrollHeight - chat.scrollTop - chat.clientHeight < 200) chat.scrollTop = chat.scrollHeight;
}
function showImage(src, name) {
  $('dialogImage').src = src; $('dialogImage').alt = name; $('imageTitle').textContent = name;
  if (!$('imageDialog').open) $('imageDialog').showModal();
}
function updateSend() {
  $('send').disabled = busy || readingImages || (!$('prompt').value.trim() && !pendingImages.length);
}
function resizePrompt() {
  $('prompt').style.height = 'auto';
  $('prompt').style.height = Math.min(190, Math.max(60, $('prompt').scrollHeight)) + 'px';
  updateSend();
}
function renderAttachments() {
  $('attachments').replaceChildren();
  pendingImages.forEach((item, index) => {
    const wrap = document.createElement('div'); wrap.className = 'attachment';
    const image = document.createElement('img'); image.src = item.data_url; image.alt = item.name;
    image.onclick = () => showImage(item.data_url, item.name);
    const label = document.createElement('small'); label.textContent = item.name;
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×';
    remove.title = '移除图片'; remove.setAttribute('aria-label', '移除 ' + item.name);
    remove.onclick = () => {if (!busy) {pendingImages.splice(index, 1); renderAttachments();}};
    wrap.append(image, label, remove); $('attachments').append(wrap);
  });
  updateSend();
}
async function addFiles(files) {
  if (busy || readingImages) return;
  readingImages = true; updateSend();
  try {
    const added = []; let total = pendingImages.reduce((sum, image) => sum + image.size, 0);
    for (const file of files) {
      if (pendingImages.length + added.length >= 4) throw Error('一次最多上传4张图片');
      if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) throw Error('请上传 PNG、JPEG 或 WebP 图片');
      if (file.size > 8 * 1024 * 1024) throw Error('单张图片不得超过8 MB');
      total += file.size; if (total > 16 * 1024 * 1024) throw Error('图片合计不得超过16 MB');
      const data_url = await new Promise((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(Error('图片读取失败')); reader.readAsDataURL(file);
      });
      added.push({name: file.name, data_url, size: file.size});
    }
    pendingImages.push(...added); renderAttachments();
  } catch (error) {toast(error.message);}
  finally {readingImages = false; $('imageInput').value = ''; updateSend();}
}
function message(role, text, images = []) {
  $('welcome').classList.add('hidden');
  const node = document.createElement('article'); node.className = 'message ' + role;
  if (role === 'assistant') {
    const label = document.createElement('div'); label.className = 'message-label';
    label.append(icon('cube'), document.createTextNode('形构')); node.append(label);
  }
  const content = document.createElement('div'); content.className = 'message-content';
  if (role === 'assistant') renderMarkdown(content, text); else content.textContent = text;
  node.append(content);
  if (images.length) {
    const group = document.createElement('div'); group.className = 'message-images';
    for (const item of images) {
      const image = document.createElement('img'); image.src = item.data_url; image.alt = item.name;
      image.onclick = () => showImage(item.data_url, item.name); group.append(image);
    }
    node.append(group);
  }
  if (role === 'assistant') {
    const actions = document.createElement('div'); actions.className = 'message-actions';
    const copy = document.createElement('button'); copy.type = 'button'; copy.append(icon('copy'), document.createTextNode('复制'));
    copy.onclick = async () => {try {await navigator.clipboard.writeText(text); toast('已复制答复');} catch {toast('复制失败，请选中文字复制');}};
    actions.append(copy); node.append(actions);
  }
  $('chat').insertBefore(node, $('working')); scrollChat(role === 'user');
}
function renderProjects() {
  const query = $('projectSearch').value.trim().toLowerCase(), container = $('projectList');
  container.replaceChildren();
  const items = projectItems.filter(item => (item.title + ' ' + item.id).toLowerCase().includes(query));
  if (!items.length) {
    const empty = document.createElement('p'); empty.className = 'empty-list'; empty.textContent = query ? '没有匹配的项目' : '暂无项目';
    container.append(empty); return;
  }
  for (const item of items) {
    const button = document.createElement('button'); button.type = 'button';
    button.className = 'project-item' + (item.id === lastStatus?.project ? ' active' : '');
    button.disabled = busy; button.title = item.id;
    if (item.id === lastStatus?.project) button.setAttribute('aria-current', 'page');
    const text = document.createElement('div'); text.className = 'project-text';
    const title = document.createElement('strong'); title.textContent = item.title;
    const subtitle = document.createElement('span');
    subtitle.textContent = item.awaiting_parameters ? '待补充参数' : (item.has_model ? '已保存模型' : '设计草稿');
    text.append(title, subtitle); button.append(icon('folder'), text);
    button.onclick = () => start('/api/open', {project: item.id}); container.append(button);
  }
}
function renderBrief(status) {
  const brief = status.design_brief || {};
  $('briefPanel').classList.toggle('hidden', !brief.structure && !(status.references || []).length);
  $('briefState').textContent = status.awaiting_parameters ? '待补充' : (brief.ready_to_model ? '已齐全' : '');
  $('briefStructure').textContent = brief.structure || ''; $('briefParameters').replaceChildren();
  const names = {user_text: '用户描述', image_annotation: '图片标注', standard: '标准规格', derived: '参数推导', inferred: '推测', default: '默认', unknown: '待补充', cad_measurement: '实测'};
  for (const parameter of brief.parameters || []) {
    const row = document.createElement('tr');
    for (const value of [parameter.name, (parameter.value ?? '待补充') + ' ' + parameter.unit, names[parameter.source] || parameter.source]) {
      const cell = document.createElement('td'); cell.textContent = value;
      if (parameter.basis) cell.title = parameter.basis; row.append(cell);
    }
    $('briefParameters').append(row);
  }
  $('briefQuestions').replaceChildren();
  for (const question of brief.missing_parameters || []) {
    const li = document.createElement('li'); li.textContent = question; $('briefQuestions').append(li);
  }
  $('references').replaceChildren();
  for (const ref of status.references || []) {
    const image = document.createElement('img'), src = '/files/' + encodeURIComponent(ref.filename);
    image.src = src; image.alt = ref.name; image.title = ref.name; image.onclick = () => showImage(src, ref.name); $('references').append(image);
  }
}
function setBusy(value) {
  busy = value;
  for (const id of ['execution', 'newProject', 'inspect', 'saveConfig', 'clearKey', 'addImages', 'imageInput']) $(id).disabled = value;
  $('send').classList.toggle('hidden', value); $('stop').classList.toggle('hidden', !value);
  $('stop').disabled = !value; $('working').classList.toggle('hidden', !value);
  $('continueTask').disabled = value; $('taskState').textContent = value ? '处理中' : '就绪';
  renderProjects(); updateSend();
}
async function refresh() {
  const status = await api('/api/status'); lastStatus = status;
  projectItems = status.project_items || (status.projects || []).map(id => ({id, title: id, has_model: true}));
  renderProjects(); renderBrief(status);
  $('modelLabel').textContent = status.model; $('modelPicker').title = '连接设置 · ' + status.model;
  $('continueTask').classList.toggle('hidden', !status.can_continue); $('continueTask').disabled = busy;
  $('dot').classList.toggle('ok', status.ready); $('connection').textContent = status.error ? 'CAD 连接失败' : 'SolidCraft 已连接';
  $('connection').title = 'SolidCraft ' + (status.version || '') + ' · ' + status.mode;
  $('configState').textContent = status.connection_saved ? '已保存' : (status.key_configured ? '已连接' : '未配置');
  const state = status.snapshot || {};
  $('bodyCount').textContent = (state.bodies || []).length; $('featureCount').textContent = (state.timeline || []).length;
  $('paramCount').textContent = (state.params || []).length; $('stateJson').textContent = JSON.stringify(state, null, 2);
  if (status.preview) {
    if ($('preview').getAttribute('src') !== status.preview) $('preview').src = status.preview;
    $('preview').classList.remove('hidden'); $('placeholder').classList.add('hidden');
  } else {$('preview').classList.add('hidden'); $('placeholder').classList.remove('hidden');}
  $('files').replaceChildren();
  const files = (status.files || []).filter(name => name === 'drawing.html' || name === 'design.solidcraft' || /^model\./.test(name) || /^view-/.test(name));
  const labels = {'drawing.html': '视图说明 ↗', 'design.solidcraft': '原生文件', 'view-front.png': '前视图', 'view-top.png': '俯视图', 'view-right.png': '右视图'};
  for (const filename of files) {
    const link = document.createElement('a'); link.textContent = labels[filename] || filename;
    link.href = '/files/' + encodeURIComponent(filename); link.download = filename;
    if (filename === 'drawing.html') {link.className = 'drawing'; link.title = '下载后打开，可打印或保存为 PDF';}
    $('files').append(link);
  }
  if (!busy && status.awaiting_parameters) $('taskState').textContent = '等待补充参数';
  if (status.error) toast(status.error); return status;
}
async function poll() {
  if (!currentJob) return;
  try {
    const job = await api('/api/jobs/' + currentJob), events = job.events.slice(logCount);
    for (const event of events) {
      const row = document.createElement('div'); row.className = 'log ' + event.kind;
      const time = document.createElement('time'); time.textContent = event.time.slice(11);
      row.append(time, document.createTextNode(event.text)); $('logs').append(row);
      if (['brief', 'drawing', 'saved'].includes(event.kind)) $('workingText').textContent = {brief: '正在整理结构与尺寸', drawing: '正在生成视图', saved: '正在保存结果'}[event.kind];
      else if (event.kind === 'model') $('workingText').textContent = '正在理解需求与规划';
      else if (event.kind === 'tool') $('workingText').textContent = '正在调用 CAD';
    }
    logCount = job.events.length; $('logBadge').textContent = logCount ? String(logCount) : ''; $('logs').scrollTop = $('logs').scrollHeight;
    if (events.some(event => event.kind === 'brief')) await refresh();
    if (['completed', 'failed', 'stopped', 'paused', 'awaiting_parameters'].includes(job.status)) {
      if (job.status === 'completed' && ['new', 'open'].includes(job.kind)) {
        document.querySelectorAll('#chat .message').forEach(node => node.remove());
        pendingImages = []; $('prompt').value = ''; renderAttachments(); resizePrompt(); $('briefPanel').open = false;
        $('welcome').classList.toggle('hidden', job.kind === 'open');
        toast(job.kind === 'new' ? '已新建设计' : '已打开项目');
        if (job.kind === 'open') message('assistant', '项目已打开，可以继续描述修改要求。');
      } else if (job.answer) message('assistant', job.answer);
      if (job.error) message('error', job.error);
      currentJob = null; setBusy(false); await refresh();
      $('taskState').textContent = {completed: '已完成', failed: '需要处理', stopped: '已停止', paused: '等待继续', awaiting_parameters: '等待补充参数'}[job.status];
      return;
    }
    setTimeout(poll, 900);
  } catch (error) {
    if ([403, 404].includes(error.status)) {currentJob = null; setBusy(false); toast('服务已更新，请刷新页面'); return;}
    toast('读取进度失败：' + error.message); setTimeout(poll, 2500);
  }
}
async function start(path, body = {}) {
  if (busy || readingImages) return false;
  setBusy(true); $('workingText').textContent = '正在处理'; scrollChat(true);
  try {
    const result = await api(path, body); currentJob = result.job_id; logCount = 0;
    $('logs').replaceChildren(); poll(); return true;
  } catch (error) {setBusy(false); toast(error.message); return false;}
}
function openSettings() {
  $('connectionPanel').open = true;
  if (matchMedia('(max-width:760px)').matches) document.body.classList.add('show-sidebar');
  else document.body.classList.remove('sidebar-collapsed');
  $('connectionPanel').scrollIntoView({block: 'nearest'}); $('model').focus();
}
function preference(key, value) {try {if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value);} catch {}}
function setTheme(theme) {
  document.documentElement.dataset.theme = theme; preference('shapecraft-theme', theme);
  $('themeToggle').setAttribute('aria-label', theme === 'dark' ? '切换浅色主题' : '切换深色主题');
  $('themeToggle').title = theme === 'dark' ? '切换浅色主题' : '切换深色主题';
}
setTheme(preference('shapecraft-theme') || 'light');
$('fontChoice').value = preference('shapecraft-font') || 'noto';
document.documentElement.dataset.font = $('fontChoice').value;
$('fontSize').value = preference('shapecraft-size') || 'normal';
document.documentElement.dataset.textSize = $('fontSize').value;
$('fontChoice').onchange = () => {document.documentElement.dataset.font = $('fontChoice').value; preference('shapecraft-font', $('fontChoice').value);};
$('fontSize').onchange = () => {document.documentElement.dataset.textSize = $('fontSize').value; preference('shapecraft-size', $('fontSize').value);};
$('themeToggle').onclick = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
$('sidebarToggle').onclick = () => {
  if (matchMedia('(max-width:760px)').matches) document.body.classList.toggle('show-sidebar');
  else document.body.classList.toggle('sidebar-collapsed');
};
$('sidebarShade').onclick = () => document.body.classList.remove('show-sidebar');
$('modelPicker').onclick = openSettings;
$('connectionForm').onsubmit = event => {event.preventDefault(); $('saveConfig').click();};
$('projectSearch').oninput = renderProjects;
$('preview').onclick = () => showImage($('preview').src, '当前模型');
$('closeImage').onclick = () => $('imageDialog').close();
$('imageDialog').onclick = event => {if (event.target === $('imageDialog')) $('imageDialog').close();};
document.addEventListener('keydown', event => {if (event.key === 'Escape') document.body.classList.remove('show-sidebar');});
$('saveConfig').onclick = async () => {
  try {
    const result = await api('/api/config', {api_key: $('key').value, model: $('model').value.trim(), thinking: $('reasoning').value !== 'off', reasoning_effort: $('reasoning').value === 'off' ? 'high' : $('reasoning').value, remember: $('remember').checked});
    $('key').value = ''; await refresh(); toast(result.connection_saved ? '配置已保存' : '配置已应用');
  } catch (error) {toast(error.message);}
};
$('clearKey').onclick = async () => {
  try {
    await api('/api/config', {clear_key: true, model: $('model').value.trim(), thinking: $('reasoning').value !== 'off', reasoning_effort: $('reasoning').value === 'off' ? 'high' : $('reasoning').value, remember: false});
    $('key').value = ''; await refresh(); toast('已清除连接配置');
  } catch (error) {toast(error.message);}
};
$('send').onclick = async () => {
  const prompt = $('prompt').value.trim();
  if ((!prompt && !pendingImages.length) || busy || readingImages) return;
  const local = $('execution').value === 'local';
  if (local && pendingImages.length) {toast('图片识别请使用智能体模式'); openSettings(); return;}
  if (!local && !lastStatus?.key_configured) {toast('请先配置 API Key'); openSettings(); $('key').focus(); return;}
  const images = pendingImages.map(({name, data_url}) => ({name, data_url}));
  if (await start(local ? '/api/local' : '/api/chat', {prompt, images})) {
    message('user', prompt || '根据参考图建模', images); $('prompt').value = ''; pendingImages = [];
    renderAttachments(); resizePrompt(); document.body.classList.remove('show-sidebar');
  }
};
$('addImages').onclick = () => $('imageInput').click();
$('imageInput').onchange = event => addFiles([...event.target.files]);
$('prompt').oninput = resizePrompt;
$('prompt').addEventListener('paste', event => {
  const files = [...event.clipboardData.items].filter(item => item.kind === 'file' && item.type.startsWith('image/')).map(item => item.getAsFile()).filter(Boolean);
  if (files.length) {event.preventDefault(); addFiles(files);}
});
$('prompt').onkeydown = event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229 && !busy) {event.preventDefault(); $('send').click();}
};
$('composer').addEventListener('dragover', event => {event.preventDefault(); if (!busy) $('composer').classList.add('drop-active');});
$('composer').addEventListener('dragleave', () => $('composer').classList.remove('drop-active'));
$('composer').addEventListener('drop', event => {event.preventDefault(); $('composer').classList.remove('drop-active'); addFiles([...event.dataTransfer.files]);});
$('continueTask').onclick = () => start('/api/continue');
$('stop').onclick = async () => {try {const result = await api('/api/stop', {}); toast(result.message);} catch (error) {toast(error.message);}};
$('inspect').onclick = () => start('/api/inspect');
$('newProject').onclick = () => start('/api/new');
document.querySelectorAll('[data-prompt]').forEach(button => button.onclick = () => {$('prompt').value = button.dataset.prompt; resizePrompt(); $('prompt').focus();});
updateSend();
refresh().then(status => {
  $('model').value = status.model; $('reasoning').value = status.thinking ? (status.reasoning_effort || 'high') : 'off';
  $('connectionPanel').open = !status.key_configured;
  if (status.active_job) {currentJob = status.active_job; setBusy(true); poll();}
}).catch(error => {$('connection').textContent = '连接失败'; toast('连接本地服务失败：' + error.message);});
