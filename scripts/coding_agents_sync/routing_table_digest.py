import hashlib


def table_digest(policy):
    single = policy / "routing-table.json"
    parts = [single] if single.is_file() else sorted((policy / "routing-table").glob("*.json"))
    if not parts:
        raise FileNotFoundError("no routing table under %s" % policy)
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.read_bytes())
    return digest.hexdigest()
