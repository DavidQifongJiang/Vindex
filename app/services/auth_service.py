import os
from dataclasses import dataclass
from functools import lru_cache

from fastapi import Header, HTTPException

try:
    import jwt
    from jwt import PyJWKClient
except ImportError:
    jwt = None
    PyJWKClient = None


@dataclass
class AuthUser:
    user_id: str
    email: str | None = None
    name: str | None = None
    picture_url: str | None = None


def auth_required():
    return os.getenv("AUTH_REQUIRED", "false").lower() == "true"


def dev_user():
    return AuthUser(
        user_id=os.getenv("DEV_USER_ID", "dev-user"),
        email=os.getenv("DEV_USER_EMAIL", "dev@vindex.local"),
        name=os.getenv("DEV_USER_NAME", "Local Developer"),
        picture_url=os.getenv("DEV_USER_PICTURE_URL"),
    )


def cognito_issuer():
    region = os.getenv("COGNITO_REGION")
    user_pool_id = os.getenv("COGNITO_USER_POOL_ID")

    if not region or not user_pool_id:
        raise HTTPException(status_code=500, detail="Cognito is not configured")

    return f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"


@lru_cache(maxsize=1)
def jwks_client():
    if PyJWKClient is None:
        raise HTTPException(status_code=500, detail="PyJWT crypto support is not installed")

    return PyJWKClient(f"{cognito_issuer()}/.well-known/jwks.json")


def bearer_token(authorization: str | None):
    if not authorization:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    return token


def user_from_claims(claims: dict):
    return AuthUser(
        user_id=claims["sub"],
        email=claims.get("email"),
        name=claims.get("name") or claims.get("cognito:username"),
        picture_url=claims.get("picture"),
    )


def verify_cognito_id_token(token: str):
    if jwt is None:
        raise HTTPException(status_code=500, detail="PyJWT crypto support is not installed")

    app_client_id = os.getenv("COGNITO_APP_CLIENT_ID")
    if not app_client_id:
        raise HTTPException(status_code=500, detail="Cognito app client is not configured")

    try:
        signing_key = jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=app_client_id,
            issuer=cognito_issuer(),
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if claims.get("token_use") != "id":
        raise HTTPException(status_code=401, detail="Expected Cognito ID token")

    return user_from_claims(claims)


def get_current_user(authorization: str | None = Header(default=None, alias="Authorization")):
    if not auth_required():
        return dev_user()

    token = bearer_token(authorization)
    if token is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    return verify_cognito_id_token(token)


def get_optional_user(authorization: str | None = Header(default=None, alias="Authorization")):
    if not auth_required():
        return dev_user()

    token = bearer_token(authorization)
    if token is None:
        return None

    return verify_cognito_id_token(token)
