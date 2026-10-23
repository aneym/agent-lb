from __future__ import annotations

from ipaddress import IPv4Network, IPv6Network, ip_address

_HTTP_TOKEN_CHARACTERS = frozenset("!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")


def _resolve_client_ip_from_xff_chain(
    socket_ip: str, forwarded_for: str, trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...]
) -> str:
    hops = [entry.strip() for entry in forwarded_for.split(",")]
    if any((not _is_valid_ip(entry) for entry in hops)):
        raise ValueError("Invalid X-Forwarded-For chain")
    return _resolve_client_ip_from_hops(socket_ip, hops, trusted_proxy_networks)


def _resolve_client_ip_from_hops(
    socket_ip: str, hops: list[str], trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...]
) -> str:
    resolved = socket_ip
    for previous_hop in reversed(hops):
        if not is_trusted_proxy_source(resolved, trusted_proxy_networks):
            break
        resolved = previous_hop
    return resolved


def is_trusted_proxy_source(host: str, trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...]) -> bool:
    if not trusted_proxy_networks:
        return False
    try:
        source_ip = ip_address(host)
    except ValueError:
        return False
    return any((source_ip in network for network in trusted_proxy_networks))


def _is_trusted_proxy_source(host: str, trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...]) -> bool:
    if not trusted_proxy_networks:
        return False
    try:
        source_ip = ip_address(host)
    except ValueError:
        return False
    return any((source_ip in network for network in trusted_proxy_networks))


def _is_valid_ip(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True


def _split_forwarded_value(value: str, delimiter: str) -> list[str]:
    parts: list[str] = []
    start = 0
    in_quotes = False
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if in_quotes and character == "\\":
            escaped = True
            continue
        if character == '"':
            in_quotes = not in_quotes
            continue
        if character == delimiter and (not in_quotes):
            part = value[start:index].strip()
            if not part:
                raise ValueError("Empty Forwarded header part")
            parts.append(part)
            start = index + 1
    if in_quotes or escaped:
        raise ValueError("Malformed quoted Forwarded header value")
    part = value[start:].strip()
    if not part:
        raise ValueError("Empty Forwarded header part")
    parts.append(part)
    return parts


def _is_http_token(value: str) -> bool:
    return bool(value) and all((character in _HTTP_TOKEN_CHARACTERS for character in value))


def _is_forwarded_quoted_character(character: str, *, escaped: bool) -> bool:
    code_point = ord(character)
    if escaped:
        return code_point == 9 or code_point == 32 or 33 <= code_point <= 126 or (128 <= code_point <= 255)
    return (
        code_point == 9
        or code_point in (32, 33)
        or 35 <= code_point <= 91
        or (93 <= code_point <= 126)
        or (128 <= code_point <= 255)
    )


def _unquote_forwarded_value(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) < 2 or not (value.startswith('"') and value.endswith('"')):
        raise ValueError("Malformed quoted Forwarded header value")
    unescaped: list[str] = []
    escaped = False
    for character in value[1:-1]:
        if escaped:
            if not _is_forwarded_quoted_character(character, escaped=True):
                raise ValueError("Malformed quoted Forwarded header value")
            unescaped.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"' or not _is_forwarded_quoted_character(character, escaped=False):
            raise ValueError("Malformed quoted Forwarded header value")
        else:
            unescaped.append(character)
    if escaped:
        raise ValueError("Malformed quoted Forwarded header value")
    return "".join(unescaped)


def _validate_forwarded_port(port: str) -> None:
    if not 1 <= len(port) <= 5 or not port.isascii() or (not port.isdigit()):
        raise ValueError("Invalid Forwarded node port")
    if not 0 <= int(port) <= 65535:
        raise ValueError("Invalid Forwarded node port")


def _parse_forwarded_node(value: str, *, is_quoted: bool) -> str:
    if value.lower() == "unknown" or value.startswith("_"):
        raise ValueError("Unsupported Forwarded node identifier")
    candidate = value
    expects_ipv6 = value.startswith("[")
    if expects_ipv6:
        if not is_quoted:
            raise ValueError("Bracketed Forwarded nodes must be quoted")
        closing_bracket = value.find("]")
        if closing_bracket == -1:
            raise ValueError("Invalid bracketed Forwarded node")
        candidate = value[1:closing_bracket]
        if "%" in candidate:
            raise ValueError("Scoped IPv6 Forwarded nodes are unsupported")
        suffix = value[closing_bracket + 1 :]
        if suffix:
            if not suffix.startswith(":"):
                raise ValueError("Invalid bracketed Forwarded node")
            _validate_forwarded_port(suffix[1:])
    else:
        try:
            parsed_ip = ip_address(value)
        except ValueError:
            if not is_quoted:
                raise ValueError("Forwarded nodes with ports must be quoted") from None
            candidate, separator, port = value.rpartition(":")
            if not separator or ":" in candidate:
                raise ValueError("Invalid Forwarded node") from None
            _validate_forwarded_port(port)
        else:
            if parsed_ip.version != 4:
                raise ValueError("IPv6 Forwarded nodes must be bracketed")
            return str(parsed_ip)
    try:
        parsed_ip = ip_address(candidate)
    except ValueError:
        raise ValueError("Invalid Forwarded node") from None
    if (parsed_ip.version == 6) != expects_ipv6:
        raise ValueError("Invalid Forwarded node address family")
    return str(parsed_ip)


def _parse_forwarded_pair(parameter: str) -> tuple[str, str, bool]:
    name, separator, raw_value = parameter.partition("=")
    if not separator or name != name.strip() or raw_value != raw_value.strip():
        raise ValueError("Malformed Forwarded parameter")
    normalized_name = name.lower()
    value = raw_value
    if not _is_http_token(normalized_name) or not value:
        raise ValueError("Malformed Forwarded parameter")
    if value.startswith('"'):
        return (normalized_name, _unquote_forwarded_value(value), True)
    if not _is_http_token(value):
        raise ValueError("Malformed Forwarded parameter value")
    return (normalized_name, value, False)


def _parse_forwarded_for_chain(forwarded: str) -> list[str]:
    hops: list[str] = []
    for element in _split_forwarded_value(forwarded, ","):
        forwarded_for: str | None = None
        seen_parameter_names: set[str] = set()
        for parameter in _split_forwarded_value(element, ";"):
            name, value, is_quoted = _parse_forwarded_pair(parameter)
            if name in seen_parameter_names:
                raise ValueError("Duplicate Forwarded parameter")
            seen_parameter_names.add(name)
            if name == "for":
                forwarded_for = _parse_forwarded_node(value, is_quoted=is_quoted)
        if forwarded_for is None:
            raise ValueError("Missing Forwarded for parameter")
        hops.append(forwarded_for)
    return hops


def _resolve_client_ip_from_forwarded_chain(
    socket_ip: str, forwarded: str, trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...]
) -> str:
    return _resolve_client_ip_from_hops(socket_ip, _parse_forwarded_for_chain(forwarded), trusted_proxy_networks)


def _resolve_forwarded_header_ip(forwarded: str) -> str | None:
    for segment in forwarded.split(","):
        for part in segment.split(";"):
            item = part.strip()
            if not item.lower().startswith("for="):
                continue
            candidate = item[4:].strip().strip('"')
            if candidate.startswith("[") and candidate.endswith("]"):
                candidate = candidate[1:-1]
            if candidate.startswith("_"):
                return None
            return candidate if _is_valid_ip(candidate) else None
    return None
