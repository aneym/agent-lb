
import importlib.util, json, os, subprocess, sys
src = sys.argv[1]
spec = importlib.util.spec_from_file_location("hd", src); hd = importlib.util.module_from_spec(spec)
sys.argv = [src]; spec.loader.exec_module(hd)
guard = os.path.expanduser("~/.claude/hooks/no-chrome-guard.sh")
hook = {"type": "command", "command": '"$HOME/.claude/hooks/no-chrome-guard.sh"'}
p = hd.plan_hook(hook, set(), {})
print("plan:", p.kind, "|", p.reason)
assert p.kind == "filtered", p.reason
def pay(c): return json.dumps({"tool_name": "Bash", "tool_input": {"command": c}})
for c in ["ls -la", "git status && cat <<EOF\nx\nEOF", "python3 - <<'PY'\nprint(1)\nPY"]:
    assert not p.prefilter(json.loads(pay(c))), c
for c in ['open -a "Google Chrome" https://x', "/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --headless",
          "ssh pc 'start chrome'", "node -e '" + "puppeteer" + ".launch()'"]:
    assert p.prefilter(json.loads(pay(c))), c
    r = subprocess.run(["/bin/bash", guard], input=pay(c), capture_output=True, text=True)
    print(r.returncode, c[:50])
print("ok")
