import bcrypt
import jwt
from datetime import datetime, timedelta
from typing import Dict, Any
from backend.shared.config.settings import settings
from backend.shared.exceptions.auth import AuthenticationException

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def create_access_token(data: Dict[str, Any], expires_delta: timedelta = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.jwt_expiration_minutes)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return encoded_jwt

def decode_token(token: str) -> Dict[str, Any]:
    try:
        decoded_data = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return decoded_data
    except jwt.ExpiredSignatureError:
        raise AuthenticationException(code="TOKEN_EXPIRED", message="JWT Token has expired")
    except jwt.InvalidTokenError:
        raise AuthenticationException(code="INVALID_TOKEN", message="Invalid JWT Token")

def verify_token(token: str) -> bool:
    try:
        decode_token(token)
        return True
    except AuthenticationException:
        return False
