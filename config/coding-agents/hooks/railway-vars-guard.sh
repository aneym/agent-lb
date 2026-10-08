#!/bin/bash
# Railway writes must use custody tokens, never the owner's interactive login.
INPUT=$(cat)
RAILWAY_GUARD_INPUT="$INPUT" python3 - <<'PY'
import json
import os
import re
import shlex
import sys

DENIAL = ('Railway write refused: use a custody RAILWAY_TOKEN or RAILWAY_API_TOKEN, '
          'not the owner\'s CLI login. Never put secrets in argv; use '
          '`railway variables --set KEY` with the value from stdin or an env file.')
TOKEN_NAMES = {'RAILWAY_TOKEN', 'RAILWAY_API_TOKEN'}
READS = {'status', 'logs', 'list', 'whoami', 'version', 'help'}
SECRET_NAME = re.compile(r'(?:token|secret|password|passwd|api[_-]?key|authorization|bearer|credential)', re.I)
SECRET_VALUE = re.compile(r'(?:bearer\s+|(?:sk|rk|pk|ghp|gho|github_pat|xox[baprs])[-_]|eyJ[A-Za-z0-9_-]+\.)', re.I)
ASSIGNMENT = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', re.S)


def check(command, inherited):
    # Recurse through shell wrappers and substitutions without executing anything.
    if not re.search(r'\brailway\b', command):
        return False
    if re.search(r'\bbearer\s+', command, re.I):
        return True
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|()<>\n')
        lexer.whitespace = ' \t\r'
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return True
    segments, current = [], []
    for word in words:
        if word and all(c in ';&|()<>\n' for c in word):
            segments.append(current)
            current = []
        else:
            current.append(word)
    segments.append(current)
    for words in segments:
        env = dict(inherited)
        i = 0
        while i < len(words):
            word = words[i]
            assignment = ASSIGNMENT.match(word)
            if assignment:
                env[assignment[1]] = assignment[2]
                i += 1
                continue
            name = word.rsplit('/', 1)[-1]
            if name == 'env':
                i += 1
                while i < len(words) and words[i].startswith('-'):
                    option = words[i]
                    i += 1
                    if option in ('-i', '--ignore-environment'):
                        env = {}
                    elif option in ('-u', '--unset') and i < len(words):
                        env.pop(words[i], None)
                        i += 1
                    elif option.startswith('--unset='):
                        env.pop(option.split('=', 1)[1], None)
                continue
            if name in {'sudo', 'command', 'exec', 'nohup', 'npx', 'bunx'}:
                i += 1
                while i < len(words) and words[i].startswith('-'):
                    i += 1
                continue
            if name in {'sh', 'bash', 'zsh', 'dash', 'ksh'}:
                for j in range(i + 1, len(words) - 1):
                    if words[j].startswith('-') and 'c' in words[j][1:]:
                        if check(words[j + 1], env):
                            return True
                break
            if name == 'railway':
                args = words[i + 1:]
                for j, arg in enumerate(args):
                    if (j > 0 and args[j - 1] in {'set', '--set'} and SECRET_NAME.search(arg)
                            and '=' not in arg and j + 1 < len(args) and not args[j + 1].startswith('-')):
                        return True
                    value = arg.split('=', 1)[1] if arg.startswith('--set=') else arg
                    match = ASSIGNMENT.match(value)
                    if match and match[2] and (SECRET_NAME.search(match[1]) or SECRET_VALUE.search(match[2])
                                              or len(match[2]) >= 24):
                        return True
                    if SECRET_VALUE.search(arg):
                        return True
                if not args or args[0] in READS or '--help' in args or '-h' in args:
                    break
                if args[0] in {'variables', 'variable', 'vars'}:
                    write = any(arg in {'set', 'delete', 'remove', '--set', '--delete', '--remove'}
                                or arg.startswith(('--set=', '--delete=', '--remove=')) for arg in args[1:])
                elif args[0] in {'environment', 'environments', 'service', 'services'}:
                    write = any(arg in {'new', 'create', 'delete', 'remove', 'rename', '--new', '--delete', '--rename'}
                                or arg.startswith(('--new=', '--delete=', '--rename=')) for arg in args[1:])
                    # Selecting a target also mutates local Railway state.
                    write = write or any(not arg.startswith('-') for arg in args[1:])
                else:
                    write = True
                if write and not any(env.get(key) for key in TOKEN_NAMES):
                    return True
                break
            # An unknown wrapper containing Railway must not silently bypass the floor.
            # Match a Railway executable word, never prose or a path that merely names it.
            if any(value == 'railway' or value.endswith('/railway') for value in words[i + 1:]):
                return True
            break
    # Expansions can execute inside a quoted argument, even on a non-Railway command.
    for nested in re.findall(r'\$\(([^()]*)\)|`([^`]*)`', command):
        if check(next(value for value in nested if value), inherited):
            return True
    return False


try:
    payload = json.loads(os.environ['RAILWAY_GUARD_INPUT'])
    command = payload.get('tool_input', {}).get('command', '')
    denied = check(command, {key: os.environ.get(key, '') for key in TOKEN_NAMES})
except (ValueError, TypeError, AttributeError):
    denied = True
if denied:
    print(DENIAL, file=sys.stderr)
    sys.exit(2)
PY
