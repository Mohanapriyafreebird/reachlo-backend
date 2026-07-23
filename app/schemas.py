from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime

# --- USER SCHEMAS ---
class UserRegister(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    phone: str = Field(..., min_length=10, max_length=20)
    password: str = Field(..., min_length=8)
    role: str = Field("BUYER", pattern="^(BUYER|SELLER|ADMIN)$")
    # city is kept optional for backward compat with existing buyer registration frontend
    # For SELLER: city is extracted from location_address server-side
    city: Optional[str] = None
    area: Optional[str] = None
    # Seller-specific fields (only used when role=SELLER)
    company_name: Optional[str] = None               # Brand/company name for SELLER accounts
    # business_description: "What does your business provide?" — feeds AI generation
    # DISTINCT from campaign description which is per-campaign marketing copy
    business_description: Optional[str] = None
    usp: Optional[str] = None                        # Unique Selling Proposition
    # Business location (from location picker in Step 2 of seller registration)
    location_address: Optional[str] = None           # Human-readable full address for display
    latitude: Optional[float] = None                 # GPS lat — for Haversine distance queries
    longitude: Optional[float] = None                # GPS lng

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: str
    name: str
    email: str
    phone: str
    role: str
    is_active: bool
    city: Optional[str] = None
    profile_picture: Optional[str] = None
    preferences: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class UserUpdate(BaseModel):
    """For updating personal details from the profile edit page."""
    name: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    profile_picture: Optional[str] = None
    preferences: Optional[str] = None

# --- BUSINESS SCHEMAS ---
class BusinessResponse(BaseModel):
    id: str
    user_id: str
    name: str
    # business_description: what the business provides — feeds AI
    # NOTE: not the same as campaigns.description (per-campaign marketing copy)
    business_description: Optional[str] = None
    usp: Optional[str] = None
    city: Optional[str] = None
    location_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    website_url: Optional[str] = None
    whatsapp_number: Optional[str] = None
    verified: bool
    rating: float
    rating_count: int

    class Config:
        from_attributes = True

class BusinessUpdate(BaseModel):
    name: Optional[str] = None
    business_description: Optional[str] = None
    usp: Optional[str] = None
    city: Optional[str] = None
    location_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    website_url: Optional[str] = None
    whatsapp_number: Optional[str] = None

# --- CAMPAIGN SCHEMAS ---
class CampaignCreate(BaseModel):
    title: str = Field(..., max_length=150)
    # description: per-campaign marketing copy — DISTINCT from business.business_description
    description: str
    offer: str = Field(..., max_length=150)
    image_url: Optional[str] = None
    image_urls: Optional[List[str]] = None
    cta_type: Optional[str] = None
    cta_value: Optional[str] = None
    category: Optional[str] = None
    target_audience: Optional[str] = None
    price: Optional[float] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    target_cities: Optional[str] = None
    # Optional exact location
    location_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    google_place_id: Optional[str] = None
    ai_generated: Optional[bool] = False

class CampaignUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    offer: Optional[str] = None
    image_url: Optional[str] = None
    image_urls: Optional[List[str]] = None
    cta_type: Optional[str] = None
    cta_value: Optional[str] = None
    category: Optional[str] = None
    target_audience: Optional[str] = None
    target_cities: Optional[str] = None
    price: Optional[float] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    status: Optional[str] = None
    location_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    google_place_id: Optional[str] = None

class CampaignResponse(BaseModel):
    id: str
    business_id: str
    title: str
    description: str
    offer: str
    image_url: Optional[str] = None
    image_urls: Optional[List[str]] = None
    cta_type: Optional[str] = None
    cta_value: Optional[str] = None
    category: Optional[str] = None
    target_audience: Optional[str] = None
    target_cities: Optional[str] = None
    price: Optional[float] = None
    start_date: datetime
    end_date: Optional[datetime] = None
    status: str
    is_boosted: Optional[bool] = False
    boost_until: Optional[datetime] = None
    view_count: Optional[int] = 0
    lead_count: Optional[int] = 0
    created_at: datetime
    location_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    google_place_id: Optional[str] = None
    business_name: Optional[str] = None
    business_verified: Optional[bool] = False
    ai_generated: Optional[bool] = False

    class Config:
        from_attributes = True

# --- LEAD SCHEMAS ---
class LeadCreate(BaseModel):
    name: str
    phone: str
    message: Optional[str] = None

class LeadUpdate(BaseModel):
    label: Optional[str] = None
    notes: Optional[str] = None
    is_read: Optional[bool] = None

class LeadResponse(BaseModel):
    id: str
    campaign_id: str
    buyer_id: Optional[str] = None
    name: str
    phone: str
    message: Optional[str] = None
    label: str
    notes: Optional[str] = None
    is_read: bool
    created_at: datetime
    campaign_title: Optional[str] = None

    class Config:
        from_attributes = True

# --- AUTH TOKEN RESPONSE ---
class Token(BaseModel):
    access_token: str
    token_type: str
    role: str
    user: UserResponse

# --- AI CAMPAIGN GENERATION SCHEMAS ---

class AIGenerateRequest(BaseModel):
    """Seller inputs for triggering AI campaign generation."""
    campaign_topic: str = Field(..., min_length=5, max_length=500,
                                description="What is this campaign promoting? Be specific.")
    price_or_deal: Optional[str] = Field(None, max_length=255,
                                         description="Optional price or deal to highlight")
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    target_cities: Optional[str] = None
    location_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class AIPublishRequest(BaseModel):
    """
    Editable fields the seller may have changed in the draft review screen.
    All fields are optional — only send what changed. draft_id comes from URL path.
    """
    title: Optional[str] = Field(None, max_length=200)
    # campaign_description: the per-campaign marketing copy to be stored in campaigns.description
    campaign_description: Optional[str] = None
    offer: Optional[str] = Field(None, max_length=255)
    cta_type: Optional[str] = None
    cta_value: Optional[str] = None
    target_audience: Optional[str] = None
    target_cities: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    image_url: Optional[str] = None   # In case image was regenerated
    price: Optional[float] = None     # Exact campaign price set by seller (₹ value)

class AICampaignDraftResponse(BaseModel):
    """Response returned after generation — includes all content + warnings."""
    id: str
    business_id: str
    campaign_topic: str
    price_or_deal: Optional[str] = None
    price: Optional[float] = None                # Exact price set by seller (₹ value)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    title: Optional[str] = None
    # campaign_description: per-campaign AI-generated copy
    # Will be stored as campaigns.description when published
    campaign_description: Optional[str] = None
    offer: Optional[str] = None
    cta_type: Optional[str] = None
    cta_value: Optional[str] = None
    target_audience: Optional[str] = None
    target_cities: Optional[str] = None
    location_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    image_url: Optional[str] = None
    market_signals_used: Optional[str] = None   # JSON string
    hallucination_warnings: Optional[str] = None # JSON string
    ai_pipeline_stages: Optional[str] = None     # JSON string: full reasoning chain (marketing_strategy, buyer_psychology, creative_brief)
    status: str
    campaign_id: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# --- CHAT SCHEMAS ---

class ChatThreadCreate(BaseModel):
    """Payload to open a new chat thread — client sends the lead_id returned by POST /leads."""
    lead_id: str

class ChatMessageCreate(BaseModel):
    """Payload to send a new message inside an existing thread."""
    body: str

class ChatMessageResponse(BaseModel):
    id: str
    thread_id: str
    sender_id: str
    sender_role: str      # BUYER | SELLER | SYSTEM
    body: str
    is_system: bool
    sender_name: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class ChatThreadResponse(BaseModel):
    id: str
    lead_id: str
    campaign_id: str
    buyer_id: str
    seller_id: str
    last_message_at: datetime
    last_buyer_message_at: Optional[datetime] = None
    last_seller_reply_at: Optional[datetime] = None
    seller_unread_count: int
    buyer_unread_count: int
    created_at: datetime
    # Denormalised display fields populated server-side
    campaign_title: Optional[str] = None
    campaign_image_url: Optional[str] = None
    buyer_name: Optional[str] = None
    seller_name: Optional[str] = None
    buyer_phone: Optional[str] = None
    seller_phone: Optional[str] = None
    last_message_body: Optional[str] = None
    total_messages: Optional[int] = 0

    class Config:
        from_attributes = True

class UnreadCountResponse(BaseModel):
    unread_count: int

class PushTokenUpdate(BaseModel):
    """Sent by the client after obtaining an Expo push token."""
    expo_push_token: str
