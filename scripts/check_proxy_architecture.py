#!/usr/bin/env python3


from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROXY_DIR = ROOT / "app" / "modules" / "proxy"
SERVICE_PATH = PROXY_DIR / "service.py"
_SERVICE_DIR = PROXY_DIR / "_service"
SERVICE_PACKAGE_DIR = PROXY_DIR / "_service"
HTTP_BRIDGE_MIXIN_PATH = PROXY_DIR / "_service" / "http_bridge" / "mixin.py"
STREAMING_MIXIN_PATH = PROXY_DIR / "_service" / "streaming" / "mixin.py"

MAX_SERVICE_LINES = 2_600
MAX_HTTP_BRIDGE_MIXIN_LINES = 2_400
MAX_STREAMING_MIXIN_LINES = 1_100
MAX_PROXY_SERVICE_METHOD_LINES = 1_200

REQUIRED_SERVICE_PACKAGES = {
    "http_bridge",
    "websocket",
    "streaming",
}

REQUIRED_SERVICE_MODULES = {
    "__init__.py",
    "api_key_usage.py",
    "codex_control.py",
    "compact.py",
    "file_ops.py",
    "observability.py",
    "rate_limit.py",
    "request_log.py",
    "response_create.py",
    "support.py",
    "transcribe.py",
    "warmup.py",
}

REQUIRED_SERVICE_FACADE_NAMES = {
    "ProxyService",
    "CodexControlResponse",
    "core_codex_control_request",
    "core_compact_responses",
    "core_create_file",
    "core_finalize_file",
    "core_transcribe_audio",
}

ALLOWED_SERVICE_INTERNAL_IMPORTS = {
    "app.modules.proxy._service.support",
}


ALLOWED_SERVICE_IMPORT_DOMAINS_BY_DOMAIN = {
    "api_key_usage": {"support"},
    "codex_control": {"codex_control_context", "support"},
    "compact": {"support"},
    "file_ops": {"file_transfer", "support"},
    "http_bridge": {"api_key_usage", "compact", "http_bridge", "observability", "support", "warmup"},
    "response_create": {"response_create_diagnostics", "support"},
    "streaming": {
        "api_key_usage",
        "compact",
        "http_bridge",
        "observability",
        "streaming",
        "support",
        "warmup",
        "websocket",
    },
    "transcribe": {"support"},
    "warmup": {"support"},
    "websocket": {"api_key_usage", "compact", "http_bridge", "observability", "support", "warmup", "websocket"},
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _line_count(path: Path) -> int:
    return len(path.read_text().splitlines())


def _defined_or_imported_names(module: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in module.body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".", 1)[0])
    return names


def _proxy_service_methods(module: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == "ProxyService":
            return [child for child in node.body if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)]
    raise AssertionError("ProxyService class not found in service.py")


def _check_service_line_count() -> None:
    count = _line_count(SERVICE_PATH)
    if count > MAX_SERVICE_LINES:
        raise AssertionError(f"service.py has {count} lines; limit is {MAX_SERVICE_LINES}")


def _check_http_bridge_mixin_line_count() -> None:
    count = _line_count(HTTP_BRIDGE_MIXIN_PATH)
    if count > MAX_HTTP_BRIDGE_MIXIN_LINES:
        raise AssertionError(f"http_bridge/mixin.py has {count} lines; limit is {MAX_HTTP_BRIDGE_MIXIN_LINES}")


def _check_streaming_mixin_line_count() -> None:
    count = _line_count(STREAMING_MIXIN_PATH)
    if count > MAX_STREAMING_MIXIN_LINES:
        raise AssertionError(f"streaming/mixin.py has {count} lines; limit is {MAX_STREAMING_MIXIN_LINES}")


def _check_proxy_service_method_size(module: ast.Module) -> None:
    methods = _proxy_service_methods(module)
    largest = max((method.end_lineno or method.lineno) - method.lineno + 1 for method in methods)
    if largest > MAX_PROXY_SERVICE_METHOD_LINES:
        raise AssertionError(
            f"largest ProxyService method spans {largest} lines; limit is {MAX_PROXY_SERVICE_METHOD_LINES}"
        )


def _check_service_facade_surface(module: ast.Module) -> None:
    names = _defined_or_imported_names(module)
    missing = sorted(REQUIRED_SERVICE_FACADE_NAMES - names)
    if missing:
        raise AssertionError("service.py is missing compatibility façade names: " + ", ".join(missing))


def _check_service_does_not_import_shims(module: ast.Module) -> None:
    forbidden = {"app.modules.proxy._support", "app.modules.proxy._warmup"}
    for node in module.body:
        if isinstance(node, ast.ImportFrom) and node.module in forbidden:
            raise AssertionError(f"service.py must import moved implementation from _service/*, not {node.module}")


def _check_required_service_packages() -> None:
    existing = {d.name for d in _SERVICE_DIR.iterdir() if d.is_dir() and not d.name.startswith("__")}
    missing = sorted(REQUIRED_SERVICE_PACKAGES - existing)
    if missing:
        raise AssertionError("missing required proxy _service packages: " + ", ".join(missing))


def _check_required_service_modules() -> None:
    existing = {path.name for path in SERVICE_PACKAGE_DIR.glob("*.py")}
    missing = sorted(REQUIRED_SERVICE_MODULES - existing)
    if missing:
        raise AssertionError("missing required proxy _service modules: " + ", ".join(missing))


def _service_domain(path: Path) -> str:
    relative = path.relative_to(SERVICE_PACKAGE_DIR)
    if len(relative.parts) == 1:
        return path.stem
    return relative.parts[0]


def _imported_service_domain(imported_module: str) -> str | None:
    prefix = "app.modules.proxy._service."
    if not imported_module.startswith(prefix):
        return None
    return imported_module.removeprefix(prefix).split(".", 1)[0]


def _check_no_cross_domain_service_imports() -> None:
    for path in SERVICE_PACKAGE_DIR.rglob("*.py"):
        if path.name == "__init__.py" or path == SERVICE_PACKAGE_DIR / "support.py":
            continue
        current_domain = _service_domain(path)
        allowed_domains = ALLOWED_SERVICE_IMPORT_DOMAINS_BY_DOMAIN.get(current_domain, {current_domain, "support"})
        module = _parse(path)
        for node in ast.walk(module):
            if not isinstance(node, ast.ImportFrom):
                continue
            imported_module = node.module or ""
            imported_domain = _imported_service_domain(imported_module)
            if imported_domain is None:
                continue
            if imported_module in ALLOWED_SERVICE_INTERNAL_IMPORTS or imported_domain in allowed_domains:
                continue
            raise AssertionError(
                f"{path.relative_to(ROOT)} imports cross-domain module {imported_module}; "
                f"allowed domains for {current_domain}: {', '.join(sorted(allowed_domains))}"
            )


def main() -> int:
    try:
        service_module = _parse(SERVICE_PATH)
        _check_service_line_count()
        _check_http_bridge_mixin_line_count()
        _check_streaming_mixin_line_count()
        _check_proxy_service_method_size(service_module)
        _check_service_facade_surface(service_module)
        _check_service_does_not_import_shims(service_module)
        _check_required_service_packages()
        _check_required_service_modules()
        _check_no_cross_domain_service_imports()
    except AssertionError as exc:
        print(f"proxy architecture check failed: {exc}", file=sys.stderr)
        return 1

    print("proxy architecture checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
