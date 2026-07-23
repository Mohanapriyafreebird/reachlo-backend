from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.models import User, Business, Campaign, Lead
from app.schemas import LeadCreate, LeadUpdate, LeadResponse
from app.dependencies import get_current_user, get_optional_current_user

router = APIRouter(prefix="/leads", tags=["Leads"])

@router.post("", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
def create_lead(
    campaign_id: str,
    lead_in: LeadCreate,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user)
):
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found."
        )

    if current_user:
        existing_lead = db.query(Lead).filter(
            Lead.campaign_id == campaign_id,
            Lead.buyer_id == current_user.id
        ).first()
        if existing_lead:
            res = LeadResponse.from_orm(existing_lead)
            res.campaign_title = campaign.title
            return res
        
    # Older campaign rows can have NULL counters; treat them as zero.
    campaign.lead_count = (campaign.lead_count or 0) + 1
    
    new_lead = Lead(
        campaign_id=campaign_id,
        buyer_id=current_user.id if current_user else None,
        name=lead_in.name,
        phone=lead_in.phone,
        message=lead_in.message or f"Interested in your offer: '{campaign.offer}'",
        label="NEW",
        is_read=False
    )
    
    db.add(new_lead)
    db.commit()
    db.refresh(new_lead)
    
    res = LeadResponse.from_orm(new_lead)
    res.campaign_title = campaign.title
    return res

@router.get("", response_model=List[LeadResponse])
def get_leads(
    campaign_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role != "SELLER":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only sellers can view leads."
        )
        
    business = db.query(Business).filter(Business.user_id == current_user.id).first()
    if not business:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Seller business profile not found."
        )
        
    query = db.query(Lead).join(Campaign).filter(Campaign.business_id == business.id)
    
    if campaign_id:
        query = query.filter(Lead.campaign_id == campaign_id)
        
    leads = query.order_by(Lead.created_at.desc()).all()
    
    result = []
    for l in leads:
        res = LeadResponse.from_orm(l)
        res.campaign_title = l.campaign.title
        result.append(res)
        
    return result

@router.put("/{id}", response_model=LeadResponse)
def update_lead(
    id: str,
    lead_in: LeadUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    lead = db.query(Lead).filter(Lead.id == id).first()
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found."
        )
        
    # Verify the seller owns the campaign
    campaign = db.query(Campaign).filter(Campaign.id == lead.campaign_id).first()
    business = db.query(Business).filter(Business.id == campaign.business_id).first()
    if not business or business.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this lead."
        )
        
    update_data = lead_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(lead, field, value)
        
    db.commit()
    db.refresh(lead)
    
    res = LeadResponse.from_orm(lead)
    res.campaign_title = campaign.title
    return res
