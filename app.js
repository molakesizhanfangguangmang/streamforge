let currentInfo=null;let currentPlatform='bilibili';

const $=s=>document.querySelector(s),$$=s=>document.querySelectorAll(s);const toast=$('#toast');let timer;
function showToast(text){toast.textContent=text;toast.classList.add('show');clearTimeout(timer);timer=setTimeout(()=>toast.classList.remove('show'),2600)}
function escapeHtml(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function known(value){return value!==null&&value!==undefined&&String(value).trim()!==''}
function display(value,unit=''){return known(value)?escapeHtml(value)+unit:'未知'}
function fmtSize(n){return known(n)&&Number.isFinite(Number(n))&&Number(n)>=0?`${(Number(n)/1048576).toFixed(1)} MB`:'未知'}
function renderRows(target,rows,type){
  const video=type==='video';
  target.innerHTML=rows.map((r,i)=>{
    const bitrate=known(r.tbr)?r.tbr:r.abr;
    const cells=video
      ?[display(r.resolution),display(r.vcodec),display(r.fps,' fps'),display(r.bit_depth,' bit'),display(r.dynamic_range),display(r.dolby),fmtSize(r.size)]
      :[display(bitrate,' kbps'),display(r.acodec),display(r.asr,' Hz'),display(r.audio_channels),display(r.dolby),fmtSize(r.size)];
    return `<tr><td><input type="radio" name="${type}-format" value="${escapeHtml(r.id??'')}" ${i===0?'checked':''}></td><td>${display(r.id)}</td>${cells.map((cell,j)=>`<td${j===0?' class="format-quality"':''}>${j===1?`<span class="codec">${cell}</span>`:cell}</td>`).join('')}</tr>`;
  }).join('')||`<tr class="table-empty"><td colspan="${video?9:8}">没有可用流</td></tr>`;
}
function renderFormats(info){currentInfo=info;const v=info.formats.filter(x=>x.kind==='video'),a=info.formats.filter(x=>x.kind==='audio');renderRows($('#video-rows'),v,'video');renderRows($('#audio-rows'),a,'audio');$('#video-count').textContent=`视频 ${v.length}`;$('#audio-count').textContent=`音频 ${a.length}`}
function selectView(view){$$('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.view===view));$$('.view').forEach(v=>v.classList.toggle('active',v.id===`${view}-view`))}
$$('.nav-item').forEach(b=>b.addEventListener('click',()=>selectView(b.dataset.view)));$$('[data-view-target]').forEach(b=>b.addEventListener('click',()=>selectView(b.dataset.viewTarget)));
$$('.source-btn').forEach(btn=>btn.addEventListener('click',()=>{$$('.source-btn').forEach(b=>b.classList.remove('active'));btn.classList.add('active');currentPlatform=btn.dataset.platform;const yt=currentPlatform==='youtube';$('#url').placeholder=yt?'粘贴 YouTube 视频链接':'粘贴 B站视频链接';$('#cookie-hint').textContent=`将使用 ${yt?'YouTube':'B站'} Cookie 配置`}));
$('#parse-btn').addEventListener('click',async()=>{const url=$('#url').value.trim();if(!url){$('#url').focus();showToast('请先输入媒体地址');return}$('#parse-btn').disabled=true;$('#parse-btn').textContent='解析中…';try{const r=await fetch('/api/inspect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,platform:currentPlatform})});const d=await r.json();if(!r.ok)throw Error(d.error||'解析失败');renderFormats(d);showToast(`解析完成：抓到 ${d.formats.filter(x=>x.kind==='video').length} 个视频流、${d.formats.filter(x=>x.kind==='audio').length} 个音频流`)}catch(e){showToast(e.message)}finally{$('#parse-btn').disabled=false;$('#parse-btn').innerHTML='⌕&nbsp; 开始解析'}});
$$('[data-select]').forEach(b=>b.addEventListener('click',()=>{const input=$(`input[name="${b.dataset.select}-format"]`);if(!input){showToast('请先解析媒体地址');return}input.checked=true;showToast(`已选最高${b.dataset.select==='video'?'画质':'音质'}`)}));
function queueMode(){return $('#queue-mode').checked}async function createJob(kind){if(!currentInfo){showToast('请先解析媒体地址');return}const v=$('input[name="video-format"]:checked'),a=$('input[name="audio-format"]:checked');const format=kind==='best'?(v&&a?`${v.value}+${a.value}`:v?.value||a?.value):kind==='video'?v?.value:a?.value;if(!format){showToast('请先选择对应的流');return}try{const r=await fetch('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:currentInfo.webpage_url||$('#url').value,format,title:currentInfo.title,source_info:currentInfo,platform:currentPlatform,metadata:$('#metadata').checked})});if(!r.ok)throw Error('任务创建失败');showToast(queueMode()?'已加入下载列表':'已提交下载任务');selectView('queue')}catch(e){showToast(e.message)}}
function actionMessage(kind){createJob(kind)}
$$('[data-action]').forEach(b=>b.addEventListener('click',()=>actionMessage(b.dataset.action)));
$('#download-selected').addEventListener('click',()=>{const v=$('input[name="video-format"]:checked'),a=$('input[name="audio-format"]:checked');createJob(v&&a?'best':v?'video':'audio')});
$('#queue-mode').addEventListener('change',e=>showToast(e.target.checked?'已打开：下载动作会先加入列表':'已关闭：下载动作将直接执行'));
function pluginNameFromFile(file){return file.name.replace(/\.zip$/i,'').trim()}
function closePlugin(){ $('#plugin-modal').classList.add('hidden') }
function renderPlugins(plugins){const list=$('#plugin-list');list.innerHTML=plugins.length?plugins.map(plugin=>`<article class="plugin-card panel"><div class="plugin-top"><span class="plugin-icon">◇</span><span class="plugin-state ${plugin.enabled?'on':''}">${plugin.enabled?'已启用':'已禁用'} · ${plugin.persistent?'持久化':'运行时'}</span></div><h3>${escapeHtml(plugin.name)}</h3><p>${plugin.version?`版本 ${escapeHtml(plugin.version)}`:'未提供版本信息'}</p><small>${escapeHtml(plugin.id)}</small><div class="plugin-actions"><button class="outline-btn" data-plugin-action="${plugin.enabled?'disable':'enable'}" data-plugin-id="${escapeHtml(plugin.id)}">${plugin.enabled?'禁用':'启用'}</button><button class="outline-btn plugin-delete" data-plugin-action="delete" data-plugin-id="${escapeHtml(plugin.id)}">删除</button></div></article>`).join(''):'<div class="queue-empty">尚未安装插件。</div>'}
async function refreshPlugins(){try{const response=await fetch('/api/plugins'),plugins=await response.json();if(!response.ok)throw Error('插件列表读取失败');renderPlugins(plugins)}catch(error){$('#plugin-list').innerHTML='<div class="queue-empty">插件列表读取失败。</div>'}}
$('#install-plugin').addEventListener('click',()=>$('#plugin-modal').classList.remove('hidden'));
$('.plugin-install')?.addEventListener('click',()=>$('#plugin-modal').classList.remove('hidden'));
$('#close-plugin-modal').addEventListener('click',closePlugin);$('#cancel-plugin').addEventListener('click',closePlugin);
$('#plugin-file').addEventListener('change',event=>{const file=event.target.files[0];$('#plugin-file-name').textContent=file?`${file.name} · ${(file.size/1048576).toFixed(2)} MiB`:'请选择不超过 10 MiB 的 ZIP 文件。'});
$('#confirm-plugin').addEventListener('click',()=>{const file=$('#plugin-file').files[0];if(!file){showToast('请选择 ZIP 文件');return}const button=$('#confirm-plugin');button.disabled=true;button.textContent='正在安装…';const reader=new FileReader();reader.onerror=()=>{button.disabled=false;button.textContent='确认安装';showToast('读取 ZIP 文件失败')};reader.onload=async()=>{try{const result=String(reader.result);const zip_base64=result.slice(result.indexOf(',')+1);const response=await fetch('/api/plugins/install',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:pluginNameFromFile(file),zip_base64,persistent:$('#persist-plugin').checked})});const data=await response.json();if(!response.ok)throw Error(data.error||'插件安装失败');showToast(`插件已安装：${data.plugin.name}`);$('#plugin-file').value='';$('#plugin-file-name').textContent='请选择不超过 10 MiB 的 ZIP 文件。';closePlugin();refreshPlugins()}catch(error){showToast(error.message)}finally{button.disabled=false;button.textContent='确认安装'}};reader.readAsDataURL(file)});
$('#plugin-list').addEventListener('click',async event=>{const button=event.target.closest('[data-plugin-action]');if(!button)return;const id=button.dataset.pluginId,action=button.dataset.pluginAction;if(action==='delete'&&!confirm(`删除插件 ${id}？`))return;button.disabled=true;try{const url=action==='delete'?`/api/plugins/${encodeURIComponent(id)}`:`/api/plugins/${encodeURIComponent(id)}/${action}`;const response=await fetch(url,{method:action==='delete'?'DELETE':'POST'}),data=await response.json();if(!response.ok)throw Error(data.error||'插件操作失败');showToast(action==='delete'?'插件已删除':action==='enable'?'插件已启用':'插件已禁用');refreshPlugins()}catch(error){showToast(error.message)}finally{button.disabled=false}});
refreshPlugins();
$('#backup-now').addEventListener('click',async()=>{const include=['config','cookies','proxies','plugins'];try{const r=await fetch('/api/backup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({include})}),d=await r.json();if(!r.ok)throw Error(d.error||'备份失败');showToast(`已创建备份：${d.name}`)}catch(e){showToast(e.message)}});$('#restore-backup').addEventListener('click',async()=>{try{const list=await (await fetch('/api/backups')).json();const name=prompt(`输入要恢复的备份文件名：\n${list.map(x=>x.name).join('\n')}`);if(!name)return;if(!confirm('恢复会覆盖当前配置、Cookie、代理和插件。继续吗？'))return;const r=await fetch('/api/restore',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,include:['config','cookies','proxies','plugins']})}),d=await r.json();if(!r.ok)throw Error(d.error||'恢复失败');showToast('恢复完成，必要时请重启服务')}catch(e){showToast(e.message)}});function stamp(value){if(!value)return '未知';const d=new Date(value*1000);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`}
async function refreshUpdateStatus(fresh){const project=$('#project-update-status'),ytdlp=$('#ytdlp-update-status'),note=$('#update-check-status'),projectButton=$('#update-project'),ytButton=$('#update-ytdlp');try{const r=await fetch('/api/updates'+(fresh?'?fresh=1':'')),d=await r.json();if(!r.ok)throw Error(d.error||'更新状态读取失败');if(!d.checked){project.textContent='尚未检查更新';ytdlp.textContent='尚未检查更新';projectButton.disabled=true;projectButton.textContent='更新项目';ytButton.disabled=true;ytButton.textContent='更新 yt-dlp';note.textContent='尚未检查过更新，点击“检查更新”查询官方 Release。';return}const release=d.release||{},yt=d.ytdlp||{},state=d.state||{};if(d.up_to_date){project.textContent=`当前 v${d.current_version}，已是最新版本`;projectButton.disabled=true;projectButton.textContent='已是最新'}else if(release.ok&&release.available){project.textContent=`当前 v${d.current_version||'未知'}，最新 ${release.tag} 可更新`;projectButton.disabled=false;projectButton.textContent='更新项目'}else{project.textContent=release.ok?`最新版本 ${release.tag} 缺少校验资产，暂不可更新`:'无法读取官方 Release';projectButton.disabled=true;projectButton.textContent='暂不可更新'}if(yt.ok&&yt.up_to_date){ytdlp.textContent=`当前 ${yt.current}，已是最新版本`;ytButton.disabled=true;ytButton.textContent='已是最新'}else if(yt.ok&&yt.available){ytdlp.textContent=`当前 ${yt.current||'镜像内置'}，最新 ${yt.tag}`;ytButton.disabled=false;ytButton.textContent='更新 yt-dlp'}else{ytdlp.textContent='yt-dlp 官方资产或校验文件不可用';ytButton.disabled=true;ytButton.textContent='暂不可更新'}if(state.running){project.textContent='更新正在执行…';projectButton.disabled=true;ytButton.disabled=true}const cycle=d.auto_check_days?`每 ${d.auto_check_days} 天自动检查`:'不自动检查';note.textContent=`上次检查：${stamp(d.checked_at)}${d.next_check_at?` · 下次自动检查：${stamp(d.next_check_at)}`:''} · ${cycle}`}catch(e){project.textContent='尚未检查更新';ytdlp.textContent=e.message;projectButton.disabled=true;ytButton.disabled=true;note.textContent=e.message}}
async function loadUpdateFrequency(){try{const c=await (await fetch('/api/config')).json();$('#update-frequency').value=String(c.update_check_days??7)}catch(e){}}
async function startHostUpdate(){if(!confirm('更新会备份 Compose、下载并校验官方 ARM64 镜像、重建流铸容器，并会中断当前任务。确定继续吗？'))return;const r=await fetch('/api/updates/start',{method:'POST'}),d=await r.json();if(!r.ok){showToast(d.error||'无法开始更新');return}showToast('更新已开始，服务重建期间页面会短暂断开');setTimeout(()=>refreshUpdateStatus(false),3000)}
async function startYtdlpUpdate(){if(!confirm('只更新 yt-dlp：下载官方 ARM64 文件并校验 SHA256，不重建流铸容器。确定继续吗？'))return;const r=await fetch('/api/updates/ytdlp',{method:'POST'}),d=await r.json();if(!r.ok){showToast(d.error||'无法开始 yt-dlp 更新');return}showToast('yt-dlp 更新已开始；新任务完成后使用新版');setTimeout(()=>refreshUpdateStatus(false),3000)}
$('#update-project').onclick=startHostUpdate;$('#update-ytdlp').onclick=startYtdlpUpdate;$('#check-updates').addEventListener('click',async event=>{const button=event.currentTarget;button.disabled=true;button.textContent='检查中…';try{await refreshUpdateStatus(true);showToast('已获取官方最新版本信息')}finally{button.disabled=false;button.textContent='检查更新'}});function proxyList(p){const list=[];if(p&&p.youtube)list.push(`YouTube ${p.youtube}`);if(p&&p.bilibili)list.push(`B站 ${p.bilibili}`);return list}
function renderProxyNote(p){const list=proxyList(p),note=$('#proxy-note');note.textContent=list.length?`当前生效：${list.join(' · ')}`:'当前未使用代理'}
async function loadProxies(){try{const c=await (await fetch('/api/config')).json(),p=c.proxies||{};$('#youtube-proxy').value=p.youtube||'';$('#bilibili-proxy').value=p.bilibili||'';renderProxyNote(p)}catch(e){$('#proxy-note').textContent='代理设置读取失败'}}
function normalizeProxy(v){v=String(v||'').trim().replace(/^["']+|["']+$/g,'').replace(/\/+$/,'');if(!v)return '';return v.indexOf('://')<0?'http://'+v:v}
$('#save-proxies').addEventListener('click',async()=>{const proxies={youtube:normalizeProxy($('#youtube-proxy').value),bilibili:normalizeProxy($('#bilibili-proxy').value)},bad=Object.entries(proxies).find(([,v])=>v&&!/^(https?|socks5h?|socks4a?):\/\/[^\s/]+$/i.test(v));if(bad){showToast(`${bad[0]==='youtube'?'YouTube':'B站'}代理地址要写成 127.0.0.1:7890 或 http://127.0.0.1:7890`);return}const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({proxies})});const d=await r.json();if(!r.ok){showToast(d.error||'代理保存失败');return}const saved=d.proxies||{};$('#youtube-proxy').value=saved.youtube||'';$('#bilibili-proxy').value=saved.bilibili||'';renderProxyNote(saved);showToast('代理设置已保存，之后的解析与下载立即生效')});
$('#save-update-frequency').addEventListener('click',async()=>{const days=Number($('#update-frequency').value);const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({update_check_days:days})}),d=await r.json();if(!r.ok){showToast(d.error||'周期保存失败');return}showToast(days?`自动检查周期已设为每 ${days} 天`:'已关闭自动检查更新');refreshUpdateStatus(false)});loadUpdateFrequency();refreshUpdateStatus(false);loadProxies();
$$('.settings-tab').forEach(b=>b.addEventListener('click',()=>{$$('.settings-tab').forEach(x=>x.classList.remove('active'));b.classList.add('active');$$('.settings-section').forEach(x=>x.classList.remove('active'));$(`#${b.dataset.settings}-settings`).classList.add('active')}));$$('.save-btn:not(#save-download-path):not(#save-bilibili-cookie):not(#save-youtube-cookie)').forEach(b=>b.addEventListener('click',()=>showToast('设置已保存到本地')));$$('.outline-btn').forEach(b=>b.addEventListener('click',()=>{if(b.id==='youtube-upload')return;if(b.textContent.includes('Cookie'))showToast(`${b.textContent.trim()}功能将在后端接入`)}));
async function refreshDownloadPath(){try{const r=await fetch('/api/config'),c=await r.json();if(c.download_path)$('#download-path').value=c.download_path}catch(e){}}
$('#save-download-path').addEventListener('click',async()=>{const button=$('#save-download-path'),status=$('#download-path-status'),path=$('#download-path').value.trim();button.disabled=true;status.textContent='正在验证目录…';try{const r=await fetch('/api/download-path',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})}),d=await r.json();if(!r.ok)throw Error(d.error||'目录验证失败');$('#download-path').value=d.path;status.textContent=`已确认：${d.path} 可写`;showToast('下载目录已确认')}catch(e){status.textContent=`错误：${e.message}`;showToast(e.message)}finally{button.disabled=false}});
refreshDownloadPath();
let qrRun=null;let qrCloseTimer=null;
function stopQr(){
  if(qrCloseTimer){clearTimeout(qrCloseTimer);qrCloseTimer=null}
  const run=qrRun;qrRun=null;
  if(run){clearTimeout(run.poll);clearTimeout(run.timeout);clearTimeout(run.expiry);run.controller.abort()}
  const image=$('#qr-image');image.onload=null;image.onerror=null;image.removeAttribute('src');image.classList.add('hidden');
  $('#qr-loading').classList.add('hidden');
}
function closeQr(){stopQr();$('#qr-modal').classList.add('hidden')}
async function openQrLogin(){
  stopQr();
  const modal=$('#qr-modal'),loading=$('#qr-loading'),image=$('#qr-image'),state=$('#qr-state'),retry=$('#qr-retry');
  const run={controller:new AbortController(),poll:null,timeout:null,expiry:null};qrRun=run;
  const active=()=>qrRun===run;
  modal.classList.remove('hidden');loading.classList.remove('hidden');retry.classList.add('hidden');state.textContent='正在连接 B站…';
  function fail(message){if(!active())return;stopQr();state.textContent=message;retry.classList.remove('hidden')}
  async function request(url,options={}){
    run.timeout=setTimeout(()=>run.controller.abort(),15000);
    try{const response=await fetch(url,{...options,signal:run.controller.signal});const data=await response.json();if(!response.ok)throw Error(data.error||'请求失败');return data}
    finally{clearTimeout(run.timeout)}
  }
  async function poll(session){
    if(!active())return;
    try{
      const data=await request(`/api/bilibili/qr/status/${session}`);if(!active())return;
      if(data.state==='success'){
        stopQr();state.textContent=`登录成功，当前账号：${data.account?.name||'已登录'}（UID ${data.account?.uid??'未知'}），窗口将在 1.5 秒后关闭`;
        await refreshAuth();qrCloseTimer=setTimeout(closeQr,1500);return;
      }
      if(data.state==='expired'){fail('二维码已失效，请重新获取');return}
      if(!['waiting','scanned'].includes(data.state)){fail(data.error||'登录状态异常，请重试');return}
      state.textContent=data.state==='scanned'?'已扫码，请在手机确认':'等待扫码…';
      run.poll=setTimeout(()=>poll(session),2000);
    }catch(error){fail(error.name==='AbortError'?'登录状态查询超时，请重试':'登录状态查询失败：'+error.message)}
  }
  try{
    const data=await request('/api/bilibili/qr/start',{method:'POST'});if(!active())return;
    if(!known(data.session))throw Error('未返回二维码会话');
    const session=encodeURIComponent(data.session);
    image.onload=()=>{if(!active())return;clearTimeout(run.timeout);loading.classList.add('hidden');image.classList.remove('hidden');state.textContent='等待扫码…';run.poll=setTimeout(()=>poll(session),2000)};
    image.onerror=()=>fail('二维码图片加载失败，请重试');
    state.textContent='二维码加载中…';
    run.timeout=setTimeout(()=>fail('二维码图片加载超时，请重试'),15000);
    run.expiry=setTimeout(()=>fail('二维码已失效，请重新获取'),180000);
    image.src=`/api/bilibili/qr/image/${session}`;
  }catch(error){fail(error.name==='AbortError'?'连接 B站超时，请检查网络或代理':'获取二维码失败：'+error.message)}
}
$('#bilibili-qr-login').addEventListener('click',openQrLogin);
$('#bilibili-refresh').addEventListener('click',async()=>{
  const button=$('#bilibili-refresh');if(button.disabled)return;
  button.disabled=true;button.textContent='正在刷新…';
  try{
    const response=await fetch('/api/bilibili/refresh',{method:'POST'}),data=await response.json();
    if(!response.ok)throw Error(data.error||'刷新登录状态失败');
    showToast(data.refreshed?'登录状态已刷新':'当前登录无需刷新');
    await refreshAuth();
  }catch(error){showToast(error.message)}finally{button.textContent='刷新登录状态';await refreshAuth()}
});
$('#save-bilibili-cookie').addEventListener('click',async()=>{
  const button=$('#save-bilibili-cookie'),text=$('#bilibili-cookie').value;
  if(!text.trim()){showToast('请先粘贴完整 Netscape Cookie 文件');return}
  button.disabled=true;button.textContent='正在验证…';
  try{
    const response=await fetch('/api/auth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bilibili_cookie_text:text})});
    const data=await response.json();if(!response.ok)throw Error(data.error||'Cookie 保存失败');
    $('#bilibili-cookie').value='';await refreshAuth();showToast(data.bilibili?'Cookie 已保存并通过账号验证':'Cookie 已保存，但账号验证未通过');
  }catch(error){showToast(error.message)}finally{button.disabled=false;button.textContent='保存粘贴的 Cookie'}
});
$('#youtube-upload').addEventListener('click',()=>$('#youtube-file').click());
$('#youtube-file').addEventListener('change',async()=>{
  const file=$('#youtube-file').files[0];if(!file)return;
  if(file.size>2*1024*1024){showToast('Cookie 文件过大，请确认选择的是 cookies.txt');$('#youtube-file').value='';return}
  $('#youtube-cookie').value=await file.text();$('#youtube-file').value='';showToast('已读取文件，点击“保存粘贴的 Cookie”写入');
});
$('#save-youtube-cookie').addEventListener('click',async()=>{
  const button=$('#save-youtube-cookie'),text=$('#youtube-cookie').value;
  if(!text.trim()){showToast('请先粘贴 Cookie 内容');return}
  button.disabled=true;button.textContent='正在保存…';
  try{
    const response=await fetch('/api/auth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({youtube_cookie_text:text})});
    const data=await response.json();if(!response.ok)throw Error(data.error||'Cookie 保存失败');
    $('#youtube-cookie').value='';await refreshAuth();showToast(`YouTube Cookie 已保存（${data.youtube_cookies} 条）`);
  }catch(error){showToast(error.message)}finally{button.disabled=false;button.textContent='保存粘贴的 Cookie'}
});
$('#qr-retry').addEventListener('click',openQrLogin);
$('#close-qr').addEventListener('click',closeQr);
$('#qr-modal').addEventListener('click',event=>{if(event.target===$('#qr-modal'))closeQr()});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('#qr-modal').classList.contains('hidden'))closeQr()});
let theme='system';function applyTheme(){document.body.classList.toggle('light',theme==='light'||(theme==='system'&&matchMedia('(prefers-color-scheme: light)').matches));$('#theme-label').textContent=theme==='system'?'跟随系统':theme==='light'?'浅色':'深色';$('#theme-toggle').title=`当前：${$('#theme-label').textContent}，点击切换`};$('#theme-toggle').addEventListener('click',()=>{theme=theme==='system'?'light':theme==='light'?'dark':'system';applyTheme()});applyTheme();
async function refreshJobCount(){try{const r=await fetch('/api/jobs');const d=await r.json();const active=d.jobs.filter(j=>!['done','error','cancelled'].includes(j.status)).length;$('#nav-count').textContent=active;const stats=document.querySelectorAll('.queue-stats b');if(stats.length){stats[0].textContent=d.jobs.filter(j=>j.status==='running').length;stats[1].textContent=d.jobs.filter(j=>j.status==='queued'||j.status==='waiting').length;stats[2].textContent=d.jobs.filter(j=>j.status==='paused').length}}catch(e){}}
let authVersion=0;
async function refreshAuth(){
  const version=++authVersion;
  const status=$('#bilibili-status'),account=$('#bilibili-account');
  try{
    const response=await fetch('/api/auth');if(!response.ok)throw Error('认证状态读取失败');const data=await response.json();if(version!==authVersion)return;
    const verified=data.bilibili===true;
    status.classList.toggle('verified',verified);
    status.textContent=verified?'已认证':data.bilibili_configured===true?'已配置，未验证':data.bilibili_configured===false?'未配置':'状态未知';
    status.title=data.bilibili_error||'';
    account.textContent='';account.classList.add('hidden');
    if(verified&&data.bilibili_account){account.textContent=`当前账号：${data.bilibili_account.name||'未知'}（UID ${data.bilibili_account.uid??'未知'}）`;account.classList.remove('hidden')}
    else if(data.bilibili_error){account.textContent=`验证失败：${data.bilibili_error}`;account.classList.remove('hidden')}
    account.classList.toggle('auth-error',!verified);
    const refresh=$('#bilibili-refresh');
    refresh.disabled=data.bilibili_refresh_capable!==true;
    refresh.title=refresh.disabled?'当前登录没有可用刷新令牌，请重新扫码登录':'';
    const youtube=$('#youtube-status'),note=$('#youtube-note');
    const present=data.youtube_login_present||[],missing=data.youtube_login_missing||[],stale=data.youtube_login_expired||[];
    const login=`登录凭据 ${present.length}/${present.length+missing.length}`;
    youtube.textContent=data.youtube_configured?(data.youtube?login:stale.length?'凭据已过期':'无登录凭据'):'未配置';
    youtube.classList.toggle('verified',data.youtube===true);
    youtube.title=[missing.length?`缺少：${missing.join('、')}`:'',stale.length?`已过期：${stale.join('、')}`:''].filter(Boolean).join('；')||(data.youtube_error||'');
    if(note&&!note.dataset.base)note.dataset.base=note.textContent;
    if(note)note.textContent=data.youtube_configured
      ?`有效 ${data.youtube_valid} 条 · 已过期 ${data.youtube_expired} 条 · ${login}${stale.length?`（已过期：${stale.join('、')}）`:''}${data.youtube_nearest_expiry?` · 登录组最近到期 ${stamp(data.youtube_nearest_expiry)}`:''} · 保存于 ${stamp(data.youtube_saved_at)}${data.youtube_error?` · ${data.youtube_error}`:''}`
      :`${data.youtube_error||'尚未配置 YouTube Cookie'}。${note.dataset.base}`;
  }catch(error){if(version!==authVersion)return;status.textContent='状态未知';status.classList.remove('verified');status.title=error.message;account.textContent='';account.classList.add('hidden');$('#youtube-status').textContent='状态未知'}
}
async function refreshLogs(){try{const d=await (await fetch('/api/logs')).json();const box=$('#log-box');if(!box)return;box.innerHTML=d.length?d.map(x=>`<div data-log-kind="${escapeHtml(x.kind??'')}" data-log-level="${escapeHtml(x.level??'')}"><time>${display(x.time)}</time><span class="log-${escapeHtml(x.level??'')}">${display(x.message)}</span></div>`).join(''):'<div><span class="log-muted">暂无日志</span></div>'}catch(e){}}
refreshAuth();refreshLogs();setInterval(refreshAuth,5000);setInterval(refreshLogs,3000);$$('.log-filter').forEach(f=>f.addEventListener('click',()=>{$$('.log-filter').forEach(x=>x.classList.remove('active'));f.classList.add('active');const key=f.textContent.trim();$$('#log-box>div').forEach(row=>row.style.display=key==='全部'||(key==='错误'&&row.dataset.logLevel==='error')||(key==='解析'&&row.dataset.logKind==='parse')||(key==='下载'&&row.dataset.logKind==='download')?'flex':'none')}));$('#clear-logs')?.addEventListener('click',async()=>{await fetch('/api/logs',{method:'DELETE'});refreshLogs();showToast('日志已清空')});

function queueStatus(status){return {waiting:'等待开始',queued:'等待中',running:'下载中',paused:'已暂停',done:'已完成',error:'失败',cancelled:'已取消'}[status]||status}
function renderQueue(data){const jobs=data.jobs||[],list=$('.task-list'),active=jobs.filter(j=>j.status==='running'),waiting=jobs.filter(j=>j.status==='waiting'||j.status==='queued'),paused=jobs.filter(j=>j.status==='paused');document.querySelectorAll('.queue-stats b').forEach((el,i)=>el.textContent=[active.length,waiting.length,paused.length][i]);$('#nav-count').textContent=active.length+waiting.length+paused.length;$('#start-all').disabled=!jobs.some(j=>j.status==='waiting');$('#start-all').textContent=jobs.some(j=>j.status==='waiting')?'开始下载':'没有等待任务';list.innerHTML=jobs.length?jobs.slice().sort((a,b)=>b.created_at-a.created_at).map(j=>{const action=j.status==='running'?'pause':j.status==='paused'?'resume':null;const pct=Math.max(0,Math.min(100,Number(j.percent)||0));return `<article class="task panel task-${escapeHtml(j.status)}"><div class="task-main"><span class="task-status ${j.status==='running'?'running-dot':''}"></span><div class="task-title"><strong>${display(j.title)}</strong><small>${display(j.format)} · ${queueStatus(j.status)}</small></div><span class="task-percent">${j.status==='waiting'?'等待':pct.toFixed(1)+'%'}</span>${action?`<button class="icon-btn" data-job-action="${action}" data-job-id="${escapeHtml(j.id)}" title="${action==='pause'?'暂停任务':'继续任务'}">${action==='pause'?'Ⅱ':'▶'}</button>`:''}${!['done','error','cancelled'].includes(j.status)?`<button class="icon-btn" data-job-action="cancel" data-job-id="${escapeHtml(j.id)}" title="取消任务">×</button>`:''}</div><div class="task-progress"><span style="width:${pct}%"></span></div><div class="task-bottom"><span>${display(j.log||j.error||'等待任务输出')}</span><span>${display(j.id)}</span></div></article>`}).join(''):'<div class="queue-empty">当前没有下载任务。</div>';const current=active[0]||waiting[0]||paused[0],empty=$('#progress-empty'),panel=$('#progress-active');if(!current){empty.classList.remove('hidden');panel.classList.add('hidden')}else{empty.classList.add('hidden');panel.classList.remove('hidden');panel.querySelector('strong').textContent=current.title||'未命名任务';panel.querySelector('small').textContent=`${current.format||'--'} · ${queueStatus(current.status)}`;panel.querySelector('.percent').textContent=`${Number(current.percent||0).toFixed(1)}%`;panel.querySelector('.progress-track span').style.width=`${Number(current.percent||0)}%`;panel.querySelector('.progress-meta span').textContent=current.log||current.error||queueStatus(current.status)}}
async function refreshQueue(){try{const r=await fetch('/api/jobs'),d=await r.json();renderQueue(d)}catch(e){}}
$('.task-list').addEventListener('click',async e=>{const b=e.target.closest('[data-job-action]');if(!b)return;const id=b.dataset.jobId,action=b.dataset.jobAction;const url=action==='cancel'?`/api/jobs/${encodeURIComponent(id)}`:`/api/jobs/${encodeURIComponent(id)}/${action}`;await fetch(url,{method:action==='cancel'?'DELETE':'PATCH'});refreshQueue()});
$('#start-all').onclick=async()=>{const r=await fetch('/api/jobs/start',{method:'POST'}),d=await r.json();showToast(`已开始 ${d.started?.length||0} 个任务`);refreshQueue()};$('#clear-finished').onclick=async()=>{const r=await fetch('/api/jobs/clear-finished',{method:'POST'}),d=await r.json();showToast(`已清除 ${d.removed?.length||0} 条记录`);refreshQueue()};refreshQueue();setInterval(refreshQueue,1000);
async function loadRuntimeConfig(){try{const c=await (await fetch('/api/config')).json();$('#config-concurrency').value=String(c.concurrency||2);$('#config-metadata').checked=!!c.metadata;$('#default-queue').checked=!!c.queue_mode;$('#metadata').checked=!!c.metadata}catch(e){}}
$('#save-storage-config').addEventListener('click',async()=>{const body={concurrency:Number($('#config-concurrency').value),metadata:$('#config-metadata').checked,queue_mode:$('#default-queue').checked};const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),d=await r.json();if(!r.ok){showToast(d.error||'下载设置保存失败');return}$('#metadata').checked=!!d.metadata;showToast('下载设置已保存')});loadRuntimeConfig();
