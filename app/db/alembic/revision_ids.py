from __future__ import annotations

import re

REVISION_ID_PATTERN = re.compile(r"^\d{8}_\d{6}_[a-z0-9_]+$")
