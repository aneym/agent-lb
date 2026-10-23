import asyncio, json, os, socket, subprocess, sys, time
from pathlib import Path
import httpx
from aiohttp import web
ROOT=Path.cwd()
OUT=ROOT/'.scratch/proof'
OUT.mkdir(exist_ok=True)
for stale in OUT.glob("db.sqlite*"): stale.unlink()
def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0))
        return s.getsockname()[1]
async def main():
    up,lb=port(),port()
    env=dict(os.environ)
    env["HOME"]=str(OUT/"home")
    env.update(AGENT_LB_DATA_DIR=str(OUT),AGENT_LB_DATABASE_URL=f'sqlite+aiosqlite:///{OUT}/db.sqlite',AGENT_LB_ENCRYPTION_KEY_FILE=str(OUT/'key'),AGENT_LB_UPSTREAM_BASE_URL=f'http://127.0.0.1:{up}',AGENT_LB_MODEL_ALIASES=json.dumps({'voice':'gpt-4o-transcribe','pic':'gpt-image-1','denied':'blocked-target','blocked-alias':'gpt-4o-transcribe'}),AGENT_LB_EXCLUDED_MODEL_PATTERNS='["blocked-*"]',AGENT_LB_DASHBOARD_BOOTSTRAP_TOKEN='disposable-proof',AGENT_LB_UPSTREAM_STREAM_TRANSPORT='http')
    for name in ['ACCOUNT_PULSE','USAGE_REFRESH','MODEL_REGISTRY','QUOTA_PLANNER_SCHEDULER','ACCOUNTS_CACHE_WARMER','RESET_CREDIT_AUTO_REDEEM','RESET_CREDIT_EXPIRY_REDEEM','STICKY_SESSION_CLEANUP','IDENTITY','HTTP_RESPONSES_SESSION_BRIDGE']:
        env[f'AGENT_LB_{name}_ENABLED']='false'
    records=[]
    async def fake(req):
        raw=await req.read()
        records.append({'path':req.path,'body':raw.hex()})
        if req.path.endswith('/transcribe'): return web.json_response({'text':'proof transcription'})
        return web.json_response({'error':{'message':'fake upstream reached','type':'proof'}},status=418)
    app=web.Application(); app.router.add_route('*','/{path:.*}',fake)
    runner=web.AppRunner(app); await runner.setup(); await web.TCPSite(runner,'127.0.0.1',up).start()
    log=(OUT/'lb.log').open('w')
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(lb)],env=env,stdout=log,stderr=log,cwd=ROOT)
    results=[]
    try:
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{lb}',timeout=30,follow_redirects=True) as client:
            for _ in range(300):
                if proc.poll() is not None: raise RuntimeError((OUT/'lb.log').read_text()[-5000:])
                try:
                    r=await client.get('/health')
                    if r.status_code==200: break
                except httpx.ConnectError: pass
                await asyncio.sleep(.1)
            else: raise RuntimeError('startup timeout')
            seed='''import asyncio
from datetime import datetime, timedelta
from app.db.session import SessionLocal
from app.db.models import Account
from app.core.crypto import TokenEncryptor
async def seed():
    enc=TokenEncryptor()
    async with SessionLocal() as s:
        s.add(Account(id="proof", email="proof@example.invalid", plan_type="plus", access_token_encrypted=enc.encrypt("disposable-fake-access"), refresh_token_encrypted=enc.encrypt("disposable-fake-refresh"), last_refresh=datetime.utcnow(), access_expires_at=datetime.utcnow()+timedelta(days=1)))
        await s.commit()
asyncio.run(seed())
'''
            seeded=subprocess.run([sys.executable,'-c',seed],env=env,cwd=ROOT,capture_output=True,text=True)
            if seeded.returncode: raise RuntimeError(seeded.stderr)
            for path in ['/v1/images/edits','/v1/audio/transcriptions','/backend-api/transcribe','/v1/warmup','/v1/warmup/normal','/v1/responses','/backend-api/codex/v1/images/edits']:
                for model in ['blocked-direct','denied','blocked-alias']:
                    before=len(records)
                    if 'warmup' in path or 'responses' in path: r=await client.post(path,json={'model':model,'mode':'normal','input':'hi'})
                    else: r=await client.post(path,data={'model':model,'prompt':'proof'},files={'file':('a.wav',b'proof-audio'),'image':('a.png',b'proof-image')})
                    assert r.status_code==400 and r.json()['error']['code']=='model_not_allowed',(path,model,r.status_code,r.text)
                    assert len(records)==before
                    results.append({'path':path,'model':model,'status':r.status_code,'upstream_calls':0})
            for path in ['/v1/audio/transcriptions','/backend-api/transcribe']:
                before=len(records)
                r=await client.post(path,data={'model':'voice','prompt':'proof'},files={'file':('a.wav',b'proof-audio\x00\xff')})
                assert r.status_code==200,(path,r.status_code,r.text)
                assert len(records)==before+1
                assert b'proof-audio\x00\xff' in bytes.fromhex(records[-1]['body'])
                results.append({'path':path,'model':'voice','status':r.status_code,'upload_preserved':True})
            r=await client.post('/v1/audio/transcriptions',data={'model':'unknown'},files={'file':('a.wav',b'proof')})
            assert r.status_code==400 and r.json()['error'].get('code')!='model_not_allowed'
            results.append({'path':'/v1/audio/transcriptions','model':'unknown','status':r.status_code})
            r=await client.post('/v1/warmup',json={'mode':'normal','model':'voice'})
            assert r.status_code==400 and r.json()['error']['param']=='model',(r.status_code,r.text)
            results.append({'path':'/v1/warmup','model':'voice','status':r.status_code})
            for path in ['/v1/images/edits','/backend-api/codex/v1/images/edits']:
                r=await client.post(path,data={'model':'pic','prompt':'proof'},files={'image':('a.png',b'proof-image')})
                assert r.status_code==418,(r.status_code,r.text)
                body=json.loads(bytes.fromhex(records[-1]['body']))
                assert body['tools'][0]['model']=='gpt-image-1'
                assert body['input'][0]['content'][1]['image_url'].endswith('cHJvb2YtaW1hZ2U=')
                results.append({'path':path,'model':'pic','status':r.status_code,'response':r.text})
        (OUT/'verdict.json').write_text(json.dumps({'results':results,'upstream':records},indent=2))
        print(f'PASS: {len(results)} HTTP scenarios; fake upstream calls={len(records)}; artifacts={OUT}')
    finally:
        proc.terminate(); proc.wait(timeout=15); log.close(); await runner.cleanup()
asyncio.run(main())
