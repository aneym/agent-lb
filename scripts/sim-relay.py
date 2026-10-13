#!/usr/bin/env python3
"""Loopback simulator relay. Every operation requires a live own-lane UDID lease."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socketserver
import subprocess
import sys
import urllib.request

PORT = 21429
HOME = Path.home()
LEASES = HOME / '.agent-rails/sim-leases'
LEASE_TOOL = Path('/Volumes/StudioExt/repos/agent-rails/scripts/sim_lease.py')
UUID = re.compile(r'^[A-Fa-f0-9-]{36}$')


def descendants(pid, ancestor):
    rows = subprocess.run(['/bin/ps','-axo','pid=,ppid='],capture_output=True,text=True,timeout=5,check=True).stdout
    parents = {int(a):int(b) for a,b in (line.split() for line in rows.splitlines())}
    seen = set()
    while pid > 1 and pid not in seen:
        if pid == ancestor: return True
        seen.add(pid); pid = parents.get(pid,0)
    return False


def execute(request):
    lane, udid, pid = request['lane'], request['udid'], int(request['pid'])
    args = request['args']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*',lane) or not UUID.fullmatch(udid):
        raise ValueError('invalid lane or UDID')
    if not isinstance(args,list) or not args or not all(isinstance(x,str) and len(x)<4096 for x in args):
        raise ValueError('invalid arguments')
    record = json.loads((LEASES/(udid+'.json')).read_text())
    if record.get('owner') == 'capture': raise ValueError('protected simulator')
    expiry = datetime.fromisoformat(record['expires_at'].replace('Z','+00:00'))
    if expiry <= datetime.now(timezone.utc): raise ValueError('expired simulator lease')
    users = record.get('users') or [{'holder':record.get('owner'),'pid':record.get('pid')}]
    if not any(u.get('holder')==lane and not u.get('expired_at') and descendants(pid,int(u.get('pid') or 0)) for u in users):
        raise ValueError('caller must descend from live own-lane lease holder')
    verb = args[0]
    if verb == 'list':
        if args != ['list']: raise ValueError('list takes no arguments')
        result = subprocess.run(['/usr/bin/xcrun','simctl','list','devices','available','-j'],capture_output=True,text=True,timeout=20)
        if result.returncode: return {'rc':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
        devices = json.loads(result.stdout)
        devices['devices'] = {runtime:[d for d in ds if d['udid']==udid] for runtime,ds in devices['devices'].items()}
        return {'rc':0,'stdout':json.dumps(devices),'stderr':''}
    if verb in ('boot','shutdown') and len(args)==1: command=[verb,udid]
    elif verb == 'install' and len(args)==2:
        app=Path(args[1]).resolve(strict=True)
        if app.suffix!='.app' or not app.is_dir() or not str(app).startswith('/Volumes/StudioExt/repos/'):
            raise ValueError('install requires repository .app artifact')
        command=[verb,udid,str(app)]
    elif verb in ('launch','terminate') and len(args)==2 and re.fullmatch(r'[A-Za-z0-9_.-]+',args[1]): command=[verb,udid,args[1]]
    elif verb=='openurl' and len(args)==2 and re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:',args[1]): command=[verb,udid,args[1]]
    elif verb=='io' and len(args)==3 and args[1]=='screenshot':
        output=Path(args[2]).expanduser().resolve()
        roots=[(HOME/'.agent-rails/lanes'/lane/'evidence').resolve(),Path('/Volumes/StudioExt/agent-rails-runs')/lane]
        if output.suffix!='.png' or not any(output.is_relative_to(r) for r in roots):
            raise ValueError('screenshot must be PNG under own-lane evidence root')
        output.parent.mkdir(parents=True,exist_ok=True)
        command=['io',udid,'screenshot',str(output)]
    else: raise ValueError('unsupported simctl operation or arguments')
    # Preserve the canonical DRIVE arbitration for all simulator mutations and captures.
    gate=subprocess.run([sys.executable,str(LEASE_TOOL),'drive-check','--holder',lane,'--pid',str(pid),'--op',verb],capture_output=True,text=True,timeout=15)
    if gate.returncode: return {'rc':gate.returncode,'stdout':gate.stdout,'stderr':gate.stderr}
    result=subprocess.run(['/usr/bin/xcrun','simctl',*command],capture_output=True,text=True,timeout=90)
    return {'rc':result.returncode,'stdout':result.stdout,'stderr':result.stderr}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        try:
            data=self.rfile.readline(16385)
            if len(data)>16384: raise ValueError('request too large')
            result=execute(json.loads(data))
        except Exception as error: result={'rc':75,'stdout':'','stderr':'sim-relay: refused: '+str(error)+'\n'}
        self.wfile.write((json.dumps(result)+'\n').encode())


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True


def main():
    if sys.argv[1:]==['serve']:
        with Server(('127.0.0.1',PORT),Handler) as server: server.serve_forever()
    else:
        import socket
        p=argparse.ArgumentParser();p.add_argument('--lane',default=os.environ.get('FACTORY_LANE'));p.add_argument('--udid',required=True);p.add_argument('args',nargs=argparse.REMAINDER);a=p.parse_args()
        lane=a.lane or Path(os.environ.get('LANE_DIR','')).name
        with socket.create_connection(('127.0.0.1',PORT),timeout=5) as sock:
            sock.settimeout(110);sock.sendall((json.dumps({'lane':lane,'udid':a.udid,'pid':os.getpid(),'args':a.args})+'\n').encode())
            result=json.loads(sock.makefile('r').readline(2*1024*1024))
        print(result['stdout'],end='');print(result['stderr'],end='',file=sys.stderr);return result['rc']
    return 0


if __name__=='__main__': sys.exit(main())
