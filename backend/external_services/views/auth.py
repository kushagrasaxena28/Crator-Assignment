"""Token issuance — one endpoint per identity.

Both are unauthenticated to call (they *are* the authentication step) and both are a credential
exchange: a user trades a username and password for a user token, an agent trades its id and
secret for an agent token. Neither issues the other's token type, so obtaining an agent's secret
never yields the ability to approve that agent's work.

Identities are provisioned out of band (see the `seed` command), never self-registered — creating
an identity is an operator concern, and public sign-up would undermine the whole trust model.
"""

from django.contrib.auth.hashers import make_password
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from ..models import User, get_agent
from ..tokens import issue_agent_token, issue_user_token

# Every failed exchange returns exactly this, whether the identity was missing, the credential was
# wrong, or the account was disabled. Distinguishing them would let a caller enumerate valid
# usernames and agent ids by reading the error.
_INVALID = {"error": "invalid_credentials", "message": "Invalid credentials."}


def _burn_a_hash(raw_secret: str) -> None:
    """Run the password hasher once on a lookup miss.

    Without this, a request for an identity that doesn't exist returns measurably faster than one
    with a wrong password, because the second path pays for a PBKDF2 comparison and the first does
    not. Doing the work anyway keeps the two indistinguishable. (This mirrors what
    django.contrib.auth's own ModelBackend does for the same reason.)"""
    make_password(raw_secret)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def issue_user_access_token(request):
    """POST /api/auth/user/token/ — exchange a username and password for a short-lived user token.

    This is the credential that can approve held actions, so it must never be stored anywhere an
    agent can read it."""
    if not isinstance(request.data, dict):
        return Response({"error": "invalid_request", "message": "Body must be a JSON object."}, status=400)

    username = request.data.get("username")
    password = request.data.get("password")
    if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
        return Response(
            {"error": "invalid_request", "message": "username and password are required."}, status=400
        )

    user = User.objects.filter(username=username).first()
    if user is None:
        _burn_a_hash(password)
        return Response(_INVALID, status=401)
    if not user.check_password(password) or not user.is_active:
        return Response(_INVALID, status=401)

    token, expires_at = issue_user_token(user)
    return Response({"token": token, "expires_at": expires_at, "user_id": str(user.id)})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def issue_agent_access_token(request):
    """POST /api/auth/agent/token/ — exchange an agent id and secret for a short-lived agent token.

    The secret is what makes this an authentication step rather than a lookup. An agent id alone is
    an identifier, not a credential: it appears in URLs, in every audit row, and in the CLI's
    configuration file, so issuing tokens on the strength of knowing one would mean the system had
    no agent authentication at all."""
    if not isinstance(request.data, dict):
        return Response({"error": "invalid_request", "message": "Body must be a JSON object."}, status=400)

    agent_id = request.data.get("agent_id")
    agent_secret = request.data.get("agent_secret")
    if not isinstance(agent_id, str) or not agent_id or not isinstance(agent_secret, str) or not agent_secret:
        return Response(
            {"error": "invalid_request", "message": "agent_id and agent_secret are required."}, status=400
        )

    agent = get_agent(agent_id)
    if agent is None:
        _burn_a_hash(agent_secret)
        return Response(_INVALID, status=401)
    if not agent.check_secret(agent_secret):
        return Response(_INVALID, status=401)
    if not agent.is_active or not agent.owner.is_active:
        return Response(_INVALID, status=401)

    token, expires_at = issue_agent_token(agent)
    return Response({"token": token, "expires_at": expires_at, "agent_id": str(agent.id)})


# Not implemented here, and worth naming: neither endpoint is rate-limited, so both are open to
# online brute force. In production these are the two routes that most need a throttle plus
# alerting on repeated failures for one identity.
