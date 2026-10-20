#!/bin/bash





set -euo pipefail
INPUT=$(cat) || { echo "BLOCKED: railway-vars-guard could not read the hook input (cat exit $?), so this call is refused." >&2; exit 2; }
STATUS=0
RAILWAY_GUARD_INPUT="$INPUT" python3 - <<'PY' || STATUS=$?
import json
import os
import re
import shlex
import sys
from pathlib import Path

DENIAL = ('Railway destructive delete refused (destructive data floor): use a custody '
          "RAILWAY_TOKEN or RAILWAY_API_TOKEN, not the owner's CLI login.")

WARNINGS = set()
ARGV_DENIAL = 'Railway secret-looking argv detected (secret floor); use stdin or an env file.'


def secret_warning(message):
    WARNINGS.add(message)


TOKEN_NAMES = {'RAILWAY_TOKEN', 'RAILWAY_API_TOKEN'}
READS = {'status', 'logs', 'list', 'whoami', 'version', 'help'}
SECRET_NAME = re.compile(r'(?:token|secret|password|passwd|api[_-]?key|authorization|bearer|credential)', re.I)
SECRET_VALUE = re.compile(r'''(?:^|[=\s'"])(?:bearer\s+|(?:sk|rk|pk|ghp|gho|github_pat|xox[baprs])[-_]|eyJ[A-Za-z0-9_-]+\.)''', re.I)
ASSIGNMENT = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', re.S)

KV_DENIAL = "railway variables read prints values (secret floor)."
PRINTENV_DENIAL = "railway run printenv/env prints every secret (secret floor)."


OPTION_VALUES = {'--environment', '-e', '--service', '-s', '--project', '-p'}


def variable_write(args):
    i, positional = 0, None
    while i < len(args):
        arg = args[i]
        if arg in OPTION_VALUES:
            i += 2
            continue
        if arg in {'--set', '--delete', '--remove', '--set-from-stdin'} or arg.startswith(
                ('--set=', '--delete=', '--remove=')):
            return True
        if not arg.startswith('-') and positional is None:
            positional = arg
        i += 1
    return positional in {'set', 'delete', 'remove'}


def run_value_output(args):
    while args:
        if ASSIGNMENT.match(args[0]):
            args = args[1:]
            continue
        name = args[0].rsplit('/', 1)[-1]
        if name not in {'time', 'sudo', 'nice', 'command', 'exec', 'nohup'}:
            break
        i = 1
        while i < len(args) and args[i].startswith('-'):
            flag = args[i]
            i += 1
            if flag == '--':
                break
            if flag in {'-n', '-u', '-g', '--user', '--group', '--adjustment'}:

                if flag != '-n' or name == 'nice':
                    i += 1
        args = args[i:]
    if not args:
        return False
    if name in {'printenv', 'env', 'set'} or (name == 'export' and '-p' in args[1:]):
        return True
    if name == 'railway' and len(args) > 1 and args[1] in {'variables', 'variable', 'vars'}:
        return (not variable_write(args[2:]) or 'get' in args[2:]
                or any(arg in {'--kv', '--json'} or re.fullmatch(r'-[^-]*k[^-]*', arg)
                       for arg in args[2:]))
    if name in {'echo', 'printf'} and any(
            SECRET_NAME.search(variable)
            for arg in args[1:]
            for variable in re.findall(r'\$\{?([A-Za-z_][A-Za-z0-9_]*)', arg)):
        return True
    if name == 'cat' and any(Path(arg).name == '.env' or Path(arg).name.startswith('.env.')
                             for arg in args[1:]):
        return True
    if name in {'sh', 'bash', 'zsh', 'dash', 'ksh'}:
        for i, arg in enumerate(args[1:-1], 1):
            if arg.startswith('-') and 'c' in arg[1:]:
                try:
                    lexer = shlex.shlex(args[i + 1], posix=True, punctuation_chars=';&|\n')
                    lexer.whitespace_split = True
                    segment = []
                    for word in list(lexer) + [';']:
                        if word and all(c in ';&|\n' for c in word):
                            if run_value_output(segment):
                                return True
                            segment = []
                        else:
                            segment.append(word)
                except ValueError:
                    return True


    return False


def production_environment(args):


    for i, arg in enumerate(args):
        value = None
        if arg in {'--environment', '-e'} and i + 1 < len(args):
            value = args[i + 1]
        elif arg.startswith('--environment='):
            value = arg.split('=', 1)[1]
        elif arg.startswith('-e') and len(arg) > 2:
            value = arg[2:].lstrip('=')
        if value is not None and value.casefold() in {'production', 'prod'}:
            return True
    return False


def substitutions(text, heredoc=False):

    result, out = [], []
    i, quote = 0, None
    while i < len(text):
        c = text[i]
        if c == "\\" and quote != "'":
            out.append(text[i:i + 2])
            i += 2
            continue
        if not heredoc and c in "\"'" and (quote is None or quote == c):
            quote = None if quote else c
        if (heredoc or quote != "'") and (text.startswith('$(', i) or c == '`'):
            backtick = c == '`'
            start = i + (1 if backtick else 2)
            j, depth, inner_quote = start, 1, None
            while j < len(text):
                ch = text[j]
                if ch == "\\" and inner_quote != "'":
                    j += 2
                    continue
                if ch in "\"'" and (inner_quote is None or inner_quote == ch):
                    inner_quote = None if inner_quote else ch
                elif not inner_quote:
                    if backtick and ch == '`':
                        break
                    if not backtick:
                        if ch == '(':
                            depth += 1
                        elif ch == ')':
                            depth -= 1
                            if not depth:
                                break
                j += 1
            result.append(text[start:j])
            out.append('$UNKNOWN_SUBSTITUTION')
            i = j + 1
            continue
        out.append(c)
        i += 1
    return ''.join(out), result


def heredocs(command):
    lines = command.splitlines(keepends=True)
    out, nested, pending = [], [], []
    quote = None
    for line in lines:
        if pending:
            delimiter, quoted, tabs = pending[0]
            data = line.lstrip('\t') if tabs else line
            if data.rstrip('\r\n') == delimiter:
                pending.pop(0)
            elif not quoted:
                nested.extend(substitutions(data, heredoc=True)[1])
            continue
        i, cleaned = 0, []
        while i < len(line):
            c = line[i]
            if c == '\\' and quote != "'":
                cleaned.append(line[i:i + 2])
                i += 2
                continue
            if c in "\"'" and (quote is None or quote == c):
                quote = None if quote else c
            elif not quote:
                if c == '#' and (i == 0 or line[i - 1] in ' \t;|&()'):


                    cleaned.append('\n' if line.endswith('\n') else '')
                    break

                fd = re.match(r'\d+(?=[<>])', line[i:])
                if fd and (i == 0 or line[i - 1] in ' \t;|&()'):
                    i += len(fd[0])
                    continue
                if line.startswith('<<<', i):
                    cleaned.append('<<<')
                    i += 3
                    continue
                if line.startswith('<<', i):
                    match = re.match(r"<<(-?)[ \t]*('([^']*)'|\"([^\"]*)\"|([A-Za-z_][A-Za-z0-9_]*))", line[i:])
                    if match:
                        pending.append((match[3] if match[3] is not None else
                                        match[4] if match[4] is not None else match[5],
                                        match[3] is not None or match[4] is not None, bool(match[1])))
                        cleaned.append(match[0])
                        i += len(match[0])
                        continue
            cleaned.append(c)
            i += 1
        out.append(''.join(cleaned))
    return ''.join(out), nested


def expand(value, env):
    value = re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)',
                   lambda m: env.get(m[1] or m[2], ''), value)
    return '' if '$' in value else value


def check(command, inherited, value_output=False):


    if not value_output and not re.search(r'\brailway\b', command):
        return ''
    command, nested = heredocs(command)
    command, expansions = substitutions(command)
    for value in nested + expansions:
        found = check(value, inherited, value_output)
        if found:
            return found
    words = []
    parsed = True
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|()<>\n')
        lexer.whitespace = ' \t\r'
        lexer.commenters = ''
        lexer.whitespace_split = True
        for word in lexer:
            words.append(word)
    except ValueError:
        parsed = False
        print('WARNING: railway guard could not fully parse the command.', file=sys.stderr)
        if lexer.token:
            words.append(lexer.token)
    segments, current = [], []
    names_only_sources = set()
    redirected = False
    i = 0
    while i < len(words):
        word = words[i]
        if re.fullmatch(r'[<>]+|[<>]+&', word):
            redirected = True
            i += 2
            continue
        if word and all(c in ';&|()\n' for c in word):
            if (parsed and word == '|' and not redirected and words[i + 1:i + 3] == ['jq', 'keys']
                    and (i + 3 == len(words) or words[i + 3] in {';', '\n', '&&', '||'})):
                names_only_sources.add(len(segments))
            segments.append(current)
            current = []
            redirected = False
        else:
            current.append(word)
        i += 1
    segments.append(current)
    for segment_index, words in enumerate(segments):
        env = dict(inherited)
        i = 0
        while i < len(words):
            word = words[i]
            assignment = ASSIGNMENT.match(word)
            if assignment:
                if '$UNKNOWN_SUBSTITUTION' in assignment[2] and assignment[1] in TOKEN_NAMES:
                    env[assignment[1]] = inherited.get(assignment[1], '')
                else:
                    env[assignment[1]] = expand(assignment[2], env)
                i += 1
                continue
            name = word.rsplit('/', 1)[-1]
            if value_output and run_value_output(words[i:]):
                secret_warning(PRINTENV_DENIAL)
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
                    elif option.startswith('-u'):
                        env.pop(option[2:], None)
                    elif option in ('-S', '--split-string') and i < len(words):
                        found = check(words[i] + ' ' + shlex.join(words[i + 1:]), env, value_output)
                        if found:
                            return found
                        i = len(words)
                    elif option.startswith('--split-string='):
                        found = check(option.split('=', 1)[1] + ' ' + shlex.join(words[i:]), env, value_output)
                        if found:
                            return found
                        i = len(words)
                continue
            if name == 'eval':
                found = check(' '.join(words[i + 1:]), env, value_output)
                if found:
                    return found
                break
            if name in {'sudo', 'command', 'exec', 'nohup', 'nice', 'time', 'xargs', 'timeout'}:
                i += 1
                while i < len(words) and words[i].startswith('-'):
                    option = words[i]
                    i += 1
                    if option == '--':
                        break
                    operands = ({'-u', '-g', '-h', '-p', '-C', '-T', '-R', '-D', '-r',
                                 '--user', '--group', '--host', '--prompt', '--close-from',
                                 '-t', '--command-timeout', '--chroot', '--chdir', '--role', '--type'}
                                if name == 'sudo' else
                                {'-a', '-I', '-n', '-P', '-L', '-s', '-d', '-E'} if name == 'xargs' else
                                {'-n', '-u', '-g', '-t', '-k', '-s', '-I', '-P'})
                    if option in operands:
                        i += 1
                    elif name == 'sudo' and not option.startswith('--'):
                        for j, flag in enumerate(option[1:], 1):
                            if '-' + flag in operands:


                                if j == len(option) - 1:
                                    i += 1
                                break
                if name == 'timeout':
                    i += 1
                continue
            if name in {'npx', 'bunx', 'pnpm', 'yarn', 'npm'}:
                i += 1
                if name in {'pnpm', 'yarn', 'npm'}:
                    if i >= len(words) or words[i] not in {'dlx', 'exec'}:
                        break
                    i += 1
                while i < len(words) and words[i].startswith('-'):
                    i += 1
                if i < len(words) and re.fullmatch(r'@railway/cli(?:@.+)?', words[i]):
                    words[i] = 'railway'
                continue
            if name in {'sh', 'bash', 'zsh', 'dash', 'ksh', 'su'}:
                for j in range(i + 1, len(words) - 1):
                    if words[j].startswith('-') and 'c' in words[j][1:]:
                        found = check(words[j + 1], env, value_output)
                        if found:
                            return found
                break
            if name == 'railway':
                args = words[i + 1:]
                for j, arg in enumerate(args):
                    if '$UNKNOWN_SUBSTITUTION' in arg and (
                            arg.startswith('--set=') or (j > 0 and args[j - 1] in {'--set', 'set'})):
                        secret_warning(ARGV_DENIAL)
                    if (j > 0 and args[j - 1] in {'set', '--set'} and SECRET_NAME.search(arg)
                            and '=' not in arg and j + 1 < len(args) and not args[j + 1].startswith('-')):
                        secret_warning(ARGV_DENIAL)
                    value = arg.split('=', 1)[1] if arg.startswith('--set=') else arg
                    match = ASSIGNMENT.match(value)
                    secret_expansion = match and any(
                        re.search(r'token|secret|key|password', name, re.I)
                        for name in re.findall(r'\$\{?([A-Za-z_][A-Za-z0-9_]*)', match[2]))
                    if match and match[2] and (SECRET_NAME.search(match[1]) or SECRET_VALUE.search(match[2])
                                              or len(match[2]) >= 24 or secret_expansion):
                        secret_warning(ARGV_DENIAL)
                    if SECRET_VALUE.search(arg):
                        secret_warning(ARGV_DENIAL)

                command_args = list(args)
                help_requested = False
                while command_args and command_args[0].startswith('-'):
                    flag = command_args.pop(0)
                    if flag in OPTION_VALUES and command_args:
                        command_args.pop(0)
                    elif flag in {'--help', '-h'}:
                        help_requested = True
                        break
                if help_requested or not command_args or command_args[0] in READS:
                    break
                subcommand = command_args[0]
                if subcommand in {'ssh', 'run'}:
                    k = 1
                    while k < len(command_args) and command_args[k].startswith('-'):
                        if command_args[k] in {'--help', '-h'}:
                            help_requested = True
                            break
                        if command_args[k] == '--':
                            k += 1
                            break
                        k += 2 if command_args[k] in OPTION_VALUES | {
                            '--deployment-instance', '-i', '--service-instance'} else 1
                    if help_requested:
                        break
                    found = check(shlex.join(command_args[k:]), env, value_output=True)
                    if found:
                        return found
                    break
                if subcommand in {'up', 'deploy', 'redeploy'}:
                    break
                if subcommand in {'variables', 'variable', 'vars'}:
                    write = variable_write(command_args[1:])


                    if (not write or 'get' in command_args[1:]
                            or any(arg in {'--kv', '--json'} or re.fullmatch(r'-[^-]*k[^-]*', arg)
                                   for arg in command_args[1:])):
                        if not (segment_index in names_only_sources and '--json' in command_args
                                and not any(arg in {'--kv', '-k'} for arg in command_args)):
                            secret_warning(KV_DENIAL)
                        break
                    if not WARNINGS:
                        print('Railway variable write allowed pre-GA: explicit or unknown linked environment; values withheld.', file=sys.stderr)
                    break
                destructive = subcommand in {'delete', 'down'}
                if subcommand in {'service', 'volume', 'environment'}:
                    operands = command_args[1:]
                    while operands and operands[0].startswith('-'):
                        flag = operands.pop(0)
                        if flag == '--':
                            break
                        if flag in OPTION_VALUES and operands:
                            operands.pop(0)
                    destructive = bool(operands and operands[0] == 'delete')
                if destructive and not any(env.get(key) for key in TOKEN_NAMES):
                    return DENIAL
                break

            break
    return ''


command = ''
try:
    payload = json.loads(os.environ['RAILWAY_GUARD_INPUT'])
    tool_input = payload.get('tool_input', {}) if isinstance(payload, dict) else {}
    command = tool_input.get('command', '') if isinstance(tool_input, dict) else ''
    if not isinstance(command, str):
        command = ''
    denied = check(command, dict(os.environ))
except Exception as error:

    destructive = re.search(r'\brailway\s+(?:(?:service|volume|environment)\s+)?(?:delete|down)\b', command)
    secret_surface = re.search(r'\b(?:railway|variables|variable|vars)\b',
                               command or os.environ.get('RAILWAY_GUARD_INPUT', ''))
    denied = (f'{DENIAL} railway guard error: {type(error).__name__}'
              if destructive and not any(os.environ.get(key) for key in TOKEN_NAMES) else
              f'BLOCKED: railway secret check failed: {type(error).__name__}' if secret_surface else '')
    if not denied:
        print('WARNING: railway guard could not parse the hook input.', file=sys.stderr)
if denied:
    print(denied, file=sys.stderr)
    sys.exit(2)
if WARNINGS:
    print('BLOCKED: ' + ' '.join(sorted(WARNINGS)), file=sys.stderr)
    sys.exit(2)
PY
if [ "$STATUS" -eq 0 ] && [ -n "${HOOK_FLOOR_RECEIPT:-}" ]; then printf 'floor-ok %s\n' railway-vars-guard.sh; fi
exit "$STATUS"
