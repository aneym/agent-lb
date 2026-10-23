import json, pathlib, shutil, sys
source=pathlib.Path.cwd()/'config/coding-agents'
home=pathlib.Path(sys.argv[1])
sys.path.insert(0,str(source))
from policy_settings import reconcile_settings
from policy_hook_registry import fold_hooks
from policy_hook_install import pin_inproc, dispatch_worth
hooks_dir=home/'.claude/hooks'
hooks_dir.mkdir(parents=True,exist_ok=True)
for src in (source/'hooks').iterdir():
    if src.is_file(): shutil.copy2(src,hooks_dir/src.name)
settings=reconcile_settings({},False,lambda hooks,source: [],source=source)
folded,registry=fold_hooks(settings['hooks'],worth=dispatch_worth(source,home))
assert registry is not None
registry=pin_inproc(registry,home,source)
(hooks_dir/'dispatch').mkdir(exist_ok=True)
(hooks_dir/'dispatch/registry.json').write_text(json.dumps(registry,indent=2)+'\n')
(home/'.claude/settings.json').write_text(json.dumps(dict(settings,hooks=folded),indent=2)+'\n')
print('registry:',hooks_dir/'dispatch/registry.json')
print('hook groups:',sum(map(len,registry['per_hook'].values())))
