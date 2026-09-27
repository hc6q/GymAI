import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent
DB = Path(os.getenv('DATABASE_PATH', '/app/data/gym.db'))
KEY = os.getenv('SECRET_KEY', '')
USER = os.getenv('APP_USERNAME', '')
PASSWORD = os.getenv('APP_PASSWORD', '')
AUTH = bool(USER and PASSWORD)
if AUTH and (not KEY or KEY == 'CHANGE_ME_TO_A_LONG_RANDOM_VALUE'):
    raise RuntimeError('Configure SECRET_KEY com um valor aleatório antes de ativar o login')
MODEL = os.getenv('GROQ_MODEL') or 'llama-3.3-70b-versatile'
DAYS = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
DEFAULTS = [('Supino reto','Peito','Barra'),('Supino inclinado','Peito','Halteres'),('Crucifixo','Peito','Halteres'),('Desenvolvimento','Ombros','Halteres'),('Elevação lateral','Ombros','Halteres'),('Rosca direta','Bíceps','Barra'),('Rosca martelo','Bíceps','Halteres'),('Tríceps corda','Tríceps','Polia'),('Tríceps francês','Tríceps','Halteres'),('Barra fixa','Costas','Barra'),('Puxada alta','Costas','Polia'),('Remada baixa','Costas','Polia'),('Remada curvada','Costas','Barra'),('Agachamento','Pernas','Barra'),('Leg press','Pernas','Máquina'),('Extensora','Quadríceps','Máquina'),('Flexora','Posterior','Máquina'),('Panturrilha','Panturrilha','Máquina'),('Levantamento terra','Posterior','Barra'),('Stiff','Posterior','Barra')]
SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS exercises(id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE, muscle_group TEXT NOT NULL DEFAULT '', equipment TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', custom INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS workouts(id INTEGER PRIMARY KEY, weekday INTEGER NOT NULL UNIQUE CHECK(weekday BETWEEN 0 AND 6), name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', rest INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS workout_exercises(id INTEGER PRIMARY KEY, workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE, exercise_id INTEGER NOT NULL REFERENCES exercises(id), position INTEGER NOT NULL, sets INTEGER NOT NULL CHECK(sets BETWEEN 1 AND 30), reps TEXT NOT NULL, weight REAL NOT NULL DEFAULT 0, unit TEXT NOT NULL DEFAULT 'kg', notes TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS workout_sessions(id INTEGER PRIMARY KEY, workout_id INTEGER REFERENCES workouts(id) ON DELETE SET NULL, workout_name TEXT NOT NULL, performed_at TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS session_sets(id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES workout_sessions(id) ON DELETE CASCADE, exercise_id INTEGER REFERENCES exercises(id) ON DELETE SET NULL, exercise_name TEXT NOT NULL, position INTEGER NOT NULL, set_number INTEGER NOT NULL, weight REAL NOT NULL, reps INTEGER NOT NULL, unit TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS ai_requests(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, model TEXT NOT NULL, question TEXT NOT NULL, context_hash TEXT NOT NULL, prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0, total_tokens INTEGER DEFAULT 0, cached INTEGER NOT NULL DEFAULT 0, response TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_history ON session_sets(exercise_id,session_id);
CREATE INDEX IF NOT EXISTS ix_ai_hash ON ai_requests(context_hash,cached);
'''

def now(): return datetime.now(timezone.utc).isoformat()
@contextmanager
def conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally: c.close()
def rows(cursor): return [dict(r) for r in cursor.fetchall()]
def one(cursor):
    r = cursor.fetchone()
    return dict(r) if r else None

def init():
    with conn() as c:
        c.executescript(SCHEMA)
        c.executemany('INSERT OR IGNORE INTO exercises(name,muscle_group,equipment,custom) VALUES(?,?,?,0)', DEFAULTS)
init()

app = FastAPI(title='GymAI', docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
templates = Environment(loader=FileSystemLoader(ROOT / 'templates'), autoescape=select_autoescape(['html']))

# Signed, HTTP-only login cookie; separate CSRF token is returned only to same-origin JS.
def sign(value): return hmac.new(KEY.encode(), value.encode(), hashlib.sha256).hexdigest()
def valid_auth(req):
    if not AUTH: return True
    raw = req.cookies.get('gym_session','')
    try:
        value, mac = raw.rsplit('.',1)
        name, expiry = value.split(':',1)
        return name == USER and int(expiry) > int(datetime.now().timestamp()) and hmac.compare_digest(sign(value),mac)
    except (ValueError,TypeError): return False
@app.middleware('http')
async def guard(req: Request, call_next):
    path = req.url.path
    if path.startswith('/static/') or path == '/login' or path == '/api/login' or path == '/api/health':
        return await call_next(req)
    if not valid_auth(req):
        if path.startswith('/api/'): return JSONResponse({'detail':'Autenticação necessária'},status_code=401)
        return Response(status_code=303,headers={'Location':'/login'})
    if req.method in ('POST','PUT','PATCH','DELETE'):
        cookie = req.cookies.get('gym_csrf','')
        header = req.headers.get('x-csrf-token','')
        if not cookie or not hmac.compare_digest(cookie,header):
            return JSONResponse({'detail':'CSRF inválido'},status_code=403)
    response = await call_next(req)
    if not req.cookies.get('gym_csrf'):
        response.set_cookie('gym_csrf',secrets.token_urlsafe(32),samesite='strict',secure=req.url.scheme=='https',path='/')
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='no-referrer'
    return response

@app.get('/api/health')
def health(): return {'ok':True}
@app.get('/login',response_class=HTMLResponse)
def login_page(req:Request):
    if valid_auth(req): return Response(status_code=303,headers={'Location':'/'})
    return templates.get_template('login.html').render()
class Credentials(BaseModel):
    username: str
    password: str
@app.post('/api/login')
def login(body:Credentials, req:Request, res:Response):
    if not AUTH or not (hmac.compare_digest(body.username,USER) and hmac.compare_digest(body.password,PASSWORD)):
        raise HTTPException(401,'Credenciais inválidas')
    value=f'{USER}:{int(datetime.now().timestamp())+7*86400}'
    res.set_cookie('gym_session',value+'.'+sign(value),httponly=True,samesite='strict',secure=req.url.scheme=='https',max_age=7*86400)
    res.set_cookie('gym_csrf',secrets.token_urlsafe(32),samesite='strict',secure=req.url.scheme=='https')
    return {'ok':True}
@app.post('/api/logout')
def logout(res:Response):
    res.delete_cookie('gym_session'); res.delete_cookie('gym_csrf'); return {'ok':True}
@app.get('/',response_class=HTMLResponse)
def index(): return templates.get_template('index.html').render(auth=AUTH)

class ExerciseIn(BaseModel):
    name: str = Field(min_length=1,max_length=100)
    muscle_group: str = Field(default='',max_length=80)
    equipment: str = Field(default='',max_length=80)
    notes: str = Field(default='',max_length=1000)
    @field_validator('name')
    @classmethod
    def clean_name(cls,v):
        v=v.strip()
        if not v: raise ValueError('Nome obrigatório')
        return v
class WorkoutIn(BaseModel):
    weekday: int = Field(ge=0,le=6)
    name: str = Field(min_length=1,max_length=100)
    description: str = Field(default='',max_length=1000)
    rest: bool = False
class EntryIn(BaseModel):
    exercise_id: int
    sets: int = Field(default=3,ge=1,le=30)
    reps: str = Field(default='10',min_length=1,max_length=30,pattern=r'^\d{1,3}(?:\s*-\s*\d{1,3})?$')
    weight: float = Field(default=0,ge=0,le=10000)
    unit: Literal['kg','lb'] = 'kg'
    notes: str = Field(default='',max_length=1000)
    details: list[dict] = Field(default_factory=list,max_length=30)
class ReorderIn(BaseModel):
    ids: list[int]
class SessionSetIn(BaseModel):
    exercise_id: int
    exercise_name: str = Field(min_length=1,max_length=100)
    position: int = Field(ge=0,le=100)
    set_number: int = Field(ge=1,le=30)
    weight: float = Field(ge=0,le=10000)
    reps: int = Field(ge=0,le=1000)
    unit: Literal['kg','lb'] = 'kg'
    notes: str = Field(default='',max_length=500)
class SessionIn(BaseModel):
    workout_id: int
    notes: str = Field(default='',max_length=2000)
    performed_at: date = Field(default_factory=date.today)
    sets: list[SessionSetIn] = Field(min_length=1,max_length=300)

def get_workout(c,wid):
    w=one(c.execute('SELECT * FROM workouts WHERE id=?',(wid,)))
    if not w: raise HTTPException(404,'Treino não encontrado')
    w['exercises']=rows(c.execute('SELECT we.*,e.name,e.muscle_group,e.equipment FROM workout_exercises we JOIN exercises e ON e.id=we.exercise_id WHERE we.workout_id=? ORDER BY we.position,we.id',(wid,)))
    for x in w['exercises']: x['details']=json.loads(x['details'])
    return w
@app.get('/api/exercises')
def exercises(q:str='',limit:int=100):
    with conn() as c: return rows(c.execute('SELECT * FROM exercises WHERE name LIKE ? ORDER BY name LIMIT ?',('%'+q[:100]+'%',min(max(limit,1),300))))
@app.post('/api/exercises')
def add_exercise(b:ExerciseIn):
    with conn() as c:
        try: cur=c.execute('INSERT INTO exercises(name,muscle_group,equipment,notes) VALUES(?,?,?,?)',(b.name,b.muscle_group,b.equipment,b.notes))
        except sqlite3.IntegrityError: raise HTTPException(409,'Exercício já existe')
        return one(c.execute('SELECT * FROM exercises WHERE id=?',(cur.lastrowid,)))
@app.put('/api/exercises/{eid}')
def edit_exercise(eid:int,b:ExerciseIn):
    with conn() as c:
        try: cur=c.execute('UPDATE exercises SET name=?,muscle_group=?,equipment=?,notes=? WHERE id=?',(b.name,b.muscle_group,b.equipment,b.notes,eid))
        except sqlite3.IntegrityError: raise HTTPException(409,'Nome já existe')
        if not cur.rowcount: raise HTTPException(404,'Exercício não encontrado')
        return one(c.execute('SELECT * FROM exercises WHERE id=?',(eid,)))
@app.delete('/api/exercises/{eid}')
def remove_exercise(eid:int):
    with conn() as c:
        try: cur=c.execute('DELETE FROM exercises WHERE id=?',(eid,))
        except sqlite3.IntegrityError: raise HTTPException(409,'Exercício usado em uma rotina')
        if not cur.rowcount: raise HTTPException(404,'Exercício não encontrado')
        return {'ok':True}
@app.get('/api/workouts')
def workouts():
    with conn() as c: return [get_workout(c,w['id']) for w in rows(c.execute('SELECT id FROM workouts ORDER BY weekday'))]
@app.get('/api/workouts/{wid}')
def workout(wid:int):
    with conn() as c: return get_workout(c,wid)
@app.post('/api/workouts')
def add_workout(b:WorkoutIn):
    with conn() as c:
        try: cur=c.execute('INSERT INTO workouts(weekday,name,description,rest) VALUES(?,?,?,?)',(b.weekday,b.name.strip(),b.description,int(b.rest)))
        except sqlite3.IntegrityError: raise HTTPException(409,'Já existe um treino nesse dia')
        return get_workout(c,cur.lastrowid)
@app.put('/api/workouts/{wid}')
def edit_workout(wid:int,b:WorkoutIn):
    with conn() as c:
        try: cur=c.execute('UPDATE workouts SET weekday=?,name=?,description=?,rest=? WHERE id=?',(b.weekday,b.name.strip(),b.description,int(b.rest),wid))
        except sqlite3.IntegrityError: raise HTTPException(409,'Já existe um treino nesse dia')
        if not cur.rowcount: raise HTTPException(404,'Treino não encontrado')
        return get_workout(c,wid)
@app.delete('/api/workouts/{wid}')
def remove_workout(wid:int):
    with conn() as c:
        cur=c.execute('DELETE FROM workouts WHERE id=?',(wid,))
        if not cur.rowcount: raise HTTPException(404,'Treino não encontrado')
        return {'ok':True}
def save_entry(c,wid,b,eid=None):
    if not one(c.execute('SELECT id FROM exercises WHERE id=?',(b.exercise_id,))): raise HTTPException(404,'Exercício não encontrado')
    details=[]
    for d in b.details:
        try:
            weight=float(d['weight']); reps=int(d['reps'])
            if not 0<=weight<=10000 or not 0<=reps<=1000: raise ValueError()
            details.append({'weight':weight,'reps':reps})
        except (KeyError,ValueError,TypeError): raise HTTPException(422,'Série detalhada inválida')
    if eid:
        cur=c.execute('UPDATE workout_exercises SET exercise_id=?,sets=?,reps=?,weight=?,unit=?,notes=?,details=? WHERE id=? AND workout_id=?',(b.exercise_id,b.sets,b.reps,b.weight,b.unit,b.notes,json.dumps(details),eid,wid))
        if not cur.rowcount: raise HTTPException(404,'Item não encontrado')
        return eid
    pos=c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM workout_exercises WHERE workout_id=?',(wid,)).fetchone()[0]
    return c.execute('INSERT INTO workout_exercises(workout_id,exercise_id,position,sets,reps,weight,unit,notes,details) VALUES(?,?,?,?,?,?,?,?,?)',(wid,b.exercise_id,pos,b.sets,b.reps,b.weight,b.unit,b.notes,json.dumps(details))).lastrowid
@app.post('/api/workouts/{wid}/exercises')
def add_entry(wid:int,b:EntryIn):
    with conn() as c:
        get_workout(c,wid)
        eid=save_entry(c,wid,b)
        return one(c.execute('SELECT * FROM workout_exercises WHERE id=?',(eid,)))
@app.put('/api/workouts/{wid}/exercises/{eid}')
def edit_entry(wid:int,eid:int,b:EntryIn):
    with conn() as c:
        save_entry(c,wid,b,eid)
        return {'ok':True}
@app.delete('/api/workouts/{wid}/exercises/{eid}')
def remove_entry(wid:int,eid:int):
    with conn() as c:
        cur=c.execute('DELETE FROM workout_exercises WHERE id=? AND workout_id=?',(eid,wid))
        if not cur.rowcount: raise HTTPException(404,'Item não encontrado')
        return {'ok':True}
@app.put('/api/workouts/{wid}/order')
def reorder(wid:int,b:ReorderIn):
    with conn() as c:
        ids=[r[0] for r in c.execute('SELECT id FROM workout_exercises WHERE workout_id=?',(wid,))]
        if len(ids)!=len(b.ids) or set(ids)!=set(b.ids): raise HTTPException(422,'Ordem incompleta')
        for i,eid in enumerate(b.ids): c.execute('UPDATE workout_exercises SET position=? WHERE id=?',(i,eid))
        return {'ok':True}
@app.post('/api/sessions')
def add_session(b:SessionIn):
    with conn() as c:
        w=get_workout(c,b.workout_id)
        valid={e['exercise_id'] for e in w['exercises']}
        if any(s.exercise_id not in valid for s in b.sets): raise HTTPException(422,'Exercício não pertence ao treino')
        names={e['exercise_id']:e['name'] for e in w['exercises']}
        cur=c.execute('INSERT INTO workout_sessions(workout_id,workout_name,performed_at,notes) VALUES(?,?,?,?)',(w['id'],w['name'],b.performed_at.isoformat(),b.notes))
        c.executemany('INSERT INTO session_sets(session_id,exercise_id,exercise_name,position,set_number,weight,reps,unit,notes) VALUES(?,?,?,?,?,?,?,?,?)',[(cur.lastrowid,s.exercise_id,names[s.exercise_id],s.position,s.set_number,s.weight,s.reps,s.unit,s.notes) for s in b.sets])
        return {'id':cur.lastrowid}
@app.get('/api/history')
def history(exercise_id:int|None=None,limit:int=100):
    with conn() as c:
        if exercise_id:
            sessions=rows(c.execute('SELECT DISTINCT s.* FROM workout_sessions s JOIN session_sets ss ON ss.session_id=s.id WHERE ss.exercise_id=? ORDER BY s.performed_at DESC,s.id DESC LIMIT ?',(exercise_id,min(max(limit,1),500))))
        else: sessions=rows(c.execute('SELECT * FROM workout_sessions ORDER BY performed_at DESC,id DESC LIMIT ?',(min(max(limit,1),500),)))
        for s in sessions:
            sql='SELECT * FROM session_sets WHERE session_id=?'+(' AND exercise_id=?' if exercise_id else '')+' ORDER BY position,set_number,id'
            args=(s['id'],exercise_id) if exercise_id else (s['id'],)
            s['sets']=rows(c.execute(sql,args))
        return sessions
@app.get('/api/exercises/{eid}/progress')
def progress(eid:int):
    with conn() as c:
        e=one(c.execute('SELECT * FROM exercises WHERE id=?',(eid,)))
        if not e: raise HTTPException(404,'Exercício não encontrado')
        h=rows(c.execute('SELECT s.performed_at,ss.weight,ss.reps,ss.unit FROM session_sets ss JOIN workout_sessions s ON s.id=ss.session_id WHERE ss.exercise_id=? ORDER BY s.performed_at DESC,s.id DESC,ss.set_number DESC LIMIT 100',(eid,)))
        return {'exercise':e,'last':h[0] if h else None,'max_weight':max((x['weight'] for x in h),default=None),'recent':h}

SYSTEM='Você é um assistente de treino de musculação. Analise volume, divisão, progressão e exercícios. Dê sugestões práticas com motivos. Não invente dados. Quando faltar informação, diga o que falta. Evite mudanças apenas por mudar. Se houver dor, lesão ou sintomas importantes, recomende avaliação profissional.'
class AskIn(BaseModel):
    question: str = Field(min_length=1,max_length=2000)
    scope: Literal['week','day','exercise']
    weekday: int|None = Field(default=None,ge=0,le=6)
    exercise_id: int|None = None
    fresh: bool = False

def build_context(c,scope,weekday=None,exercise_id=None):
    if scope=='exercise':
        e=one(c.execute('SELECT * FROM exercises WHERE id=?',(exercise_id,))) if exercise_id else None
        if not e: raise HTTPException(422,'Selecione um exercício')
        h=rows(c.execute('SELECT s.performed_at,ss.weight,ss.reps,ss.unit FROM session_sets ss JOIN workout_sessions s ON s.id=ss.session_id WHERE ss.exercise_id=? ORDER BY s.performed_at DESC,s.id DESC,ss.set_number DESC LIMIT 60',(exercise_id,)))
        lines=[f'EXERCÍCIO: {e["name"]} | {e["muscle_group"]} | {e["equipment"]}',f'Observações: {e["notes"][:300]}']
        for d in sorted(set(x['performed_at'] for x in h)):
            sets=[x for x in h if x['performed_at']==d]
            lines.append(d+': '+', '.join(f'{x["weight"]:g}{x["unit"]} x {x["reps"]}' for x in sets))
        return '\n'.join(lines)[:10000]
    if scope=='day' and weekday is None: raise HTTPException(422,'Selecione um dia')
    ws=rows(c.execute('SELECT id FROM workouts WHERE weekday=? ORDER BY weekday',(weekday,))) if scope=='day' else rows(c.execute('SELECT id FROM workouts ORDER BY weekday'))
    lines=[]
    for x in ws:
        w=get_workout(c,x['id'])
        lines.append(f'{DAYS[w["weekday"]]} — {w["name"]}'+(' (descanso)' if w['rest'] else ''))
        if w['description']: lines.append(w['description'][:300])
        for e in w['exercises']:
            detail=', '.join(f'{d["weight"]:g}{e["unit"]} x {d["reps"]}' for d in e['details'])
            lines.append(f'{e["position"]+1}. {e["name"]}: {detail or f"{e["sets"]}x{e["reps"]} — {e["weight"]:g}{e["unit"]}"} {e["notes"][:200]}')
    return '\n'.join(lines)[:12000] or 'Nenhuma rotina cadastrada.'
@app.post('/api/ai/ask')
async def ask(b:AskIn):
    key=os.getenv('GROQ_API_KEY','')
    if not key: raise HTTPException(503,'Configure GROQ_API_KEY no .env')
    with conn() as c:
        context=build_context(c,b.scope,b.weekday,b.exercise_id)
        question=b.question.strip()
        digest=hashlib.sha256(json.dumps([context,question,MODEL],ensure_ascii=False).encode()).hexdigest()
        if not b.fresh:
            cached=one(c.execute('SELECT response FROM ai_requests WHERE context_hash=? ORDER BY id DESC LIMIT 1',(digest,)))
            if cached:
                c.execute('INSERT INTO ai_requests(created_at,model,question,context_hash,cached,response) VALUES(?,?,?,?,1,?)',(now(),MODEL,question,digest,cached['response']))
                return {'answer':cached['response'],'cached':True,'context':context}
    payload={'model':MODEL,'messages':[{'role':'system','content':SYSTEM},{'role':'user','content':f'DADOS:\n{context}\n\nPERGUNTA:\n{question}'}],'temperature':0.35,'max_tokens':900}
    try:
        async with httpx.AsyncClient(timeout=35) as client:
            response=await client.post('https://api.groq.com/openai/v1/chat/completions',headers={'Authorization':'Bearer '+key},json=payload)
        response.raise_for_status()
        data=response.json()
        answer=data['choices'][0]['message']['content']
        usage=data.get('usage') or {}
    except (httpx.HTTPError,KeyError,IndexError,ValueError) as exc:
        if isinstance(exc,httpx.HTTPStatusError):
            code=exc.response.status_code
            raise HTTPException(502,f'Groq retornou HTTP {code}. Confira chave, modelo e limites.')
        raise HTTPException(502,'Falha ao consultar Groq. Tente novamente.')
    with conn() as c:
        c.execute('INSERT INTO ai_requests(created_at,model,question,context_hash,prompt_tokens,completion_tokens,total_tokens,cached,response) VALUES(?,?,?,?,?,?,?,?,?)',(now(),MODEL,question,digest,usage.get('prompt_tokens',0),usage.get('completion_tokens',0),usage.get('total_tokens',0),0,answer))
    return {'answer':answer,'cached':False,'context':context}
@app.get('/api/ai/context')
def ai_context(scope:Literal['week','day','exercise'],weekday:int|None=None,exercise_id:int|None=None):
    with conn() as c: return {'context':build_context(c,scope,weekday,exercise_id)}
@app.get('/api/ai/usage')
def ai_usage():
    today=date.today().isoformat(); month=today[:7]
    with conn() as c:
        items=rows(c.execute('SELECT created_at,model,question,prompt_tokens,completion_tokens,total_tokens,cached FROM ai_requests ORDER BY id DESC LIMIT 100'))
        def aggregate(prefix):
            r=one(c.execute('SELECT COUNT(*) calls, COALESCE(SUM(total_tokens),0) tokens, COALESCE(SUM(cached),0) cached FROM ai_requests WHERE created_at LIKE ?',(prefix+'%',)))
            return r
        return {'today':aggregate(today),'month':aggregate(month),'recent':items}
@app.get('/api/settings')
def settings(): return {'groq_configured':bool(os.getenv('GROQ_API_KEY')),'model':MODEL,'auth_enabled':AUTH}
@app.post('/api/ai/test')
async def test_groq():
    key=os.getenv('GROQ_API_KEY','')
    if not key: raise HTTPException(503,'Chave não configurada')
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r=await client.get('https://api.groq.com/openai/v1/models',headers={'Authorization':'Bearer '+key})
        r.raise_for_status()
        return {'ok':True,'model_available':any(x.get('id')==MODEL for x in r.json().get('data',[]))}
    except httpx.HTTPError: raise HTTPException(502,'Falha na conexão ou autenticação com Groq')
@app.get('/api/export')
def export_data():
    with conn() as c:
        data={'version':1,'exercises':rows(c.execute('SELECT * FROM exercises')),'workouts':rows(c.execute('SELECT * FROM workouts')),'workout_exercises':rows(c.execute('SELECT * FROM workout_exercises')),'workout_sessions':rows(c.execute('SELECT * FROM workout_sessions')),'session_sets':rows(c.execute('SELECT * FROM session_sets'))}
    return JSONResponse(data,headers={'Content-Disposition':'attachment; filename="gymai-backup.json"'})
@app.post('/api/import')
async def import_data(req:Request):
    content=await req.body()
    if len(content)>5_000_000: raise HTTPException(413,'Backup acima de 5 MB')
    try:
        data=json.loads(content)
        if data['version']!=1: raise ValueError()
        columns={'exercises':['id','name','muscle_group','equipment','notes','custom'],'workouts':['id','weekday','name','description','rest'],'workout_exercises':['id','workout_id','exercise_id','position','sets','reps','weight','unit','notes','details'],'workout_sessions':['id','workout_id','workout_name','performed_at','notes'],'session_sets':['id','session_id','exercise_id','exercise_name','position','set_number','weight','reps','unit','notes']}
        for table,fields in columns.items():
            if not isinstance(data[table],list) or len(data[table])>100000: raise ValueError()
            for row in data[table]:
                if not isinstance(row,dict) or set(row)!=set(fields): raise ValueError()
        with conn() as c:
            for table in reversed(columns): c.execute(f'DELETE FROM {table}')
            for table,fields in columns.items():
                names=','.join(fields); marks=','.join('?' for _ in fields)
                c.executemany(f'INSERT INTO {table} ({names}) VALUES ({marks})',[[r[f] for f in fields] for r in data[table]])
        return {'ok':True}
    except (ValueError,KeyError,TypeError,sqlite3.Error): raise HTTPException(422,'Backup inválido ou incompatível')
