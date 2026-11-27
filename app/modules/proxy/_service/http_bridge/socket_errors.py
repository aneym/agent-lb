from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error

BRIDGE_SOCKET_RETIRED = "bridge_socket_retired"
BRIDGE_SOCKET_RETIRED_MESSAGE = "HTTP bridge retired the upstream websocket before response.completed"


def socket_retired_error() -> ProxyResponseError:
    return ProxyResponseError(502, openai_error(BRIDGE_SOCKET_RETIRED, BRIDGE_SOCKET_RETIRED_MESSAGE))
