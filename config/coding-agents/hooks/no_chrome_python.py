import ast


def isolated_launches(body, profile):
    try:
        tree = ast.parse(body)
    except (SyntaxError, ValueError):
        return body
    edits = []
    launches = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        engine = node.func.value
        if method not in ("launch", "launch_persistent_context"):
            continue
        if not isinstance(engine, ast.Attribute) or engine.attr != "chromium":
            continue
        options = {keyword.arg: keyword.value for keyword in node.keywords}
        headless = options.get("headless")
        if not isinstance(headless, ast.Constant) or headless.value is not True:
            continue
        if method == "launch":
            allowed = not node.args and set(options) == {"headless"}
        else:
            directory = node.args[0] if len(node.args) == 1 else options.get("user_data_dir")
            allowed = (
                len(node.args) <= 1
                and not (node.args and "user_data_dir" in options)
                and set(options) <= {"headless", "user_data_dir"}
                and isinstance(directory, ast.Constant)
                and isinstance(directory.value, str)
                and profile(directory.value)
            )
        if allowed:
            launches.add(id(node))
            edits.append((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset))
    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings.setdefault(target.id, []).append(id(node.value) in launches)
    browsers = {name for name, values in bindings.items() if values == [True]}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        receiver = node.func.value
        if node.func.attr != "new_context" or not isinstance(receiver, ast.Name) or receiver.id not in browsers:
            continue
        options = {keyword.arg: keyword.value for keyword in node.keywords}
        state = options.get("storage_state")
        if (
            not node.args
            and set(options) == {"storage_state"}
            and isinstance(state, ast.Constant)
            and isinstance(state.value, str)
            and profile(state.value)
        ):
            edits.append((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset))
    lines = body.splitlines(keepends=True)
    for start_line, start, end_line, end in sorted(edits, reverse=True):
        first = lines[start_line - 1].encode()
        last = lines[end_line - 1].encode()
        lines[start_line - 1 : end_line] = [(first[:start] + b"None" + last[end:]).decode()]
    return "".join(lines)
