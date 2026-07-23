from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.database import get_db
from app.security import decode_access_token
from app.models import User

reusable_oauth2 = HTTPBearer()
optional_oauth2 = HTTPBearer(auto_error=False)

def get_current_user(
    db: Session = Depends(get_db),
    token: HTTPAuthorizationCredentials = Depends(reusable_oauth2)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    payload = decode_access_token(token.credentials)
    if payload is None:
        raise credentials_exception
        
    email: str = payload.get("sub")
    if email is None:
        raise credentials_exception
        
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception
        
    return user


def get_optional_current_user(
    db: Session = Depends(get_db),
    token: HTTPAuthorizationCredentials | None = Depends(optional_oauth2)
) -> User | None:
    if token is None:
        return None

    payload = decode_access_token(token.credentials)
    if payload is None:
        return None

    email: str | None = payload.get("sub")
    if email is None:
        return None

    return db.query(User).filter(User.email == email).first()

async def get_ws_current_user(
    token: str,
    db: Session = Depends(get_db),
) -> User:
    from fastapi import WebSocketException, status
    exception = WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    if not token:
        raise exception
    
    payload = decode_access_token(token)
    if payload is None:
        raise exception
        
    email: str = payload.get("sub")
    if email is None:
        raise exception
        
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise exception
        
    return user
