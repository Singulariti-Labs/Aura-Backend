import json
from typing import Optional
from jose import jwt
from urllib.request import urlopen
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import os
from dotenv import load_dotenv

load_dotenv()

AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN")
AUTH0_API_AUDIENCE = os.getenv("AUTH0_API_AUDIENCE")
ALGORITHMS = ["RS256"]

class VerifyToken():
    """Does all the token verification using PyJWT"""

    def __init__(self):
        self.jwks_url = f'https://{AUTH0_DOMAIN}/.well-known/jwks.json'
        self.jwks = None
        self._load_jwks()

    def _load_jwks(self):
        try:
            with urlopen(self.jwks_url) as response:
                self.jwks = json.loads(response.read())
        except Exception as e:
            print(f"❌ Failed to load JWKS: {e}")

    def verify(self, token: str):
        if not self.jwks:
            self._load_jwks()
            if not self.jwks:
                raise HTTPException(status_code=500, detail="JWT public keys not found")

        try:
            unverified_header = jwt.get_unverified_header(token)
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid token header")

        rsa_key = {}
        for key in self.jwks["keys"]:
            if key["kid"] == unverified_header["kid"]:
                rsa_key = {
                    "kty": key["kty"],
                    "kid": key["kid"],
                    "use": key["use"],
                    "n": key["n"],
                    "e": key["e"]
                }
        
        if rsa_key:
            try:
                payload = jwt.decode(
                    token,
                    rsa_key,
                    algorithms=ALGORITHMS,
                    audience=AUTH0_API_AUDIENCE,
                    issuer=f"https://{AUTH0_DOMAIN}/"
                )
                return payload
            except jwt.ExpiredSignatureError:
                raise HTTPException(status_code=401, detail="Token is expired")
            except jwt.JWTClaimsError:
                raise HTTPException(status_code=401, detail="Incorrect claims, please check the audience and issuer")
            except Exception:
                raise HTTPException(status_code=401, detail="Unable to parse authentication token")

        raise HTTPException(status_code=401, detail="Unable to find appropriate key")

token_verifier = VerifyToken()
auth_scheme = HTTPBearer()

async def get_user_info(token: str, userinfo_url: str = None) -> dict:
    """
    Fetch user info from Auth0 /userinfo endpoint.
    """
    try:
        url = userinfo_url if userinfo_url else f"https://{AUTH0_DOMAIN}/userinfo"
        headers = {"Authorization": f"Bearer {token}"}
        
        from urllib.request import Request
        request = Request(url, headers=headers)
        with urlopen(request) as response:
            return json.loads(response.read())
    except Exception as e:
        print(f"❌ Failed to fetch user info: {e}")
        return {}

async def get_current_user(token: HTTPAuthorizationCredentials = Depends(auth_scheme)):
    """
    Dependency to get the current user from the Auth0 token.
    """
    result = token_verifier.verify(token.credentials)
    return result
