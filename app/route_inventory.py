import ast
import json
from pathlib import Path


def main() -> None:
    sources = [
        *Path("app/modules").rglob("api.py"),
        Path("app/modules/proxy/meta_service.py"),
        Path("app/cli.py"),
        Path("app/db/migrate.py"),
        Path("app/startup_benchmark.py"),
        *Path("app").glob("audit_*.py"),
        Path("app/latency_cli.py"),
    ]
    routes = set()
    for source in sources:
        tree = ast.parse(source.read_text())
        prefixes = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if not isinstance(node.value.func, ast.Name) or node.value.func.id != "APIRouter":
                continue
            name = node.targets[0].id if isinstance(node.targets[0], ast.Name) else ""
            prefix = ""
            for keyword in node.value.keywords:
                if (
                    keyword.arg == "prefix"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    prefix = keyword.value.value
                    break
            prefixes[name] = prefix
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_parser"
                and node.args
            ):
                command = node.args[0]
                if isinstance(command, ast.Constant) and isinstance(command.value, str):
                    routes.add("cli:" + command.value)
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id not in prefixes:
                    continue
                allowed = {"get", "post", "put", "delete", "patch", "api_route", "websocket"}
                if decorator.func.attr not in allowed or not decorator.args:
                    continue
                path = decorator.args[0]
                if not isinstance(path, ast.Constant) or not isinstance(path.value, str) or not path.value:
                    continue
                if "{" in path.value or "}" in path.value:
                    continue
                parts = (prefixes[decorator.func.value.id], path.value)
                full_path = "/" + "/".join(part.strip("/") for part in parts if part.strip("/"))
                routes.add(full_path)
    try:
        toml = __import__("tomllib")
    except ModuleNotFoundError:
        toml = __import__("tomli")
    manifest = toml.loads(Path("pyproject.toml").read_text())
    routes.update("cli:" + name for name in manifest.get("project", {}).get("scripts", {}))
    routes = {route for route in routes if not route.startswith("cli:")}
    routes.update("cli:" + name for name in manifest.get("project", {}).get("scripts", {}))
    inventory = {
        "route": sorted(route for route in routes if not route.startswith("cli:")),
        "cli": [name for name in manifest.get("project", {}).get("scripts", {})],
    }
    print(json.dumps(inventory))


if __name__ == "__main__":
    main()
