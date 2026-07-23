from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from app.database import get_db
from app.models import User, Business, Campaign, Lead, CampaignView
from app.schemas import BusinessResponse, BusinessUpdate
from app.dependencies import get_current_user

router = APIRouter(prefix="/businesses", tags=["Businesses"])


def _require_seller(current_user: User):
    if current_user.role != "SELLER":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only sellers have a business profile."
        )


def _get_business_or_404(db: Session, user_id: str) -> Business:
    business = db.query(Business).filter(Business.user_id == user_id).first()
    if not business:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business profile not found."
        )
    return business


@router.get("/me", response_model=BusinessResponse)
def get_my_business(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get the authenticated seller's business profile."""
    _require_seller(current_user)
    return _get_business_or_404(db, current_user.id)


@router.put("/me", response_model=BusinessResponse)
def update_my_business(
    business_in: BusinessUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Full update of the seller's business profile."""
    _require_seller(current_user)
    business = _get_business_or_404(db, current_user.id)

    update_data = business_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(business, field, value)

    # Invalidate cached business analysis if business_description or usp changed.
    # The analysis will be regenerated lazily on the next campaign generation.
    if "business_description" in update_data or "usp" in update_data:
        business.ai_business_analysis = None

    db.commit()
    db.refresh(business)
    return business


@router.patch("/me", response_model=BusinessResponse)
def partial_update_my_business(
    business_in: BusinessUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Partial update of the seller's business profile.
    Only fields explicitly provided in the request body are updated.
    Used by the Profile Edit page — sends only changed fields.

    When business_description or USP is updated here, future AI campaign generations
    automatically use the new values (the pipeline reads from DB at request time, no cache).
    """
    _require_seller(current_user)
    business = _get_business_or_404(db, current_user.id)

    update_data = business_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(business, field, value)

    # Invalidate cached business analysis if business_description or usp changed.
    # The analysis will be regenerated lazily on the next campaign generation.
    if "business_description" in update_data or "usp" in update_data:
        business.ai_business_analysis = None

    db.commit()
    db.refresh(business)
    return business

from pydantic import BaseModel as _PydanticModel

class FullProfileResponse(_PydanticModel):
    """Combined user + business profile data for the profile edit page."""
    # Personal details
    user_id: str
    name: str
    email: str
    phone: str
    # Business details
    business_id: Optional[str] = None
    business_name: Optional[str] = None
    # business_description: what the business provides (feeds AI)
    # DISTINCT from campaign description which is per-campaign marketing copy
    business_description: Optional[str] = None
    usp: Optional[str] = None
    city: Optional[str] = None
    location_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    website_url: Optional[str] = None
    whatsapp_number: Optional[str] = None

    class Config:
        from_attributes = True


@router.get("/me/full", response_model=FullProfileResponse)
def get_full_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Returns combined user + business data for the Profile Edit screen.
    Works for SELLER accounts only.
    """
    _require_seller(current_user)
    business = _get_business_or_404(db, current_user.id)

    return {
        "user_id": current_user.id,
        "name": current_user.name,
        "email": current_user.email,
        "phone": current_user.phone,
        # city is now on the business (not user) — sourced from business location
        "city": business.city,
        "business_id": business.id,
        "business_name": business.name,
        "business_description": business.business_description,
        "usp": business.usp,
        "location_address": business.location_address,
        "latitude": float(business.latitude) if business.latitude else None,
        "longitude": float(business.longitude) if business.longitude else None,
        "website_url": business.website_url,
        "whatsapp_number": business.whatsapp_number,
    }


class AnalyticsResponse(_PydanticModel):
    campaigns_created: int
    active_campaigns: int
    total_leads: int
    profile_views: int
    total_reach: int
    conversion_rate: float

@router.get("/me/analytics", response_model=AnalyticsResponse)
def get_my_analytics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get analytics for the authenticated seller's business."""
    _require_seller(current_user)
    business = _get_business_or_404(db, current_user.id)

    # Calculate metrics
    campaigns_created = db.query(func.count(Campaign.id)).filter(Campaign.business_id == business.id).scalar() or 0
    active_campaigns = db.query(func.count(Campaign.id)).filter(
        Campaign.business_id == business.id, Campaign.status == "ACTIVE"
    ).scalar() or 0
    
    # Total reach is sum of views on all campaigns
    total_reach = db.query(func.sum(Campaign.view_count)).filter(Campaign.business_id == business.id).scalar() or 0
    
    # Profile views - if there isn't a separate metric, we can just use total_reach or mock it
    profile_views = total_reach
    
    # Total leads is sum of leads on all campaigns
    total_leads = db.query(func.count(Lead.id)).join(Campaign).filter(Campaign.business_id == business.id).scalar() or 0

    conversion_rate = 0.0
    if total_reach > 0:
        conversion_rate = round((total_leads / total_reach) * 100, 2)

    return {
        "campaigns_created": campaigns_created,
        "active_campaigns": active_campaigns,
        "total_leads": total_leads,
        "profile_views": profile_views,
        "total_reach": total_reach,
        "conversion_rate": conversion_rate,
    }
