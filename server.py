#!/usr/bin/env python3
import json, os, re, shutil, signal, subprocess, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT=Path(os.environ.get('STREAMFORGE_DATA','/data')); DOWNLOADS=Path(os.environ.get('STREAMFORGE_DOWNLOADS','/downloads')); STATIC=Path(__file__).parent
ROOT.mkdir(parents=True,exist_ok=True); DOWNLOADS.mkdir(parents=True,exist_ok=True)
STATE=ROOT/'state.json'; LOCK=threading.RLock(); jobs={}; processes={}; config={'concurrency':2,'metadata':False,'queue_mode':False,'cookies':{'bilibili':'','youtube':''},'proxies':{'bilibili':'','youtube':''}}
NODE_MODE=os.environ.get('STREAMFORGE_NODE','none')
def node_runtime():
 for p in ('/usr/local/bin/node','/opt/node/bin/node','/usr/bin/node'):
  if Path(p).is_file() and os.access(p,os.X_OK): return p
 return ''
NODE_PATH=node_runtime()
if STATE.exists():
 try: config.update(json.loads(STATE.read_text()))
 except Exception: pass

def save(): STATE.write_text(json.dumps(config,ensure_ascii=False,indent=2))
def json_response(h,code,data):
 raw=json.dumps(data,ensure_ascii=False).encode(); h.send_response(code); h.send_header('Content-Type','application/json; charset=utf-8'); h.send_header('Content-Length',str(len(raw))); h.end_headers(); h.wfile.write(raw)
def clean_name(s): return re.sub(r'[/\\:*?"<>|\x00-\x1f]','_',s).strip()[:160] or 'untitled'
def formats(info):
 out=[]
 for f in info.get('formats',[]):
  hasv=f.get('vcodec') not in (None,'none'); hasa=f.get('acodec') not in (None,'none')
  x={'id':f.get('format_id'),'ext':f.get('ext'),'resolution':f.get('resolution') or (f"{f.get('width')}x{f.get('height')}" if f.get('width') else None),'width':f.get('width'),'height':f.get('height'),'fps':f.get('fps'),'vcodec':f.get('vcodec'),'acodec':f.get('acodec'),'abr':f.get('abr'),'vbr':f.get('vbr'),'tbr':f.get('tbr'),'filesize':f.get('filesize') or f.get('filesize_approx'),'size':f.get('filesize') or f.get('filesize_approx'),'dynamic_range':f.get('dynamic_range'),'audio_channels':f.get('audio_channels'),'asr':f.get('asr'),'protocol':f.get('protocol'),'kind':'video' if hasv else 'audio' if hasa else 'other'}
  if x['kind'] in ('video','audio'): out.append(x)
 return {'title':info.get('title'),'id':info.get('id'),'uploader':info.get('uploader'),'thumbnail':info.get('thumbnail'),'webpage_url':info.get('webpage_url'),'duration':info.get('duration'),'upload_date':info.get('upload_date'),'description':info.get('description'),'formats':out}
def run_ytdlp(args,platform='bilibili',capture=True):
 cmd=['yt-dlp','--no-warnings','--newline']+args
 if NODE_PATH: cmd += ['--js-runtimes',f'node:{NODE_PATH}']
 c=config.get('cookies',{}).get(platform,''); p=config.get('proxies',{}).get(platform,'')
 if c: cmd += ['--cookies',c]
 if p: cmd += ['--proxy',p]
 return subprocess.run(cmd,text=True,capture_output=capture,timeout=180)
def worker(job):
 jid=job['id']; job['status']='running'; job['started_at']=time.time();
 try:
  title=clean_name(job.get('title') or jid); folder=DOWNLOADS/title; folder.mkdir(parents=True,exist_ok=True); out=str(folder/'%(title)s.%(ext)s')
  args=['-f',job['format'],'-o',out]
  if job.get('metadata'): args += ['--write-info-json']
  args += [job['url']]
  p=subprocess.Popen(['yt-dlp','--newline']+args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
  with LOCK: processes[jid]=p
  for line in p.stdout:
   with LOCK: job['log']=line.strip()[-500:]; job['updated_at']=time.time()
   m=re.search(r'\[download\]\s+(\d+(?:\.\d+)?)%',line)
   if m: job['percent']=float(m.group(1))
   if 'Destination:' in line: job['file']=line.split('Destination:',1)[1].strip()
   if job.get('cancel_requested'):
    p.send_signal(signal.SIGTERM); break
  rc=p.wait(); job['status']='cancelled' if job.get('cancel_requested') else 'done' if rc==0 else 'error'; job['returncode']=rc
 except Exception as e: job['status']='error'; job['error']=str(e)
 finally:
  with LOCK: processes.pop(jid,None)

def scheduler():
 while True:
  with LOCK:
   active=sum(j['status']=='running' for j in jobs.values()); limit=int(config.get('concurrency',2))
   waiting=[j for j in jobs.values() if j['status']=='queued']
  for j in waiting[:max(0,limit-active)]: threading.Thread(target=worker,args=(j,),daemon=True).start()
  time.sleep(.5)
threading.Thread(target=scheduler,daemon=True).start()
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def body(self): return json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or b'{}')
 def do_GET(self):
  path=urlparse(self.path).path
  if path=='/api/health': return json_response(self,200,{'ok':True,'version':'0.1.0','node':{'mode':NODE_MODE,'path':NODE_PATH,'version':subprocess.run([NODE_PATH,'--version'],capture_output=True,text=True).stdout.strip() if NODE_PATH else ''}})
  if path=='/api/jobs':
   with LOCK: return json_response(self,200,{'jobs':list(jobs.values()),'config':config})
  if path=='/api/config': return json_response(self,200,config)
  if path=='/' or path=='/index.html': self.path='/index.html'
  file=STATIC/(self.path.lstrip('/'))
  if file.is_file():
   data=file.read_bytes(); self.send_response(200); self.send_header('Content-Type','text/html' if file.suffix=='.html' else 'text/javascript' if file.suffix=='.js' else 'text/css'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
  json_response(self,404,{'error':'not found'})
 def do_POST(self):
  path=urlparse(self.path).path; b=self.body()
  if path=='/api/inspect':
   try:
    r=run_ytdlp(['--dump-single-json','--skip-download',b['url']],b.get('platform','bilibili')); return json_response(self,200,formats(json.loads(r.stdout))) if r.returncode==0 else json_response(self,422,{'error':r.stderr[-1500:]})
   except Exception as e:return json_response(self,500,{'error':str(e)})
  if path=='/api/jobs':
   if not b.get('url') or not b.get('format'): return json_response(self,400,{'error':'url and format are required'})
   j={'id':uuid.uuid4().hex[:12],'url':b['url'],'format':b['format'],'platform':b.get('platform','bilibili'),'title':b.get('title','untitled'),'metadata':bool(b.get('metadata')),'status':'queued','percent':0,'created_at':time.time(),'log':''}
   with LOCK: jobs[j['id']]=j
   return json_response(self,202,j)
  if path=='/api/config':
   with LOCK: config.update({k:v for k,v in b.items() if k in ('concurrency','metadata','queue_mode','cookies','proxies')}); save(); return json_response(self,200,config)
  json_response(self,404,{'error':'not found'})
 def do_DELETE(self):
  m=re.match(r'/api/jobs/([^/]+)$',urlparse(self.path).path)
  if not m:return json_response(self,404,{'error':'not found'})
  with LOCK:
   j=jobs.get(m.group(1))
   if not j:return json_response(self,404,{'error':'job not found'})
   if j['status'] in ('queued','running'): j['cancel_requested']=True
  return json_response(self,200,{'ok':True})
if __name__=='__main__': ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8081'))),Handler).serve_forever()
