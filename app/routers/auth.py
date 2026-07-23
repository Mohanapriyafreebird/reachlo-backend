from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, Business, LoginHistory
from app.schemas import UserRegister, UserLogin, Token, UserUpdate, UserResponse
from pydantic import BaseModel as _PydanticModel
from app.security import get_password_hash, verify_password, create_access_token
from app.dependencies import get_current_user
import re
from datetime import datetime

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Indian state names and common country labels to skip when extracting city
_INDIAN_STATES = {
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh",
    "goa", "gujarat", "haryana", "himachal pradesh", "jharkhand", "karnataka",
    "kerala", "madhya pradesh", "maharashtra", "manipur", "meghalaya", "mizoram",
    "nagaland", "odisha", "punjab", "rajasthan", "sikkim", "tamil nadu",
    "telangana", "tripura", "uttar pradesh", "uttarakhand", "west bengal",
    "delhi", "jammu and kashmir", "ladakh", "puducherry", "chandigarh",
    "india",
}


def _extract_city_from_address(address: str) -> str:
    """
    Smartly extract city from a full address string like:
    'Third floor, J4B, Periyar St, Medavakkam, Chennai, Tamil Nadu 600100, India'
    Strategy: split by comma, strip each part, skip pin codes / state names / country.
    The city is typically the last meaningful segment before the state+pincode.
    """
    if not address:
        return ""
    parts = [p.strip() for p in address.split(",") if p.strip()]
    candidates = []
    for part in parts:
        # Remove embedded pin codes (6-digit numbers) from the part
        clean = re.sub(r"\b\d{6}\b", "", part).strip()
        if not clean:
            continue
        # Skip if it's a known state name or country (case-insensitive)
        if clean.lower() in _INDIAN_STATES:
            continue
        candidates.append(clean)
    # The city is typically the last clean candidate
    # (address goes from specific → general, so last = city/district level)
    if candidates:
        return candidates[-1]
    return ""


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
def register(user_in: UserRegister, db: Session = Depends(get_db)):
    """
    Single registration endpoint for both BUYER and SELLER.
    For SELLER accounts: accepts additional seller-specific fields
    (business_description, usp, latitude, longitude, location_address).
    Both users and businesses rows are created in the same transaction to avoid orphaned records.
    """
    # Check if user already exists
    existing_user = db.query(User).filter(User.email == user_in.email.lower()).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already registered."
        )

    # Create new user (city/area no longer stored on users table)
    hashed_password = get_password_hash(user_in.password)
    new_user = User(
        name=user_in.name,
        email=user_in.email.lower(),
        phone=user_in.phone,
        password_hash=hashed_password,
        role=user_in.role,
        is_active=True
    )
    db.add(new_user)
    db.commit()
    new_user = db.query(User).filter(User.email == user_in.email.lower()).first()

    # If the user is a SELLER, create business profile in the same transaction
    if new_user.role == "SELLER":
        business_name = (user_in.company_name or "").strip() or f"{new_user.name}'s Business"

        # Smart city extraction from location_address
        # e.g. 'Third floor, J4B, Periyar St, Medavakkam, Chennai, Tamil Nadu 600100, India' → 'Chennai'
        biz_city = ""
        if user_in.location_address:
            biz_city = _extract_city_from_address(user_in.location_address)
        if not biz_city and user_in.city:
            biz_city = user_in.city.strip()
        biz_city = biz_city or "Unknown"

        # Auto-detect category using Gemini Flash (runs once at registration, result saved to DB)
        detected_category = "Other"
        detected_sub_category = None
        ai_business_analysis_json = None

        if user_in.business_description:
            try:
                from app.utils.ai_generation import detect_category
                result = detect_category(user_in.business_description)
                detected_category = result.get("category", "Other") or "Other"
                detected_sub_category = result.get("sub_category")
            except Exception as e:
                # If Gemini call fails, default to "Other" — seller can override from profile
                print(f"[WARN] Category auto-detection failed: {e}")

            # Pre-compute and cache business analysis so first campaign generation is fast
            try:
                from app.utils.ai_generation import analyze_business
                import json as _json
                analysis = analyze_business(
                    business_name=business_name,
                    business_description=user_in.business_description,
                    usp=user_in.usp or "",
                    category=detected_category,
                    city=biz_city,
                )
                ai_business_analysis_json = _json.dumps(analysis)
            except Exception as e:
                # Non-fatal — analysis generated lazily on first campaign generation
                print(f"[WARN] Business analysis pre-computation failed: {e}")

        new_business = Business(
            user_id=new_user.id,
            name=business_name,
            category=detected_category,
            sub_category=detected_sub_category,
            # business_description: what the business provides — feeds AI generation
            # DISTINCT from campaigns.description which is per-campaign marketing copy
            business_description=user_in.business_description,
            usp=user_in.usp,
            city=biz_city,
            location_address=user_in.location_address,
            latitude=user_in.latitude,
            longitude=user_in.longitude,
            ai_business_analysis=ai_business_analysis_json,
            # Auto-populate whatsapp_number from the seller's phone number
            whatsapp_number=new_user.phone,
            verified=False,
            rating=0.0,
            rating_count=0
        )
        db.add(new_business)
        db.commit()

    # Generate token — seller is immediately logged in after registration
    access_token = create_access_token(data={"sub": new_user.email})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": new_user.role,
        "user": new_user
    }


@router.post("/login", response_model=Token)
def login(login_in: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == login_in.email.lower()).first()
    if not user or not verify_password(login_in.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password."
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User account is deactivated."
        )

    # Record login event in login_history
    try:
        login_record = LoginHistory(
            user_id=user.id,
            login_time=datetime.utcnow()
        )
        db.add(login_record)
        db.commit()
    except Exception as e:
        # Non-fatal — don't block login if history insert fails
        print(f"[WARN] Failed to record login history: {e}")
        db.rollback()

    access_token = create_access_token(data={"sub": user.email})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user.role,
        "user": user
    }


@router.get("/me", response_model=Token)
def get_me(current_user: User = Depends(get_current_user)):
    access_token = create_access_token(data={"sub": current_user.email})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": current_user.role,
        "user": current_user
    }


@router.patch("/me", response_model=UserResponse)
def update_my_user_profile(
    user_in: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update personal details (name, phone) from the profile edit page."""
    update_data = user_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(current_user, field, value)
    db.commit()
    db.refresh(current_user)
    return current_user

class ChangePasswordRequest(_PydanticModel):
    current_password: str
    new_password: str

@router.post("/change-password", status_code=status.HTTP_200_OK)
def change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect current password"
        )
    if len(payload.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters."
        )
    
    current_user.password_hash = get_password_hash(payload.new_password)
    db.commit()
    return {"message": "Password updated successfully."}


from pydantic import BaseModel as PydanticBaseModel

class ResetPasswordRequest(PydanticBaseModel):
    email: str
    new_password: str

@router.post("/reset-password", status_code=200)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email.lower()).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account found with this email address."
        )
    
    if len(payload.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters."
        )
    
    user.password_hash = get_password_hash(payload.new_password)
    db.commit()
    return {"message": "Password reset successfully."}


from app.schemas import PushTokenUpdate

@router.post("/push-token", status_code=200)
def update_push_token(
    payload: PushTokenUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update the authenticated user's Expo push token."""
    current_user.expo_push_token = payload.expo_push_token
    db.commit()
    return {"message": "Push token updated."}
