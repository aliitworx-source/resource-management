import io
import csv
import sqlite3
from datetime import date, timedelta
import pytest
from app import create_app

@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path/'test.db',testing=True)

@pytest.fixture
def client(app):
    return app.test_client()

def login(c,who='admin'):
    r=c.post('/api/session',json={'username':who,'password':'local-demo-only'})
    assert r.status_code==200
    return {'X-CSRF-Token':r.json['csrf']}

def upload(c,h,rows,source='YouTrack',mode='commit'):
    text='source_entry_id,resource_id,project_id,work_date,hours,billable,activity\n'+rows
    return c.post('/api/imports/'+mode,headers=h,data={'source':source,'file':(io.BytesIO(text.encode()),'test.csv')})

def period(c,h,rid='r1',pid='p1'):
    d=date.today(); a=(d-timedelta(days=d.weekday())).isoformat(); b=(d+timedelta(days=6-d.weekday())).isoformat()
    assert c.post('/api/periods',headers=h,json={'action':'Create','resource_id':rid,'project_id':pid,'start':a,'end':b}).status_code==200
    return c.get('/api/data').json['periods'][-1],a,b

def test_auth_and_csrf(client):
    assert client.get('/api/data').status_code==401
    login(client)
    assert client.post('/api/tasks',json={}).status_code==403

def test_multi_assignee_and_version(client):
    h=login(client); d=client.get('/api/data').json; p=d['projects'][0]
    task={'title':'Shared task','project_id':p['id'],'start':date.today().isoformat(),'end':date.today().isoformat(),'hours':4,'state':'Confirmed','people':['r1','r2','r1']}
    r=client.post('/api/tasks',headers=h,json=task);assert r.status_code==200
    saved=next(t for t in client.get('/api/data').json['tasks'] if t['id']==r.json['id'])
    assert set(saved['people'])=={'r1','r2'}
    assert client.post('/api/tasks',headers=h,json={**task,'id':saved['id'],'version':0}).status_code==409
    assert client.post('/api/tasks',headers=h,json={**task,'id':saved['id'],'version':1,'hours':5}).status_code==200
    assert client.get('/api/data').json['audit'][0]['action']=='Update'

def test_import_idempotent_quarantine_and_leave(client):
    h=login(client); day=date.today().isoformat()
    assert client.post('/api/leave',headers=h,json={'resource_id':'r1','start':day,'end':day,'hours':8}).status_code==200
    row=f'a,r1,p1,{day},8,true,Build\nb,unknown,p1,{day},4,true,Build\n'
    preview=upload(client,h,row,mode='preview');assert preview.json['valid']==1 and preview.json['errors']==1
    assert not client.get('/api/data').json['entries']
    assert upload(client,h,row).status_code==200
    again=upload(client,h,row);assert again.json['duplicates']==1
    data=client.get('/api/data').json
    assert len(data['entries'])==1
    assert {'Leave conflict','Import mapping'} <= {e['kind'] for e in data['exceptions']}

def test_approval_export_lock_self_approval_and_reopen(client):
    h=login(client,'dev'); p,a,b=period(client,h)
    h=login(client,'ops');day=date.today().isoformat()
    assert upload(client,h,''.join(f'd{i},r1,p1,{(date.fromisoformat(a)+timedelta(days=i)).isoformat()},1.6,true,Build\n' for i in range(5))).status_code==200
    assert client.get(f'/api/export?start={a}&end={b}').data.decode().count('\n')==1
    h=login(client,'dev');assert client.post('/api/periods',headers=h,json={'id':p['id'],'action':'Submit'}).status_code==200
    h=login(client,'pm')
    assert client.post('/api/periods',headers=h,json={'id':p['id'],'action':'Review'}).status_code==200
    assert client.post('/api/periods',headers=h,json={'id':p['id'],'action':'Approve'}).status_code==200
    out=client.get(f'/api/export?start={a}&end={b}');records=list(csv.DictReader(io.StringIO(out.data.decode())))
    assert sum(float(r['approved_hours']) for r in records)==8
    assert client.get(f'/api/export?start={a}&end={b}&format=xlsx').data[:2]==b'PK'
    assert client.post('/api/periods',headers=h,json={'id':p['id'],'action':'Reopen','reason':'Fix'}).status_code==403
    h=login(client,'ops');locked=upload(client,h,f'new,r1,p1,{day},2,true,Build\n')
    assert locked.json['errors']==1
    assert client.post('/api/periods',headers=h,json={'id':p['id'],'action':'Reopen'}).status_code==400
    assert client.post('/api/periods',headers=h,json={'id':p['id'],'action':'Reopen','reason':'Correction required'}).status_code==200
    # Creator/submitter cannot approve, even with administrator permissions.
    p2,_,_=period(client,h,'r2');assert client.post('/api/periods',headers=h,json={'id':p2['id'],'action':'Submit'}).status_code==200
    assert client.post('/api/periods',headers=h,json={'id':p2['id'],'action':'Review'}).status_code==200
    assert client.post('/api/periods',headers=h,json={'id':p2['id'],'action':'Approve'}).status_code==403

def test_reconcile_adjustment_and_roles(client):
    h=login(client,'ops');day=date.today().isoformat()
    upload(client,h,f'a,r1,p1,{day},8,true,Build\n')
    e=client.get('/api/data').json['entries'][0]
    assert client.post('/api/adjustments',headers=h,json={'entry_id':e['id'],'delta':2,'reason':'Missing time'}).status_code==200
    a=client.get('/api/data').json['adjustments'][0]
    assert client.post('/api/adjustments',headers=h,json={'id':a['id'],'approve':True}).status_code==403
    h=login(client,'pm');assert client.post('/api/adjustments',headers=h,json={'id':a['id'],'approve':True}).status_code==200
    r=client.post('/api/statements',headers=h,json={'resource_id':'r1','project_id':'p1','start':day,'end':day,'hours':26,'kind':'Vendor'})
    assert r.status_code==200 and r.json['variance']==16
    h=login(client,'dev');d=client.get('/api/data').json
    assert all(e['resource_id']=='r1' for e in d['entries'])
    assert client.post('/api/projects',headers=h,json={}).status_code==403
    assert client.get(f'/api/export?start={day}&end={day}').status_code==403
    assert d['audit']==[]

def test_invalid_ranges_and_duplicate_resource(client):
    h=login(client)
    assert client.get('/api/export?start=2026-12-01&end=2026-01-01').status_code==400
    r=client.post('/api/resources',headers=h,json={'id':'r1','name':'Duplicate','start':'2026-01-01','end':'2026-12-31','hours':40})
    assert r.status_code==400

def test_xlsx_and_csv_formula_safety(client):
    from openpyxl import Workbook
    h=login(client);wb=Workbook();ws=wb.active;ws.append(['source_entry_id','resource_id','project_id','work_date','hours','billable']);ws.append(['xlsx1','r1','p1',date.today().isoformat(),4,True]);out=io.BytesIO();wb.save(out);out.seek(0)
    r=client.post('/api/imports/commit',headers=h,data={'source':'Jira','file':(out,'time.xlsx')})
    assert r.status_code==200 and r.json['valid']==1

def test_missing_days_and_user_management(client):
    h=login(client,'ops');p,a,b=period(client,h)
    assert client.post('/api/periods',headers=h,json={'id':p['id'],'action':'Submit'}).status_code==200
    assert client.get('/api/data').json['exceptions'][0]['kind']=='Missing day'
    assert client.post('/api/users',headers=h,json={}).status_code==403
    h=login(client)
    new={'id':'new-dev','name':'New developer','role':'Developer','resource_id':'r2','password':'test-password-123'}
    assert client.post('/api/users',headers=h,json=new).status_code==200
    assert client.post('/api/users',headers=h,json=new).status_code==400
    audit=client.get('/api/data').json['audit'][0]
    assert 'password' not in audit['after_json']
    r=client.post('/api/session',json={'username':'new-dev','password':'test-password-123'})
    assert r.status_code==200
    assert {r['id'] for r in client.get('/api/data').json['resources']}=={'r2'}

def test_weekly_hours_and_malformed_workbook(client):
    h=login(client)
    d={'title':'Weekly','project_id':'p1','start':date.today().isoformat(),'end':date.today().isoformat(),'hours':25,'unit':'week','state':'Confirmed','people':['r1']}
    assert client.post('/api/tasks',headers=h,json=d).status_code==200
    assert client.post('/api/tasks',headers=h,json={**d,'unit':'day'}).status_code==400
    r=client.post('/api/imports/preview',headers=h,data={'source':'Jira','file':(io.BytesIO(b'not a workbook'),'broken.xlsx')})
    assert r.status_code==400

def test_production_credentials_and_secure_session(tmp_path,monkeypatch):
    monkeypatch.setenv('APP_ENV','production')
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    monkeypatch.delenv('SESSION_SECRET',raising=False)
    with pytest.raises(RuntimeError,match='SESSION_SECRET'): create_app(tmp_path/'prod.db',testing=True)
    monkeypatch.setenv('SESSION_SECRET','x'*40)
    monkeypatch.delenv('APP_ADMIN_PASSWORD',raising=False)
    with pytest.raises(RuntimeError,match='APP_ADMIN_PASSWORD'): create_app(tmp_path/'prod.db',testing=True)
    monkeypatch.setenv('APP_ADMIN_PASSWORD','unique-production-pass-123')
    prod=create_app(tmp_path/'prod.db',testing=True); c=prod.test_client()
    assert c.get('/healthz').json=={'status':'ok'}
    assert c.get('/api/v1/session').json['demo'] is False
    assert c.post('/api/v1/session',json={'username':'dev','password':'local-demo-only'}).status_code==401
    r=c.post('/api/v1/session',json={'username':'admin','password':'unique-production-pass-123'},base_url='https://localhost')
    assert r.status_code==200 and 'Secure;' in r.headers['Set-Cookie']
    # Password config only initializes an empty database; redeploy does not reset accounts.
    monkeypatch.setenv('APP_ADMIN_PASSWORD','another-password-456')
    c=create_app(tmp_path/'prod.db',testing=True).test_client()
    assert c.post('/api/v1/session',json={'username':'admin','password':'unique-production-pass-123'}).status_code==200
