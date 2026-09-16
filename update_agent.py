#!/usr/bin/env python3
import hashlib,json,os,shutil,subprocess,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen
BIND=os.environ.get('STREAMFORGE_UPDATE_BIND','172.21.0.1'); PORT=int(os.environ.get('STREAMFORGE_UPDATE_PORT','19081'))
TOKEN_FILE=Path(os.environ['STREAMFORGE_UPDATE_TOKEN_FILE']); APP_DIR=Path(os.environ.get('STREAMFORGE_APP_DIR','/vol1/1000/claw/streamforge')); COMPOSE_FILE=APP_DIR/'compose.local.yaml'; UPDATE_SCRIPT=APP_DIR/'update.sh'
REPO='molakesizhanfangguangmang/streamforge'; YT_REPO='yt-dlp/yt-dlp'; YT_BINARY='yt-dlp_linux_aarch64'; YT_DIR=APP_DIR/'data/tools/yt-dlp'; LOCK=threading.Lock(); STATE={'running':False,'result':None,'log':''}
def token(): return TOKEN_FILE.read_text(encoding='ascii').strip()
def latest(repo):
 with urlopen(f'https://api.github.com/repos/{repo}/releases/latest',timeout=15) as r: return json.load(r)
def release_status():
 try:
  r=latest(REPO); a={x['name']:x['browser_download_url'] for x in r.get('assets',[])}; n='streamforge-arm64.tar.zst'
  return {'ok':True,'tag':r.get('tag_name'),'available':n in a and n+'.sha256' in a,'missing':[x for x in(n,n+'.sha256') if x not in a]}
 except Exception as e:return {'ok':False,'error':str(e)}
def ytdlp_status():
 try:
  r=latest(YT_REPO); a={x['name']:x['browser_download_url'] for x in r.get('assets',[])}; b=YT_DIR/'yt-dlp'; current=subprocess.run([str(b),'--version'],capture_output=True,text=True).stdout.strip() if b.is_file() else ''
  return {'ok':True,'tag':r.get('tag_name'),'available':YT_BINARY in a and 'SHA2-256SUMS' in a,'current':current,'url':a.get(YT_BINARY),'sums':a.get('SHA2-256SUMS')}
 except Exception as e:return {'ok':False,'error':str(e)}
def download(url,path):
 with urlopen(url,timeout=120) as r,open(path,'wb') as f:shutil.copyfileobj(r,f)
def run_ytdlp():
 s=ytdlp_status()
 if not s.get('available'):raise RuntimeError('yt-dlp 官方 Release 缺少 ARM64 文件或 SHA2-256SUMS')
 w=Path('/tmp')/f'streamforge-ytdlp-{os.getpid()}';w.mkdir(exist_ok=True)
 try:
  f,wf=w/'yt-dlp',w/'SHA2-256SUMS';download(s['url'],f);download(s['sums'],wf); expect=next((x.split()[0] for x in wf.read_text().splitlines() if x.rstrip().endswith('  '+YT_BINARY)),None)
  if not expect or hashlib.sha256(f.read_bytes()).hexdigest()!=expect:raise RuntimeError('yt-dlp SHA256 校验失败')
  YT_DIR.mkdir(parents=True,exist_ok=True);target=YT_DIR/'yt-dlp'; backup=YT_DIR/'yt-dlp.previous'
  if target.exists():shutil.copy2(target,backup)
  f.chmod(0o755);f.replace(target);return {'ok':True,'tag':s['tag'],'current':subprocess.run([str(target),'--version'],capture_output=True,text=True).stdout.strip()}
 finally:shutil.rmtree(w,ignore_errors=True)
def run_project():
 s=release_status()
 if not s.get('available'):raise RuntimeError('官方 Release 尚未提供带 SHA256 的 ARM64 镜像包')
 p=subprocess.run([str(UPDATE_SCRIPT)],cwd=APP_DIR,env={**os.environ,'STREAMFORGE_COMPOSE_FILE':str(COMPOSE_FILE)},text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=3600)
 if p.returncode:raise RuntimeError(p.stdout[-5000:])
 return {'ok':True,'tag':s['tag']}
def background(action):
 with LOCK:
  if STATE['running']:return False
  STATE.update(running=True,result=None,log='',started_at=time.time())
 def work():
  try:r=run_ytdlp() if action=='ytdlp' else run_project()
  except Exception as e:r={'ok':False,'error':str(e)}
  with LOCK:STATE.update(running=False,result=r,finished_at=time.time(),log=str(r))
 threading.Thread(target=work,daemon=True).start();return True
class H(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def send(self,c,d):
  raw=json.dumps(d,ensure_ascii=False).encode();self.send_response(c);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def auth(self):return self.client_address[0].startswith('172.21.') and self.headers.get('X-Streamforge-Update-Token','')==token()
 def do_GET(self):
  if self.path!='/v1/status':return self.send(404,{'error':'not found'})
  if not self.auth():return self.send(403,{'error':'forbidden'})
  with LOCK:s=dict(STATE)
  self.send(200,{'release':release_status(),'ytdlp':ytdlp_status(),'state':s})
 def do_POST(self):
  action={'/v1/update':'project','/v1/ytdlp':'ytdlp'}.get(self.path)
  if not action:return self.send(404,{'error':'not found'})
  if not self.auth():return self.send(403,{'error':'forbidden'})
  if not background(action):return self.send(409,{'error':'update already running'})
  self.send(202,{'ok':True,'message':f'{action} update started'})
ThreadingHTTPServer((BIND,PORT),H).serve_forever()
