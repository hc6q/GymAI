import importlib
import os
from fastapi.testclient import TestClient


def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'gym.db'))
    monkeypatch.setenv('SECRET_KEY', 'long-test-secret')
    monkeypatch.setenv('APP_USERNAME', '')
    monkeypatch.setenv('APP_PASSWORD', '')
    monkeypatch.delenv('GROQ_API_KEY', raising=False)
    import app.main as module
    importlib.reload(module)
    c=TestClient(module.app)
    c.get('/')
    return c

def headers(c): return {'X-CSRF-Token':c.cookies['gym_csrf']}

def test_workout_session_export_import_and_csrf(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    assert c.post('/api/workouts',json={'weekday':0,'name':'Peito'}).status_code==403
    h=headers(c)
    w=c.post('/api/workouts',headers=h,json={'weekday':0,'name':'Peito'}).json()
    assert w['id']
    e=c.post('/api/exercises',headers=h,json={'name':'Rosca do Heitor'}).json()
    entry=c.post(f'/api/workouts/{w["id"]}/exercises',headers=h,json={'exercise_id':e['id'],'sets':2,'reps':'8-10','weight':20}).json()
    assert entry['id']
    assert c.get('/api/ai/context?scope=day&weekday=0').json()['context'].find('Rosca do Heitor')>0
    assert c.post('/api/ai/ask',headers=h,json={'scope':'day','weekday':0,'question':'Analise'}).status_code==503
    s=c.post('/api/sessions',headers=h,json={'workout_id':w['id'],'sets':[{'exercise_id':e['id'],'exercise_name':'Ignorado','position':0,'set_number':1,'weight':20,'reps':8}]}).json()
    assert s['id']
    history=c.get(f'/api/history?exercise_id={e["id"]}').json()
    assert history[0]['sets'][0]['exercise_name']=='Rosca do Heitor'
    backup=c.get('/api/export').json()
    assert c.post('/api/import',headers=h,json={'bad':'data'}).status_code==422
    assert c.post('/api/import',headers=h,json=backup).status_code==200
    assert c.get('/api/history').json()[0]['id']==s['id']
    assert c.put(f'/api/workouts/{w["id"]}/order',headers=h,json={'ids':[entry['id']]}).status_code==200
    assert c.delete(f'/api/workouts/{w["id"]}/exercises/{entry["id"]}',headers=h).status_code==200


def test_optional_login(tmp_path,monkeypatch):
    monkeypatch.setenv('APP_USERNAME','owner')
    monkeypatch.setenv('APP_PASSWORD','secret')
    monkeypatch.setenv('SECRET_KEY','test-signing-secret')
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'private.db'))
    import app.main as module
    importlib.reload(module)
    c=TestClient(module.app)
    assert c.get('/api/workouts').status_code==401
    assert c.post('/api/login',json={'username':'owner','password':'wrong'}).status_code==401
    assert c.post('/api/login',json={'username':'owner','password':'secret'}).status_code==200
    assert c.get('/api/workouts').status_code==200
