import csv
import hashlib
import io
import json
import os
import secrets
import sqlite3
import zipfile
from xml.etree.ElementTree import ParseError
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from domain import days, capacity as available_capacity
from flask import Flask, jsonify, request, session, send_file
from openpyxl import Workbook, load_workbook
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).parent
EDIT = {'Administrator', 'Operations'}
REVIEW = EDIT | {'Project Manager'}
READ_ALL = REVIEW | {'Finance'}


def create_app(database=None, testing=False):
    app = Flask(__name__, static_folder='static')
    production = os.environ.get('APP_ENV') == 'production'
    if production and (not os.environ.get('SESSION_SECRET') or len(os.environ.get('SESSION_SECRET','')) < 32):
        raise RuntimeError('Production requires SESSION_SECRET with at least 32 characters')
    local = Path(os.environ.get('DATA_DIR') or ROOT / '.local')
    local.mkdir(exist_ok=True)
    keyfile = local / 'session-key'
    if not keyfile.exists():
        keyfile.write_text(secrets.token_hex(32))
        keyfile.chmod(0o600)
    app.config.update(SECRET_KEY=os.environ.get('SESSION_SECRET') or keyfile.read_text(),
                      DATABASE=str(database or local / 'resource.db'), TESTING=testing,
                      MAX_CONTENT_LENGTH=10 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=production)

    def db():
        con = sqlite3.connect(app.config['DATABASE'])
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        return con

    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, resource_id TEXT, password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS resources(id TEXT PRIMARY KEY, name TEXT NOT NULL, title TEXT, team TEXT, employment TEXT, manager TEXT, location TEXT, timezone TEXT, hours REAL CHECK(hours>=0 AND hours<=168), start TEXT, end TEXT, status TEXT DEFAULT 'Active', pattern TEXT DEFAULT '[8,8,8,8,8,0,0]');
        CREATE TABLE IF NOT EXISTS capacity_changes(id INTEGER PRIMARY KEY, resource_id TEXT REFERENCES resources(id), effective TEXT, hours REAL, UNIQUE(resource_id,effective));
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, name TEXT NOT NULL, client TEXT, pm TEXT REFERENCES users(id), start TEXT, end TEXT, status TEXT, billable INTEGER, budget REAL);
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, title TEXT NOT NULL, project_id TEXT REFERENCES projects(id), start TEXT, end TEXT, hours REAL CHECK(hours>0 AND hours<=168), state TEXT, notes TEXT, version INTEGER, owner TEXT REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS assignments(task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE, resource_id TEXT REFERENCES resources(id), PRIMARY KEY(task_id,resource_id));
        CREATE TABLE IF NOT EXISTS leave(id TEXT PRIMARY KEY, resource_id TEXT REFERENCES resources(id), start TEXT, end TEXT, hours REAL CHECK(hours>0 AND hours<=24), category TEXT, approved INTEGER);
        CREATE TABLE IF NOT EXISTS periods(id TEXT PRIMARY KEY, resource_id TEXT REFERENCES resources(id), project_id TEXT REFERENCES projects(id), start TEXT, end TEXT, state TEXT, submitted_by TEXT, approver TEXT, locked INTEGER DEFAULT 0, UNIQUE(resource_id,project_id,start,end));
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, source TEXT, actor TEXT, created TEXT, checksum TEXT, raw BLOB, filename TEXT);
        CREATE TABLE IF NOT EXISTS entries(id TEXT PRIMARY KEY, source TEXT, source_id TEXT, resource_id TEXT REFERENCES resources(id), project_id TEXT REFERENCES projects(id), work_date TEXT, hours REAL, billable INTEGER, batch_id TEXT REFERENCES batches(id), raw TEXT, UNIQUE(source,source_id));
        CREATE TABLE IF NOT EXISTS exceptions(id TEXT PRIMARY KEY, kind TEXT, resource_id TEXT, project_id TEXT, work_date TEXT, variance REAL, detail TEXT, owner TEXT, state TEXT, resolution TEXT, batch_id TEXT);
        CREATE TABLE IF NOT EXISTS statements(id TEXT PRIMARY KEY, resource_id TEXT REFERENCES resources(id), project_id TEXT REFERENCES projects(id), start TEXT, end TEXT, hours REAL, kind TEXT, actor TEXT);
        CREATE TABLE IF NOT EXISTS adjustments(id TEXT PRIMARY KEY, entry_id TEXT REFERENCES entries(id), delta REAL, reason TEXT, actor TEXT, approved_by TEXT);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, actor TEXT, entity TEXT, entity_id TEXT, action TEXT, before_json TEXT, after_json TEXT, created TEXT);
        ''')
        if 'unit' not in [r[1] for r in c.execute('PRAGMA table_info(tasks)')]:
            c.execute("ALTER TABLE tasks ADD COLUMN unit TEXT DEFAULT 'day'")
        # Upgrade the initial daily-only allocation constraint without discarding records.
        if 'hours<=24' in c.execute("SELECT sql FROM sqlite_master WHERE name='tasks'").fetchone()[0]:
            c.commit()
            c.executescript("""
            PRAGMA foreign_keys=OFF;
            BEGIN;
            CREATE TABLE tasks_upgrade(id TEXT PRIMARY KEY, title TEXT NOT NULL, project_id TEXT REFERENCES projects(id), start TEXT, end TEXT, hours REAL CHECK(hours>0 AND hours<=168), state TEXT, notes TEXT, version INTEGER, owner TEXT REFERENCES users(id), unit TEXT DEFAULT 'day');
            INSERT INTO tasks_upgrade SELECT id,title,project_id,start,end,hours,state,notes,version,owner,unit FROM tasks;
            DROP TABLE tasks;
            ALTER TABLE tasks_upgrade RENAME TO tasks;
            COMMIT;
            PRAGMA foreign_keys=ON;
            """)
        if not c.execute('SELECT 1 FROM users').fetchone():
            password = os.environ.get('APP_ADMIN_PASSWORD') or 'local-demo-only'
            if production and (password == 'local-demo-only' or len(password) < 12):
                raise RuntimeError('First production startup requires a unique APP_ADMIN_PASSWORD of 12+ characters')
            people = [('r1','Alex Morgan','Senior developer','Engineering','Employee','Olivia Chen','Sydney','Australia/Sydney'),('r2','Jamie Park','Product designer','Design','Employee','Olivia Chen','Melbourne','Australia/Melbourne'),('r3','Sam Rivera','Full-stack developer','Engineering','Contractor','Olivia Chen','Sydney','Australia/Sydney'),('r4','Taylor Brooks','QA engineer','Engineering','Employee','Olivia Chen','Brisbane','Australia/Brisbane'),('r5','Jordan Lee','Project manager','Delivery','Employee','Olivia Chen','Sydney','Australia/Sydney'),('r6','Casey Patel','Frontend developer','Engineering','Contractor','Olivia Chen','Perth','Australia/Perth')]
            for p in people:
                c.execute('INSERT INTO resources(id,name,title,team,employment,manager,location,timezone,hours,start,status) VALUES(?,?,?,?,?,?,?,?,40,?,?)', (*p,'2026-01-01','Active'))
            for uid,name,role,rid in [('admin','Olivia Chen','Administrator',None),('ops','Morgan Ellis','Operations',None),('pm','Jordan Lee','Project Manager','r5'),('dev','Alex Morgan','Developer','r1'),('finance','Robin Quinn','Finance',None),('vendor','Avery Lane','Vendor Liaison','r3')]:
                c.execute('INSERT INTO users VALUES(?,?,?,?,?)',(uid,name,role,rid,generate_password_hash(secrets.token_urlsafe(32) if production and uid!='admin' else password)))
            today = datetime.now(ZoneInfo('Australia/Sydney')).date()
            monday = today - timedelta(days=today.weekday())
            ds = lambda n: (monday+timedelta(days=n)).isoformat()
            for p in [('p1','Atlas platform','Acme Studio','pm',ds(-30),ds(70),'Active',1,960),('p2','Website redesign','Northstar','pm',ds(-10),ds(50),'Active',1,480),('p3','Internal operations','Aliitworx','admin',ds(-100),ds(100),'Active',0,320)]:
                c.execute('INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?)',p)
            seeds = [('t1','Platform development','p1',0,4,6,'Confirmed',['r1','r3']),('t2','Design system','p2',0,3,6,'Confirmed',['r2']),('t3','Regression testing','p1',1,4,5,'Confirmed',['r4']),('t4','Sprint planning','p1',0,0,2,'Confirmed',['r1','r5']),('t5','Frontend build','p2',2,8,7,'Confirmed',['r6']),('t6','API integration','p1',2,4,4,'Confirmed',['r1']),('t7','Discovery workshop','p2',7,8,4,'Tentative',['r2','r5'])]
            for tid,title,pid,s,e,h,state,ids in seeds:
                c.execute('INSERT INTO tasks(id,title,project_id,start,end,hours,state,notes,version,owner) VALUES(?,?,?,?,?,?,?,?,1,?)',(tid,title,pid,ds(s),ds(e),h,state,'','admin'))
                c.executemany('INSERT INTO assignments VALUES(?,?)',[(tid,rid) for rid in ids])
            c.execute('INSERT INTO leave VALUES(?,?,?,?,?,?,1)',('l1','r2',ds(4),ds(4),8,'Annual leave'))

    def rows(c,sql,args=()): return [dict(r) for r in c.execute(sql,args)]
    def uid(): return secrets.token_hex(8)
    def now(): return datetime.now(timezone.utc).isoformat()
    def audit(c,entity,ident,action,before=None,after=None):
        c.execute('INSERT INTO audit(actor,entity,entity_id,action,before_json,after_json,created) VALUES(?,?,?,?,?,?,?)',(session['user'],entity,ident,action,json.dumps(before),json.dumps(after),now()))
    def user(c): return dict(c.execute('SELECT id,name,role,resource_id FROM users WHERE id=?',(session['user'],)).fetchone())
    def auth(roles=None):
        def deco(f):
            @wraps(f)
            def wrapped(*a,**k):
                if 'user' not in session: return jsonify(error='Sign in to continue'),401
                if request.method != 'GET' and request.headers.get('X-CSRF-Token') != session.get('csrf'): return jsonify(error='Invalid session token'),403
                if roles:
                    with db() as c:
                        if user(c)['role'] not in roles: return jsonify(error='Your role does not allow this action'),403
                return f(*a,**k)
            return wrapped
        return deco
    def valid_date(v):
        try: return date.fromisoformat(v)
        except (ValueError,TypeError): raise ValueError('Use valid YYYY-MM-DD dates')
    def dates(data):
        a,b=valid_date(data.get('start')),valid_date(data.get('end'))
        if b<a: raise ValueError('End date must be on or after start date')
        if (b-a).days>1095: raise ValueError('Select a date range of three years or less')
        return a,b
    def number(v,low=0,high=100000):
        import math
        try: n=float(v)
        except (TypeError,ValueError): raise ValueError('Hours must be a number')
        if not math.isfinite(n) or n<low or n>high: raise ValueError(f'Hours must be between {low} and {high}')
        return n
    def scoped(u,pid,rid=None):
        if u['role'] in READ_ALL-{'Project Manager'}: return True
        if u['role']=='Project Manager':
            with db() as c: return bool(c.execute('SELECT 1 FROM projects WHERE id=? AND pm=?',(pid,u['id'])).fetchone())
        return rid==u['resource_id']
    def locked(c,rid,pid,start,end):
        return bool(c.execute('SELECT 1 FROM periods WHERE resource_id=? AND project_id=? AND locked=1 AND start<=? AND end>=?',(rid,pid,end,start)).fetchone())
    def exception(c,kind,rid,pid,day,detail,batch=None,variance=0):
        c.execute('INSERT INTO exceptions VALUES(?,?,?,?,?,?,?,?,?,?,?)',(uid(),kind,rid,pid,day,variance,detail,None,'Open','',batch))

    @app.errorhandler(ValueError)
    def invalid(e): return jsonify(error=str(e)),400
    @app.errorhandler(sqlite3.IntegrityError)
    def integrity(e): return jsonify(error='Duplicate ID or invalid linked record. Check your input.'),409
    @app.errorhandler(413)
    def too_large(e): return jsonify(error='File exceeds the 10 MB limit'),413

    @app.after_request
    def headers(r):
        r.headers['X-Content-Type-Options']='nosniff'
        r.headers['X-Frame-Options']='DENY'
        r.headers['Content-Security-Policy']="default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        return r

    @app.get('/healthz')
    def health():
        with db() as c: c.execute('SELECT 1 FROM users LIMIT 1').fetchone()
        return jsonify(status='ok')

    @app.get('/')
    def index(): return app.send_static_file('index.html')

    @app.route('/api/session',methods=['GET','POST','DELETE'])
    def login():
        if request.method=='POST':
            d=request.get_json()
            with db() as c: r=c.execute('SELECT * FROM users WHERE id=?',(d.get('username'),)).fetchone()
            if not r or not check_password_hash(r['password'],d.get('password','')): return jsonify(error='Incorrect username or password'),401
            session.clear(); session['user']=r['id']; session['csrf']=secrets.token_hex(24)
        elif request.method=='DELETE':
            if request.headers.get('X-CSRF-Token')!=session.get('csrf'): return jsonify(error='Invalid session token'),403
            session.clear()
        if 'user' not in session: return jsonify(user=None,demo=not production)
        with db() as c: return jsonify(user=user(c),csrf=session['csrf'],demo=not production)

    @app.get('/api/data')
    @auth()
    def data():
        with db() as c:
            u=user(c)
            projects=[p for p in rows(c,'SELECT * FROM projects') if scoped(u,p['id']) or (u['role'] not in READ_ALL and c.execute('SELECT 1 FROM assignments a JOIN tasks t ON t.id=a.task_id WHERE resource_id=? AND project_id=?',(u['resource_id'],p['id'])).fetchone())]
            pids={p['id'] for p in projects}
            resources=rows(c,'SELECT * FROM resources')
            if u['role'] not in READ_ALL: resources=[r for r in resources if r['id']==u['resource_id']]
            if u['role']=='Project Manager':
                rids={a['resource_id'] for a in rows(c,'SELECT a.resource_id,t.project_id FROM assignments a JOIN tasks t ON t.id=a.task_id') if a['project_id'] in pids}
                resources=[r for r in resources if r['id'] in rids or r['id']==u['resource_id']]
            rids={r['id'] for r in resources}
            tasks=[]
            for t in rows(c,'SELECT * FROM tasks'):
                if t['project_id'] not in pids: continue
                t['people']=[r['resource_id'] for r in rows(c,'SELECT resource_id FROM assignments WHERE task_id=?',(t['id'],)) if r['resource_id'] in rids]
                if t['people']: tasks.append(t)
            entries=[e for e in rows(c,'SELECT * FROM entries') if scoped(u,e['project_id'],e['resource_id'])]
            periods=[p for p in rows(c,'SELECT * FROM periods') if scoped(u,p['project_id'],p['resource_id'])]
            visible_e={e['id'] for e in entries}
            return jsonify(user=u,resources=resources,projects=projects,tasks=tasks,
                leave=[l for l in rows(c,'SELECT * FROM leave') if l['resource_id'] in rids],
                capacity_changes=[v for v in rows(c,'SELECT * FROM capacity_changes') if v['resource_id'] in rids],entries=entries,periods=periods,
                exceptions=[e for e in rows(c,'SELECT * FROM exceptions') if scoped(u,e['project_id'],e['resource_id'])],
                statements=[s for s in rows(c,'SELECT * FROM statements') if scoped(u,s['project_id'],s['resource_id'])],
                adjustments=[a for a in rows(c,'SELECT * FROM adjustments') if a['entry_id'] in visible_e],
                batches=rows(c,'SELECT id,source,actor,created,filename FROM batches ORDER BY created DESC') if u['role'] in EDIT|{'Vendor Liaison'} else [],
                users=rows(c,'SELECT id,name,role,resource_id FROM users') if u['role'] in REVIEW else [],
                audit=rows(c,'SELECT * FROM audit ORDER BY id DESC LIMIT 300') if u['role'] in EDIT else [])

    @app.post('/api/resources')
    @auth(EDIT)
    def save_resource():
        d=request.get_json(); dates(d)
        ident=d.get('id','').strip(); name=d.get('name','').strip()
        if not ident or not name: raise ValueError('Resource ID and name are required')
        hours=number(d.get('hours',40),0,168)
        pattern=d.get('pattern',[hours/5]*5+[0,0])
        if len(pattern)!=7 or any(number(h,0,24)!=h for h in pattern) or abs(sum(pattern)-hours)>.01: raise ValueError('Daily work pattern must have seven values totaling weekly hours')
        with db() as c:
            old=c.execute('SELECT * FROM resources WHERE id=?',(ident,)).fetchone()
            if old and not d.get('editing'): raise ValueError('Resource ID already exists')
            vals=(name,d.get('title',''),d.get('team',''),d.get('employment','Employee'),d.get('manager',''),d.get('location',''),d.get('timezone','Australia/Sydney'),hours,d['start'],d['end'],d.get('status','Active'),json.dumps(pattern),ident)
            if old: c.execute('UPDATE resources SET name=?,title=?,team=?,employment=?,manager=?,location=?,timezone=?,hours=?,start=?,end=?,status=?,pattern=? WHERE id=?',vals)
            else: c.execute('INSERT INTO resources(name,title,team,employment,manager,location,timezone,hours,start,end,status,pattern,id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',vals)
            audit(c,'resource',ident,'Update' if old else 'Create',dict(old) if old else None,d)
        return jsonify(ok=True)

    @app.post('/api/capacity')
    @auth(EDIT)
    def capacity():
        d=request.get_json(); valid_date(d.get('effective')); hours=number(d.get('hours'),0,168)
        with db() as c:
            c.execute('INSERT INTO capacity_changes(resource_id,effective,hours) VALUES(?,?,?) ON CONFLICT(resource_id,effective) DO UPDATE SET hours=excluded.hours',(d['resource_id'],d['effective'],hours))
            audit(c,'capacity',d['resource_id'],'Change',None,d)
        return jsonify(ok=True)

    @app.post('/api/projects')
    @auth(EDIT)
    def save_project():
        d=request.get_json(); dates(d)
        if not d.get('id','').strip() or not d.get('name','').strip(): raise ValueError('Project ID and name are required')
        with db() as c:
            old=c.execute('SELECT * FROM projects WHERE id=?',(d['id'],)).fetchone()
            if old and not d.get('editing'): raise ValueError('Project ID already exists')
            vals=(d['name'],d.get('client',''),d['pm'],d['start'],d['end'],d.get('status','Active'),bool(d.get('billable')),number(d.get('budget',0)),d['id'])
            if old: c.execute('UPDATE projects SET name=?,client=?,pm=?,start=?,end=?,status=?,billable=?,budget=? WHERE id=?',vals)
            else: c.execute('INSERT INTO projects(name,client,pm,start,end,status,billable,budget,id) VALUES(?,?,?,?,?,?,?,?,?)',vals)
            audit(c,'project',d['id'],'Update' if old else 'Create',dict(old) if old else None,d)
        return jsonify(ok=True)

    @app.route('/api/tasks',methods=['POST','DELETE'])
    @auth(REVIEW)
    def task():
        d=request.get_json()
        with db() as c:
            old=c.execute('SELECT * FROM tasks WHERE id=?',(d.get('id'),)).fetchone()
            if not scoped(user(c),old['project_id'] if old else d.get('project_id')): return jsonify(error='Project access denied'),403
            before=dict(old) if old else None
            if before: before['people']=[r['resource_id'] for r in rows(c,'SELECT resource_id FROM assignments WHERE task_id=?',(old['id'],))]
            if request.method=='DELETE':
                if not old: raise ValueError('Task not found')
                c.execute('DELETE FROM tasks WHERE id=?',(d['id'],)); audit(c,'task',d['id'],'Delete',before); return jsonify(ok=True)
            dates(d); unit=d.get('unit','day')
            if unit not in ['day','week']: raise ValueError('Allocation unit must be day or week')
            h=number(d.get('hours'),.01,24 if unit=='day' else 168)
            if not d.get('title','').strip() or not d.get('people'): raise ValueError('Task title and at least one assignee are required')
            if d.get('state') not in ['Confirmed','Tentative']: raise ValueError('Invalid booking state')
            p=c.execute('SELECT * FROM projects WHERE id=?',(d.get('project_id'),)).fetchone()
            if not p or p['status']!='Active': raise ValueError('Select an active project')
            if d['start']<p['start'] or d['end']>p['end']: raise ValueError('Task dates must fall within the project dates')
            for rid in set(d['people']):
                r=c.execute('SELECT * FROM resources WHERE id=?',(rid,)).fetchone()
                if not r or r['status']!='Active': raise ValueError('Select active resources')
                if d['start']<r['start'] or (r['end'] and d['end']>r['end']): raise ValueError('Task dates must fall within resource employment dates')
            ident=old['id'] if old else uid()
            if old and d.get('version')!=old['version']: return jsonify(error='This task changed. Refresh before editing.'),409
            vals=(d['title'],d['project_id'],d['start'],d['end'],h,d['state'],d.get('notes',''),(old['version']+1) if old else 1,session['user'],ident)
            if old:
                c.execute('UPDATE tasks SET title=?,project_id=?,start=?,end=?,hours=?,state=?,notes=?,version=?,owner=? WHERE id=?',vals)
                c.execute('DELETE FROM assignments WHERE task_id=?',(ident,))
            else: c.execute('INSERT INTO tasks(title,project_id,start,end,hours,state,notes,version,owner,id) VALUES(?,?,?,?,?,?,?,?,?,?)',vals)
            c.execute('UPDATE tasks SET unit=? WHERE id=?',(unit,ident))
            c.executemany('INSERT INTO assignments VALUES(?,?)',[(ident,r) for r in set(d['people'])])
            audit(c,'task',ident,'Update' if old else 'Create',before,d)
        return jsonify(ok=True,id=ident)

    @app.post('/api/leave')
    @auth(EDIT)
    def leave():
        d=request.get_json(); dates(d)
        with db() as c:
            ident=uid(); c.execute('INSERT INTO leave VALUES(?,?,?,?,?,?,1)',(ident,d['resource_id'],d['start'],d['end'],number(d['hours'],.01,24),d.get('category','Annual leave')))
            audit(c,'leave',ident,'Approve',None,d)
        return jsonify(ok=True)

    def parse_import():
        f=request.files.get('file')
        if not f: raise ValueError('Choose a CSV or XLSX file')
        raw=f.read(); name=f.filename
        try:
            if name.lower().endswith('.xlsx'):
                if len(raw)>2*1024*1024: raise ValueError('XLSX files must be under 2 MB')
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    if sum(i.file_size for i in z.infolist())>30*1024*1024: raise ValueError('Expanded workbook exceeds 30 MB')
                wb=load_workbook(io.BytesIO(raw),read_only=True,data_only=True); it=iter(wb.active.values)
                keys=[str(k).strip() for k in next(it)]; parsed=[dict(zip(keys,r)) for r in it]; wb.close()
            elif name.lower().endswith('.csv'): parsed=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
            else: raise ValueError('Use CSV or XLSX')
        except (UnicodeError,StopIteration,KeyError, zipfile.BadZipFile, ParseError): raise ValueError('Cannot read file. Check the template format.')
        if not parsed or len(parsed)>10000: raise ValueError('Import between 1 and 10,000 rows')
        source=request.form.get('source','CSV').strip()
        if not source or len(source)>100: raise ValueError('Provide a source name of 1–100 characters')
        mapping=json.loads(request.form.get('mapping','{}'))
        required=['resource_id','project_id','work_date','hours']
        results=[]
        with db() as c:
            u=user(c)
            for index,rawrow in enumerate(parsed,2):
                row={k:rawrow.get(mapping.get(k,k)) for k in required+['source_entry_id','billable','activity']}
                errors=[]
                try:
                    if any(row[k] in [None,''] for k in required): raise ValueError('Missing required field')
                    if isinstance(row['work_date'],datetime): row['work_date']=row['work_date'].date().isoformat()
                    day=valid_date(str(row['work_date'])).isoformat(); row['work_date']=day
                    row['hours']=number(row['hours'],.01,24)
                    if not c.execute('SELECT 1 FROM resources WHERE id=?',(row['resource_id'],)).fetchone(): errors.append('Unknown resource ID')
                    if not c.execute('SELECT 1 FROM projects WHERE id=?',(row['project_id'],)).fetchone(): errors.append('Unknown project ID')
                    if not scoped(u,row['project_id'],row['resource_id']): errors.append('Outside your access scope')
                    if locked(c,row['resource_id'],row['project_id'],day,day): errors.append('Approved period is locked')
                    b=str(row.get('billable') or 'false').lower()
                    if b not in ['true','false','1','0','yes','no']: errors.append('Billable must be true or false')
                    row['billable']=b in ['true','1','yes']
                except (ValueError,TypeError) as e: errors.append(str(e))
                key=str(row.get('source_entry_id') or hashlib.sha256(json.dumps({k:row[k] for k in required+['activity','billable']},sort_keys=True,default=str).encode()).hexdigest())
                duplicate=bool(c.execute('SELECT 1 FROM entries WHERE source=? AND source_id=?',(source,key)).fetchone()) or any(r['key']==key for r in results)
                results.append(dict(row=row,key=key,errors=errors,duplicate=duplicate,line=index,raw=rawrow))
        return raw,name,source,results

    @app.post('/api/imports/<mode>')
    @auth(EDIT|{'Vendor Liaison'})
    def imports(mode):
        if mode not in ['preview','commit']: raise ValueError('Invalid import action')
        raw,name,source,result=parse_import()
        counts={'rows':len(result),'valid':sum(not r['errors'] and not r['duplicate'] for r in result),'duplicates':sum(r['duplicate'] for r in result),'errors':sum(bool(r['errors']) for r in result)}
        if mode=='commit':
            with db() as c:
                batch=uid(); c.execute('INSERT INTO batches VALUES(?,?,?,?,?,?,?)',(batch,source,session['user'],now(),hashlib.sha256(raw).hexdigest(),raw,name))
                for r in result:
                    d=r['row']
                    if r['errors']:
                        exception(c,'Import mapping',d.get('resource_id'),d.get('project_id'),str(d.get('work_date') or ''),f"Row {r['line']}: {', '.join(r['errors'])}. Raw: {json.dumps(r['raw'],default=str)}",batch)
                        continue
                    if r['duplicate']: continue
                    c.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?)',(uid(),source,r['key'],d['resource_id'],d['project_id'],d['work_date'],d['hours'],d['billable'],batch,json.dumps(r['raw'],default=str)))
                    if c.execute('SELECT 1 FROM leave WHERE resource_id=? AND approved=1 AND start<=? AND end>=?',(d['resource_id'],d['work_date'],d['work_date'])).fetchone(): exception(c,'Leave conflict',d['resource_id'],d['project_id'],d['work_date'],'Time recorded on approved non-working time',batch)
                    p=c.execute('SELECT billable FROM projects WHERE id=?',(d['project_id'],)).fetchone()
                    if bool(p['billable'])!=d['billable']: exception(c,'Billable mismatch',d['resource_id'],d['project_id'],d['work_date'],'Entry billable flag differs from project',batch)
                audit(c,'import',batch,'Import',None,counts)
        return jsonify(**counts,preview=result[:100])

    @app.post('/api/periods')
    @auth(REVIEW|{'Developer'})
    def period():
        d=request.get_json()
        with db() as c:
            u=user(c); action=d.get('action'); old=c.execute('SELECT * FROM periods WHERE id=?',(d.get('id'),)).fetchone()
            if not old:
                dates(d)
                if action!='Create': raise ValueError('Reporting period not found')
                if not scoped(u,d['project_id'],d['resource_id']): return jsonify(error='Access denied'),403
                if c.execute('SELECT 1 FROM periods WHERE resource_id=? AND project_id=? AND start<=? AND end>=?',(d['resource_id'],d['project_id'],d['end'],d['start'])).fetchone(): raise ValueError('Reporting periods cannot overlap for a resource and project')
                ident=uid(); c.execute('INSERT INTO periods VALUES(?,?,?,?,?,?,?,NULL,0)',(ident,d['resource_id'],d['project_id'],d['start'],d['end'],'Draft',session['user']))
                audit(c,'period',ident,'Create',None,d); return jsonify(ok=True)
            if not scoped(u,old['project_id'],old['resource_id']): return jsonify(error='Access denied'),403
            transitions={'Submit':(['Draft','Returned'],'Submitted'),'Review':(['Submitted'],'Under Review'),'Approve':(['Under Review'],'Approved'),'Return':(['Under Review'],'Returned'),'Reopen':(['Approved'],'Draft')}
            if action not in transitions: raise ValueError('Invalid transition')
            allowed,target=transitions[action]
            if old['state'] not in allowed: raise ValueError('Invalid state transition')
            if action in ['Review','Approve','Return'] and u['role'] not in REVIEW: return jsonify(error='Approver permission required'),403
            if action=='Reopen' and u['role'] not in EDIT: return jsonify(error='Operations permission required'),403
            if action in ['Return','Reopen'] and not d.get('reason','').strip(): raise ValueError('A reason is required')
            if action=='Approve':
                if c.execute('SELECT 1 FROM adjustments a JOIN entries e ON e.id=a.entry_id WHERE a.approved_by IS NULL AND e.resource_id=? AND e.project_id=? AND e.work_date BETWEEN ? AND ?', (old['resource_id'],old['project_id'],old['start'],old['end'])).fetchone(): raise ValueError('Approve pending corrections before approving the period')
                if old['resource_id']==u['resource_id'] or old['submitted_by']==u['id']: return jsonify(error='You cannot approve your own timesheet'),403
                if c.execute("SELECT 1 FROM exceptions WHERE resource_id=? AND project_id=? AND work_date BETWEEN ? AND ? AND state!='Resolved'",(old['resource_id'],old['project_id'],old['start'],old['end'])).fetchone(): raise ValueError('Resolve period exceptions before approval')
                if not c.execute('SELECT 1 FROM entries WHERE resource_id=? AND project_id=? AND work_date BETWEEN ? AND ?',(old['resource_id'],old['project_id'],old['start'],old['end'])).fetchone(): raise ValueError('Cannot approve an empty timesheet')
            if action=='Submit':
                resource=dict(c.execute('SELECT * FROM resources WHERE id=?',(old['resource_id'],)).fetchone())
                leave=rows(c,'SELECT * FROM leave WHERE resource_id=?',(resource['id'],))
                changes=rows(c,'SELECT * FROM capacity_changes WHERE resource_id=?',(resource['id'],))
                for day in days(old['start'],old['end']):
                    allocated=c.execute("SELECT 1 FROM tasks t JOIN assignments a ON t.id=a.task_id WHERE a.resource_id=? AND t.project_id=? AND t.state='Confirmed' AND t.start<=? AND t.end>=?",(resource['id'],old['project_id'],day,day)).fetchone()
                    entered=c.execute('SELECT 1 FROM entries WHERE resource_id=? AND project_id=? AND work_date=?',(resource['id'],old['project_id'],day)).fetchone()
                    existing=c.execute("SELECT 1 FROM exceptions WHERE kind='Missing day' AND resource_id=? AND project_id=? AND work_date=?",(resource['id'],old['project_id'],day)).fetchone()
                    if allocated and available_capacity(resource,day,leave,changes)>0 and not entered and not existing:
                        exception(c,'Missing day',resource['id'],old['project_id'],day,'Confirmed allocation has no source time for this working day')
            c.execute('UPDATE periods SET state=?,locked=?,approver=?,submitted_by=? WHERE id=?',(target,target=='Approved',u['id'] if action=='Approve' else None,u['id'] if action=='Submit' else old['submitted_by'],old['id']))
            audit(c,'period',old['id'],action,dict(old),d)
        return jsonify(ok=True)

    @app.post('/api/adjustments')
    @auth(REVIEW|{'Developer'})
    def adjustment():
        d=request.get_json()
        with db() as c:
            u=user(c)
            if d.get('approve'):
                a=c.execute('SELECT * FROM adjustments WHERE id=?',(d['id'],)).fetchone()
                if not a: raise ValueError('Adjustment not found')
                e=c.execute('SELECT * FROM entries WHERE id=?',(a['entry_id'],)).fetchone()
                if u['role'] not in REVIEW or a['actor']==u['id'] or e['resource_id']==u['resource_id'] or not scoped(u,e['project_id'],e['resource_id']): return jsonify(error='Independent authorised approver required'),403
                if locked(c,e['resource_id'],e['project_id'],e['work_date'],e['work_date']): raise ValueError('Reopen the locked period before correction')
                if a['approved_by']: raise ValueError('Adjustment already approved')
                c.execute('UPDATE adjustments SET approved_by=? WHERE id=?',(u['id'],a['id'])); ident=a['id']
            else:
                e=c.execute('SELECT * FROM entries WHERE id=?',(d['entry_id'],)).fetchone()
                if not e or not scoped(u,e['project_id'],e['resource_id']): return jsonify(error='Access denied'),403
                if locked(c,e['resource_id'],e['project_id'],e['work_date'],e['work_date']): raise ValueError('Reopen the locked period before correction')
                if not d.get('reason','').strip(): raise ValueError('Correction reason required')
                delta=number(d['delta'],-24,24)
                total=e['hours']+c.execute('SELECT COALESCE(SUM(delta),0) FROM adjustments WHERE entry_id=?',(e['id'],)).fetchone()[0]+delta
                if total<0 or total>24: raise ValueError('Corrected entry must total 0–24 hours')
                ident=uid(); c.execute('INSERT INTO adjustments VALUES(?,?,?,?,?,NULL)',(ident,e['id'],delta,d['reason'],u['id']))
            for p in rows(c,"SELECT * FROM periods WHERE resource_id=? AND project_id=? AND start<=? AND end>=? AND state IN ('Submitted','Under Review')",(e['resource_id'],e['project_id'],e['work_date'],e['work_date'])):
                c.execute("UPDATE periods SET state='Returned' WHERE id=?",(p['id'],))
                audit(c,'period',p['id'],'Return for correction',p,{'reason':'Correction requires resubmission'})
            audit(c,'adjustment',ident,'Approve' if d.get('approve') else 'Propose',None,d)
        return jsonify(ok=True)

    @app.post('/api/statements')
    @auth(REVIEW|{'Vendor Liaison'})
    def statement():
        d=request.get_json(); dates(d)
        with db() as c:
            if not scoped(user(c),d['project_id'],d['resource_id']): return jsonify(error='Access denied'),403
            h=number(d['hours']); ident=uid()
            c.execute('INSERT INTO statements VALUES(?,?,?,?,?,?,?,?)',(ident,d['resource_id'],d['project_id'],d['start'],d['end'],h,d.get('kind','Vendor'),session['user']))
            actual=c.execute('SELECT COALESCE(SUM(hours),0) FROM entries WHERE resource_id=? AND project_id=? AND work_date BETWEEN ? AND ?',(d['resource_id'],d['project_id'],d['start'],d['end'])).fetchone()[0]
            delta=c.execute('SELECT COALESCE(SUM(a.delta),0) FROM adjustments a JOIN entries e ON e.id=a.entry_id WHERE a.approved_by IS NOT NULL AND e.resource_id=? AND e.project_id=? AND e.work_date BETWEEN ? AND ?',(d['resource_id'],d['project_id'],d['start'],d['end'])).fetchone()[0]
            variance=h-actual-delta
            if abs(variance)>.001: exception(c,'Total difference',d['resource_id'],d['project_id'],d['start'],f"{d.get('kind','Vendor')} {h:g}h vs source {actual+delta:g}h for {d['start']}–{d['end']}",variance=variance)
            audit(c,'statement',ident,'Reconcile',None,{**d,'variance':variance})
        return jsonify(ok=True,variance=variance)

    @app.post('/api/exceptions')
    @auth(REVIEW|{'Vendor Liaison'})
    def resolve():
        d=request.get_json()
        with db() as c:
            old=c.execute('SELECT * FROM exceptions WHERE id=?',(d['id'],)).fetchone()
            if not old or not scoped(user(c),old['project_id'],old['resource_id']): return jsonify(error='Access denied'),403
            if d.get('state') not in ['Open','In review','Resolved']: raise ValueError('Invalid exception state')
            if d['state']=='Resolved' and not d.get('resolution','').strip(): raise ValueError('Resolution comment required')
            c.execute('UPDATE exceptions SET owner=?,state=?,resolution=? WHERE id=?',(d.get('owner'),d['state'],d.get('resolution',''),d['id']))
            audit(c,'exception',d['id'],'Update',dict(old),d)
        return jsonify(ok=True)

    @app.get('/api/export')
    @auth(READ_ALL)
    def export():
        start,end=dates(request.args); pid=request.args.get('project',''); rid=request.args.get('resource',''); selected=set(filter(None,request.args.get('resources','').split(',')))
        with db() as c:
            u=user(c); result=[]
            for e in rows(c,'''SELECT e.*,r.name resource,p.name project FROM entries e JOIN resources r ON r.id=e.resource_id JOIN projects p ON p.id=e.project_id WHERE work_date BETWEEN ? AND ? AND EXISTS(SELECT 1 FROM periods t WHERE t.resource_id=e.resource_id AND t.project_id=e.project_id AND e.work_date BETWEEN t.start AND t.end AND t.state='Approved' AND t.locked=1) ORDER BY work_date''',(start.isoformat(),end.isoformat())):
                if not scoped(u,e['project_id'],e['resource_id']) or (pid and pid!=e['project_id']) or (rid and rid!=e['resource_id']) or (selected and e['resource_id'] not in selected): continue
                delta=c.execute('SELECT COALESCE(SUM(delta),0) FROM adjustments WHERE entry_id=? AND approved_by IS NOT NULL',(e['id'],)).fetchone()[0]
                result.append([e['work_date'],e['resource_id'],e['resource'],e['project_id'],e['project'],e['hours']+delta,bool(e['billable']),e['source'],e['source_id'],e['batch_id'],now(),start.isoformat(),end.isoformat(),pid,rid,','.join(sorted(selected))])
            audit(c,'report','approved','Export',None,dict(request.args))
        columns=['work_date','resource_id','resource','project_id','project','approved_hours','billable','source','source_entry_id','batch_id','exported_at','filter_start','filter_end','filter_project','filter_resource','filter_resources']
        def safe(v): return "'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v
        result=[[safe(v) for v in row] for row in result]
        if request.args.get('format')=='xlsx':
            wb=Workbook(); ws=wb.active; ws.title='Approved hours'; ws.append(columns)
            for row in result: ws.append(row)
            out=io.BytesIO(); wb.save(out); out.seek(0)
            return send_file(out,download_name=f'approved-hours-{start}-{end}.xlsx',as_attachment=True)
        out=io.StringIO(); writer=csv.writer(out); writer.writerow(columns); writer.writerows(result)
        return send_file(io.BytesIO(out.getvalue().encode()),download_name=f'approved-hours-{start}-{end}.csv',as_attachment=True,mimetype='text/csv')

    @app.get('/api/template')
    @auth()
    def template():
        return send_file(io.BytesIO(b'source_entry_id,resource_id,project_id,work_date,hours,billable,activity\nexample-001,r1,p1,2026-11-09,8,true,Development\n'),download_name='time-import-template.csv',as_attachment=True,mimetype='text/csv')

    @app.get('/api/batches/<ident>')
    @auth(EDIT)
    def raw_batch(ident):
        with db() as c: b=c.execute('SELECT * FROM batches WHERE id=?',(ident,)).fetchone()
        if not b: return jsonify(error='Batch not found'),404
        return send_file(io.BytesIO(b['raw']),download_name=b['filename'],as_attachment=True)

    # Versioned API is canonical; retain the short routes for existing local clients.
    for rule in list(app.url_map.iter_rules()):
        if rule.rule.startswith('/api/'):
            app.add_url_rule('/api/v1/'+rule.rule[5:],endpoint='v1_'+rule.endpoint,view_func=app.view_functions[rule.endpoint],methods=rule.methods)
    return app

if __name__=='__main__':
    create_app().run(host='0.0.0.0',port=int(os.environ.get('PORT',3000)),debug=False)
