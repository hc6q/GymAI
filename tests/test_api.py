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


def test_ai_size_context_cache_and_markdown(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'ai.db'))
    monkeypatch.setenv('SECRET_KEY', 'long-test-secret')
    monkeypatch.setenv('APP_USERNAME', '')
    monkeypatch.setenv('APP_PASSWORD', '')
    monkeypatch.setenv('GROQ_API_KEY', 'test-only-key')
    monkeypatch.setenv('GROQ_MODEL', 'openai/gpt-oss-120b')
    monkeypatch.setenv('GROQ_MAX_TOKENS', '400')
    import app.main as module
    importlib.reload(module)
    sent=[]
    class FakeResponse:
        def raise_for_status(self): pass
        def json(self):
            return {'choices':[{'message':{'content':'**Sim.** Uma troca basta. <script>alert(1)</script>'},'finish_reason':'stop'}], 'usage':{'prompt_tokens':30,'completion_tokens':20,'total_tokens':50}}
    class FakeClient:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,headers,json):
            sent.append(json)
            return FakeResponse()
    monkeypatch.setattr(module.httpx,'AsyncClient',FakeClient)
    c=TestClient(module.app); c.get('/'); h=headers(c)
    c.post('/api/workouts',headers=h,json={'weekday':0,'name':'Treino de segunda'})
    c.post('/api/workouts',headers=h,json={'weekday':1,'name':'Treino de terça'})
    assert c.get('/api/settings').json()['ai_response_size']=='short'
    q={'scope':'week','question':'Você mudaria algo no treino de terça?'}
    preview=c.get('/api/ai/context',params=q).json()['context']
    assert 'Treino de terça' in preview and 'Treino de segunda' not in preview
    r=c.post('/api/ai/ask',headers=h,json=q).json()
    assert r['response_size']=='short' and len(sent)==1
    assert sent[0]['max_completion_tokens']==250 and 'max_tokens' not in sent[0]
    assert sent[0]['reasoning_effort']=='low'
    assert 'Treino de segunda' not in sent[0]['messages'][1]['content']
    assert '<strong>Sim.</strong>' in r['answer_html'] and '<script>' not in r['answer_html']
    assert c.post('/api/ai/ask',headers=h,json=q).json()['cached'] is True
    assert len(sent)==1
    c.put('/api/settings',headers=h,json={'ai_response_size':'normal'})
    q['question']='Uma pergunta diferente?'
    c.post('/api/ai/ask',headers=h,json=q)
    assert sent[-1]['max_completion_tokens']==400
    q['question']='Analise detalhadamente a semana'
    r=c.post('/api/ai/ask',headers=h,json=q).json()
    assert r['response_size']=='detailed' and sent[-1]['max_completion_tokens']==900
    assert c.get('/api/settings').json()['ai_response_size']=='normal'
