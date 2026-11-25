"""Local-only BiliNote UI bridge and non-destructive Obsidian exporter. Stdlib only."""
import argparse
import hashlib
import html
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, unquote, quote
import webbrowser
from note_documents import write_word

HERE = Path(__file__).resolve().parent
PROMPT = (HERE / 'knowledge_prompt.txt').read_text(encoding='utf-8')

def read_json(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))

def atomic(p, text):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    tmp.replace(p)

def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()

def stamp(t):
    n = max(0, int(float(t)))
    return f'{n//3600:02d}:{n//60%60:02d}:{n%60:02d}'

def safe_name(s):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f\[\]#^]', '_', s).strip(' .')[:90] or '未命名视频'

def timestamp_links(markdown, source):
    parsed = urlsplit(source)
    is_video = parsed.hostname in ('www.bilibili.com','bilibili.com','www.youtube.com','youtube.com','youtu.be')
    def replace(m):
        parts = [int(x) for x in m.group(1).split(':')]
        seconds = parts[0]*60+parts[1] if len(parts)==2 else parts[0]*3600+parts[1]*60+parts[2]
        if not is_video: return f'（{m.group(1)}）'
        query = [(k,v) for k,v in parse_qsl(parsed.query,keep_blank_values=True) if k!='t']
        query.append(('t',str(seconds)))
        url=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urlencode(query),parsed.fragment))
        return f'[回看 {m.group(1)}]({url})'
    return re.sub(r'\*?Content-\[?(\d{1,3}:\d{2}(?::\d{2})?)\]?\*?',replace,markdown)

def enrich(data):
    data = dict(data)
    data['extras'] = str(data.get('extras') or '') + '\n\n' + (HERE / 'knowledge_prompt.txt').read_text(encoding='utf-8')
    data['style'] = 'detailed'
    data['format'] = list(dict.fromkeys([*(data.get('format') or []), 'toc', 'link', 'summary']))
    data['link'] = True
    return data

class Exporter:
    def __init__(self, app, vault, state_dir, standalone=False):
        self.app, self.vault, self.state_dir = Path(app), Path(vault), Path(state_dir)
        self.target = self.vault / '视频笔记'
        self.state_path = self.state_dir / 'exports.json'
        self.state = read_json(self.state_path) if self.state_path.exists() else {}
        self.error = ''
        self.standalone = standalone

    def copy_images(self, markdown, task):
        def replace(m):
            url = m.group(2)
            parsed = urlsplit(url)
            if parsed.hostname not in (None, 'localhost', '127.0.0.1'): return m.group(0)
            if not parsed.path.startswith('/static/'): return m.group(0)
            source = (self.app / unquote(parsed.path.lstrip('/'))).resolve()
            if not source.is_relative_to((self.app/'static').resolve()) or not source.is_file(): return m.group(0)
            name = digest(source.read_bytes().hex())[:16] + source.suffix
            dest = self.target / '附件' / task / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists(): shutil.copy2(source, dest)
            return f'![{m.group(1)}](<{dest.relative_to(self.target).as_posix()}>)'
        return re.sub(r'!\[([^\]]*)\]\(([^)\s]+)\)', replace, markdown)

    def export(self, file):
        file = Path(file)
        task = file.stem
        if not re.fullmatch(r'[a-f0-9-]{36}', task): return False
        status = file.with_name(task+'.status.json')
        if not status.exists() or read_json(status).get('status') != 'SUCCESS': return False
        raw = file.read_text(encoding='utf-8')
        source_hash = digest('export-v2\n'+raw)
        old = self.state.get(task, {})
        if old.get('source_hash') == source_hash and Path(old.get('path', '')).is_file():
            if self.standalone: write_word(Path(old['path']))
            return False
        data = json.loads(raw)
        if not isinstance(data.get('markdown'), str) or not data['markdown'].strip(): return False
        meta = data.get('audio_meta') or {}
        title = str(meta.get('title') or '未命名视频')
        video_id = meta.get('video_id', '')
        source = (meta.get('raw_info') or {}).get('webpage_url', '')
        if not source and meta.get('platform') == 'bilibili': source = 'https://www.bilibili.com/video/'+video_id
        if not source and meta.get('platform') == 'youtube': source = 'https://www.youtube.com/watch?v='+video_id
        markdown = timestamp_links(self.copy_images(data['markdown'], task),source)
        markdown = re.sub(r'(?m)^([123])\\\.\s',r'\1. ',markdown)
        date = time.strftime('%Y-%m-%d', time.localtime(file.stat().st_mtime))
        body = f'---\ntitle: {json.dumps(title,ensure_ascii=False)}\ntags: [视频笔记]\nbilinote_task: {task}\n生成日期: {date}\n---\n\n# {title}\n\n'
        if source.startswith(('https://','http://')): body += f'[原视频]({source})\n\n'
        body += '> 由 BiliNote 自动归档。内容来自字幕或转写及 AI 总结，重要信息请回看核对。历史笔记按原格式导入。\n\n'+markdown
        segments = (data.get('transcript') or {}).get('segments') or []
        if segments:
            body += '\n\n## 带时间戳的转写原文\n\n<details>\n<summary>展开转写原文，核对时间和上下文</summary>\n\n'
            for s in segments:
                try: a,b = stamp(s['start']),stamp(s['end'])
                except (ValueError,TypeError,KeyError): continue
                body += f'- **{a}—{b}** {str(s.get("text","")).strip()}\n'
            body += '\n</details>\n'
        name = f'{date} {safe_name(title)} [{task[:8]}].md'
        dest = self.target / name
        if old.get('path'):
            previous = Path(old['path'])
            if previous.is_relative_to(self.target): dest = previous
        if dest.exists():
            existing = dest.read_text(encoding='utf-8')
            if existing != body and digest(existing) != old.get('written_hash'):
                dest = self.target / f'{Path(name).stem} 更新-{source_hash[:8]}.md'
                if dest.exists() and dest.read_text(encoding='utf-8') != body:
                    raise ValueError('同名更新笔记已有修改，已暂停覆盖')
        atomic(dest, body)
        if self.standalone: write_word(dest)
        self.state[task] = {'source_hash':source_hash,'written_hash':digest(body),'path':str(dest),'title':title}
        atomic(self.state_path, json.dumps(self.state, ensure_ascii=False, indent=2))
        return True

    def scan(self):
        if not self.standalone and not (self.vault/'.obsidian').is_dir():
            self.error = '找不到指定 Obsidian 笔记库'; return
        self.error = ''
        for p in (self.app/'note_results').glob('*.json'):
            try: self.export(p)
            except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError) as e:
                self.error = '归档尚未完成：'+type(e).__name__

    def watch(self):
        while True:
            self.scan()
            time.sleep(5)

def open_window(url):
    for root in ('ProgramFiles(x86)', 'ProgramFiles', 'LOCALAPPDATA'):
        edge = Path(os.environ.get(root, ''))/'Microsoft/Edge/Application/msedge.exe'
        if edge.is_file():
            subprocess.Popen([str(edge), '--app='+url])
            return
    webbrowser.open(url)

def ensure_backend(app, port):
    def ready():
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
        try:
            conn.request('GET', '/openapi.json')
            return '/api/generate_note' in json.load(conn.getresponse())['paths']
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            return False
        finally:
            conn.close()
    if ready(): return
    env = dict(os.environ, BACKEND_HOST='127.0.0.1', BACKEND_PORT=str(port))
    bins = [str(p.parent) for p in (app.parent/'Tools').glob('*/bin/ffmpeg.exe')]
    env['PATH'] = os.pathsep.join(bins+[env.get('PATH','')])
    child = subprocess.Popen([str(app/'BiliNoteBackend.exe')], cwd=app,
                             env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(120):
        if ready(): return
        if child.poll() is not None: break
        time.sleep(0.5)
    raise RuntimeError('后台未就绪，请查看原版 App/logs 中的日志。')

def serve(config, open_browser=True):
    app = Path(config['app'])
    state_dir = Path(config.get('state_dir', HERE/'state'))
    exp = Exporter(app, config.get('export_dir', str(Path.home()/'Documents'/'BiliNote')), state_dir/'local', standalone=True)
    settings_path = state_dir/'save_settings.json'
    settings = read_json(settings_path) if settings_path.exists() else {'obsidian': False}
    obs = Exporter(app, config.get('vault', str(Path.home()/'Documents'/'Obsidian Vault')), state_dir)
    port = config.get('port',3016)
    backend_port = config.get('backend_port',8483)
    base = f'http://127.0.0.1:{port}'
    dist = Path(config['dist'])
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(dist),**kwargs)
        def log_message(self,*args): pass # Never log request bodies or credentials.
        def local(self):
            host = self.headers.get('Host','')
            origin = self.headers.get('Origin')
            return host in (f'127.0.0.1:{port}', f'localhost:{port}') and (not origin or origin in (base,f'http://localhost:{port}'))
        def reply(self, data, content_type='application/json; charset=utf-8'):
            b = data.encode('utf-8'); self.send_response(200)
            self.send_header('Content-Type',content_type); self.send_header('Content-Length',str(len(b)))
            self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(b)
        def do_GET(self):
            if not self.local(): self.send_error(403); return
            if self.path == '/custom/status':
                self.reply(json.dumps({'app':'bilinote-knowledge','count':len(exp.state),'error':exp.error or (obs.error if settings['obsidian'] else '')},ensure_ascii=False)); return
            if self.path.startswith('/custom/download?'):
                query = dict(parse_qsl(urlsplit(self.path).query))
                item = exp.state.get(query.get('task'))
                if not item or query.get('format') not in ('md','docx'):
                    self.send_error(404); return
                path = Path(item['path']).with_suffix('.'+query['format'])
                if not path.is_file(): self.send_error(404); return
                data = path.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type','application/octet-stream')
                self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(path.name))
                self.send_header('Content-Length',str(len(data)))
                self.end_headers(); self.wfile.write(data); return
            if self.path == '/custom/library':
                links=''.join(f'<li>{html.escape(v["title"])} · <a href="/custom/download?task={k}&format=docx">下载 Word</a> · <a href="/custom/download?task={k}&format=md">下载 Markdown</a></li>' for k,v in list(exp.state.copy().items())[::-1])
                checked = 'checked' if settings['obsidian'] else ''
                self.reply('<!doctype html><meta charset="utf-8"><title>我的笔记</title><style>body{font:17px system-ui;max-width:900px;margin:50px auto;line-height:2}a{color:#2563eb}li{margin:18px 0}</style><h1>我的笔记</h1><p>笔记自动保存为 Word 和 Markdown，保存位置：'+html.escape(str(exp.target))+'</p><p>Word 文件可用 Word 或 WPS 打开，再选择导出为 PDF。</p><label><input type="checkbox" '+checked+' onchange="this.disabled=true;fetch(\'/custom/settings\',{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify({obsidian:this.checked})}).then(r=>{if(!r.ok)throw Error();location.reload()}).catch(()=>{alert(\'保存失败，请重试\');location.reload()})">同时保存到 Obsidian</label><p>'+html.escape(exp.error or (obs.error if settings['obsidian'] else ''))+'</p><ul>'+links+'</ul>','text/html; charset=utf-8'); return
                self.reply('<!doctype html><meta charset="utf-8"><title>视频笔记</title><style>body{font:18px system-ui;max-width:850px;margin:60px auto;line-height:2}a{color:#2563eb}</style><h1>已归档的视频笔记</h1><p>点击标题在 Obsidian 中打开。新生成的笔记通常在5秒内归档。</p><ul>'+links+'</ul><button onclick="fetch(\'/custom/stop\',{method:\'POST\'}).then(()=>{this.textContent=\'已停止，关闭此页面即可\';this.disabled=true})">停止知识笔记版服务</button>','text/html; charset=utf-8'); return
            if self.path.startswith(('/api/','/static/','/uploads/')): self.proxy(); return
            clean = unquote(urlsplit(self.path).path).lstrip('/')
            if not (dist/clean).is_file(): self.path='/index.html'
            super().do_GET()
        def do_POST(self):
            if self.path == '/custom/settings' and self.local():
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length < 1024: raise ValueError()
                    data = json.loads(self.rfile.read(length))
                    if type(data.get('obsidian')) is not bool: raise ValueError()
                    atomic(settings_path, json.dumps({'obsidian':data['obsidian']}))
                    settings['obsidian'] = data['obsidian']
                    self.reply('{"saved":true}')
                except (ValueError, OSError): self.send_error(400)
                return
            if self.path == '/custom/stop' and self.local():
                self.reply('{"stopped":true}')
                threading.Thread(target=server.shutdown,daemon=True).start()
                return
            self.proxy()
        def do_PUT(self): self.proxy()
        def do_PATCH(self): self.proxy()
        def do_DELETE(self): self.proxy()
        def proxy(self):
            if not self.local() or not self.path.startswith(('/api/','/static/','/uploads/')):
                self.send_error(403); return
            length = int(self.headers.get('Content-Length','0'))
            conn = http.client.HTTPConnection('127.0.0.1',backend_port, timeout=180)
            replied = False
            try:
                headers={k:v for k,v in self.headers.items() if k.lower() not in ('host','connection','content-length','accept-encoding')}
                headers['Connection']='close'; headers['Accept-Encoding']='identity'
                if self.path == '/api/generate_note' and self.command=='POST':
                    if length>4*1024*1024: self.send_error(413); return
                    body=json.dumps(enrich(json.loads(self.rfile.read(length)))).encode()
                    headers['Content-Length']=str(len(body))
                    conn.request(self.command,self.path,body,headers)
                else:
                    conn.putrequest(self.command,self.path)
                    for k,v in headers.items(): conn.putheader(k,v)
                    conn.putheader('Content-Length',str(length)); conn.endheaders()
                    remaining=length
                    while remaining:
                        chunk=self.rfile.read(min(1024*1024,remaining))
                        if not chunk: raise ConnectionError('Incomplete upload')
                        conn.send(chunk); remaining-=len(chunk)
                response=conn.getresponse(); self.send_response(response.status)
                replied = True
                for k,v in response.getheaders():
                    if k.lower() not in ('connection','transfer-encoding','content-length'): self.send_header(k,v)
                self.send_header('Connection','close'); self.end_headers()
                while True:
                    chunk=response.read1(65536)
                    if not chunk: break
                    self.wfile.write(chunk); self.wfile.flush()
                self.close_connection=True
            except (OSError,ValueError,http.client.HTTPException):
                if not replied:
                    try: self.send_error(502,'BiliNote backend is not ready. Please retry.')
                    except OSError: pass
                self.close_connection=True
            finally: conn.close()
    class LocalServer(ThreadingHTTPServer):
        allow_reuse_address = False
        def server_bind(self):
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()
    try:
        server = LocalServer(('127.0.0.1', port), Handler)
    except OSError:
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        try:
            conn.request('GET', '/custom/status')
            if json.load(conn.getresponse()).get('app') != 'bilinote-knowledge':
                raise RuntimeError('知识笔记端口已被其他程序占用')
        finally:
            conn.close()
        ensure_backend(app, backend_port)
        if open_browser: open_window(base)
        return
    try:
        ensure_backend(app, backend_port)
    except Exception:
        server.server_close()
        raise
    threading.Thread(target=exp.watch,daemon=True).start()
    def sync_obsidian():
        while True:
            if settings['obsidian']: obs.scan()
            time.sleep(5)
    threading.Thread(target=sync_obsidian,daemon=True).start()
    if open_browser: open_window(base)
    try: server.serve_forever()
    finally: server.server_close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    try:
        config_file = HERE/'config.json'
        if not config_file.exists():
            import tkinter.filedialog
            folder = tkinter.filedialog.askdirectory(title='选择包含 BiliNoteBackend.exe 的原版安装目录')
            if not folder: raise SystemExit()
            if not (Path(folder)/'BiliNoteBackend.exe').is_file():
                raise ValueError('所选目录缺少 BiliNoteBackend.exe')
            atomic(config_file,json.dumps({'app':folder,'dist':str(HERE/'web')}))
        serve(read_json(config_file),not args.no_browser)
    except Exception as exc:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,str(exc),'BiliNote 知识笔记版',16)
        raise

