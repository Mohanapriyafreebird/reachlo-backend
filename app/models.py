import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Integer, Float, ForeignKey, DateTime, Text, Numeric, BigInteger
from sqlalchemy.orm import relationship
from app.database import Base

def generate_uuid():
    # The existing MySQL schema uses VARCHAR(10) id columns.
    return uuid.uuid4().hex[:10]

class User(Base):
    __tablename__ = "users"

    user_no = Column(Integer, primary_key=True, autoincrement=True)
    id = Column(String(10), unique=True, index=True, default=generate_uuid)
    name = Column(String(100), nullable=False)
    email = Column(String(150), unique=True, index=True, nullable=False)
    phone = Column(String(20), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="BUYER") # BUYER, SELLER, ADMIN
    is_active = Column(Boolean, default=True)
    expo_push_token = Column(String(255), nullable=True)  # Expo push token for device notifications
    city = Column(String(100), nullable=True)
    profile_picture = Column(String(255), nullable=True)
    preferences = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    business = relationship("Business", back_populates="user", uselist=False, cascade="all, delete-orphan")
    leads = relationship("Lead", back_populates="buyer", foreign_keys="Lead.buyer_id")
    views = relationship("CampaignView", back_populates="viewer")
    saved_campaigns = relationship("SavedCampaign", back_populates="buyer", cascade="all, delete-orphan")
    ratings = relationship("Rating", back_populates="buyer")
    login_history = relationship("LoginHistory", back_populates="user", cascade="all, delete-orphan")
    chat_threads_as_buyer = relationship("ChatThread", back_populates="buyer", foreign_keys="ChatThread.buyer_id", cascade="all, delete-orphan")
    chat_threads_as_seller = relationship("ChatThread", back_populates="seller", foreign_keys="ChatThread.seller_id", cascade="all, delete-orphan")
    sent_messages = relationship("ChatMessage", back_populates="sender", foreign_keys="ChatMessage.sender_id", cascade="all, delete-orphan")


class Business(Base):
    __tablename__ = "businesses"

    business_no = Column(Integer, primary_key=True, autoincrement=True)
    id = Column(String(10), unique=True, index=True, default=generate_uuid)
    user_id = Column(String(10), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    name = Column(String(150), nullable=False)

    # category/sub_category are kept for AI pipeline use (market signals, generation, playbooks)
    # They are NOT exposed in the main API responses — the frontend no longer sees them.
    category = Column(String(100), nullable=True)
    sub_category = Column(String(100), nullable=True)

    # business_description: what the seller's business provides (set at registration, feeds AI generation)
    # NOTE: distinct from campaigns.description which is the per-campaign copy written/generated for each campaign
    business_description = Column(Text, nullable=True) # "What does your business provide?" — primary field for AI
    usp = Column(Text, nullable=True)                  # "What makes you different?" — personalisation signal for AI
    # Cached Gemini business analysis — computed once at registration, reused for all campaign generations.
    # Invalidated and recomputed when seller updates business_description or usp.
    ai_business_analysis = Column(Text, nullable=True) # JSON blob: industry, positioning, buyer_persona, strengths, etc.
    # Visual style memory — locked after first campaign generation, reused + slightly varied for brand consistency.
    # JSON blob: {"palette": str, "mood": str, "subject_type": str, "style_descriptor": str}
    ai_visual_style_memory = Column(Text, nullable=True)

    # city is kept for AI market signal lookups and campaign proximity queries
    city = Column(String(100), nullable=True)

    # Business location (set via location picker at registration or profile edit)
    location_address = Column(Text, nullable=True)      # human-readable full address for display
    latitude = Column(Numeric(10, 7), nullable=True)    # e.g. 13.0826802 — used for Haversine distance queries
    longitude = Column(Numeric(10, 7), nullable=True)   # e.g. 80.2707184

    website_url = Column(String(255), nullable=True)
    whatsapp_number = Column(String(20), nullable=True)  # auto-populated from users.phone at registration
    verified = Column(Boolean, default=False)
    rating = Column(Float, default=0.0)
    rating_count = Column(Integer, default=0)

    # Relationships
    user = relationship("User", back_populates="business")
    campaigns = relationship("Campaign", back_populates="business", cascade="all, delete-orphan")
    ratings = relationship("Rating", back_populates="business", cascade="all, delete-orphan")
    ai_drafts = relationship("AICampaignDraft", back_populates="business", cascade="all, delete-orphan")


class Campaign(Base):
    __tablename__ = "campaigns"

    campaign_no = Column(Integer, primary_key=True, autoincrement=True)
    id = Column(String(10), unique=True, index=True, default=generate_uuid)
    business_id = Column(String(10), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(150), nullable=False)
    # campaign description: the per-campaign marketing copy (AI-generated or manually written)
    # DISTINCT from business.business_description which describes the seller's overall business
    description = Column(Text, nullable=False)
    offer = Column(String(150), nullable=False) # e.g. "Buy 1 Get 1 Free"
    image_url = Column(String(255), nullable=True)
    image_urls = Column(Text, nullable=True)  # JSON array of up to 5 image URLs
    cta_type = Column(String(50), nullable=True) # WhatsApp, Call, Link, Form
    cta_value = Column(String(255), nullable=True)
    category = Column(String(150), nullable=True)
    target_audience = Column(String(255), nullable=True)
    target_cities = Column(Text, nullable=True)  # JSON list of target cities (e.g., ["Chennai", "Coimbatore"] or ["All India"])
    # Optional exact business location
    location_address = Column(Text, nullable=True)
    latitude = Column(Numeric(10, 8), nullable=True)
    longitude = Column(Numeric(11, 8), nullable=True)
    google_place_id = Column(String(255), nullable=True)
    start_date = Column(DateTime, default=datetime.utcnow)
    end_date = Column(DateTime, nullable=True)
    status = Column(String(20), default="ACTIVE") # ACTIVE, DRAFT, EXPIRED, DELETED
    is_boosted = Column(Boolean, default=False)
    boost_until = Column(DateTime, nullable=True)
    view_count = Column(Integer, default=0)
    lead_count = Column(Integer, default=0)
    price = Column(Float, nullable=True)
    # Flag indicating this campaign was generated by AI (not built manually)
    ai_generated = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    business = relationship("Business", back_populates="campaigns")
    leads = relationship("Lead", back_populates="campaign", cascade="all, delete-orphan")
    views = relationship("CampaignView", back_populates="campaign", cascade="all, delete-orphan")
    saved_by = relationship("SavedCampaign", back_populates="campaign", cascade="all, delete-orphan")


class AICampaignDraft(Base):
    """
    Stores AI-generated campaign drafts before the seller reviews and publishes them.
    One draft per generation attempt. Status: DRAFT → PUBLISHED or DISCARDED.
    The campaign_description here becomes campaigns.description when published.
    """
    __tablename__ = "ai_campaign_drafts"

    draft_no = Column(Integer, primary_key=True, autoincrement=True)
    id = Column(String(10), unique=True, index=True, default=generate_uuid)
    business_id = Column(String(10), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)

    # Seller inputs that triggered this generation
    campaign_topic = Column(Text, nullable=False)       # "What is this campaign about?"
    price_or_deal = Column(String(255), nullable=True)  # Optional deal/price the seller mentioned
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)

    # AI-generated content fields
    title = Column(String(200), nullable=True)
    # campaign_description: per-campaign AI-generated marketing copy
    # This populates campaigns.description when published — distinct from business_description
    campaign_description = Column(Text, nullable=True)
    offer = Column(String(255), nullable=True)
    cta_type = Column(String(50), nullable=True)        # WHATSAPP, CALL, LINK, FORM
    cta_value = Column(String(255), nullable=True)
    target_audience = Column(String(50), nullable=True) # B2C, B2B, BOTH
    target_cities = Column(Text, nullable=True)  # JSON list of target cities
    
    # Location
    location_address = Column(Text, nullable=True)
    latitude = Column(Numeric(10, 8), nullable=True)
    longitude = Column(Numeric(11, 8), nullable=True)
    
    # Image
    image_url = Column(String(500), nullable=True)      # URL/path of generated thumbnail
    image_prompt = Column(Text, nullable=True)          # Prompt sent to Flux — stored for regeneration

    # Intelligence signals used (JSON)
    market_signals_used = Column(Text, nullable=True)   # JSON: {festival, trend, season}
    hallucination_warnings = Column(Text, nullable=True)# JSON list of warning strings
    # Full pipeline reasoning stages stored for auditability and future Creative Memory
    ai_pipeline_stages = Column(Text, nullable=True)    # JSON blob: {marketing_strategy, buyer_psychology, creative_brief}

    # Lifecycle
    status = Column(String(20), default="DRAFT")  # DRAFT, PUBLISHED, DISCARDED
    campaign_id = Column(String(10), nullable=True) # Reference to campaigns(id) but no hard FK to prevent flush issues
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    business = relationship("Business", back_populates="ai_drafts")
    published_campaign = relationship("Campaign", primaryjoin="AICampaignDraft.campaign_id == Campaign.id", foreign_keys=[campaign_id])


class MarketSignal(Base):
    """
    Pre-computed daily market signals per category+city.
    Refreshed by a background job at 2AM IST. Generation reads this, never computes at request time.
    """
    __tablename__ = "market_signals"

    id = Column(String(10), primary_key=True, default=generate_uuid)
    category = Column(String(100), nullable=False, index=True)
    city = Column(String(100), nullable=False, index=True)
    festival_name = Column(String(100), nullable=True)      # Next festival within 30 days
    festival_date = Column(DateTime, nullable=True)
    days_to_festival = Column(Integer, nullable=True)
    season = Column(String(100), nullable=True)             # wedding season, exam season, monsoon, etc.
    trend_direction = Column(String(20), nullable=True)     # RISING, STABLE, FALLING
    trend_context = Column(String(255), nullable=True)      # Human-readable trend summary
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CategoryPlaybook(Base):
    """
    Pre-seeded industry knowledge per category.
    Provides Gemini with category-specific context for campaign generation.
    """
    __tablename__ = "category_playbooks"

    id = Column(String(10), primary_key=True, default=generate_uuid)
    category = Column(String(100), nullable=False, unique=True, index=True)
    system_context = Column(Text, nullable=False)    # Injected into Gemini system prompt
    typical_cta = Column(String(50), nullable=True)  # Default CTA type for this category
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Lead(Base):
    __tablename__ = "leads"

    lead_no = Column(Integer, primary_key=True, autoincrement=True)
    id = Column(String(10), unique=True, index=True, default=generate_uuid)
    campaign_id = Column(String(10), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    buyer_id = Column(String(10), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(100), nullable=False)
    phone = Column(String(20), nullable=False)
    message = Column(Text, nullable=True)
    label = Column(String(20), default="NEW") # NEW, HOT, WARM, COLD
    notes = Column(Text, nullable=True)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    campaign = relationship("Campaign", back_populates="leads")
    buyer = relationship("User", back_populates="leads", foreign_keys=[buyer_id])
    ratings = relationship("Rating", back_populates="lead")


class CampaignView(Base):
    __tablename__ = "campaign_views"

    view_no = Column(Integer, primary_key=True, autoincrement=True)
    id = Column(String(10), unique=True, index=True, default=generate_uuid)
    campaign_id = Column(String(10), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    viewer_id = Column(String(10), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    campaign = relationship("Campaign", back_populates="views")
    viewer = relationship("User", back_populates="views")


class SavedCampaign(Base):
    __tablename__ = "saved_campaigns"

    save_no = Column(Integer, primary_key=True, autoincrement=True)
    id = Column(String(10), unique=True, index=True, default=generate_uuid)
    buyer_id = Column(String(10), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    campaign_id = Column(String(10), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    saved_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    buyer = relationship("User", back_populates="saved_campaigns")
    campaign = relationship("Campaign", back_populates="saved_by")


class Rating(Base):
    __tablename__ = "ratings"

    id = Column(String(10), primary_key=True, default=generate_uuid)
    business_id = Column(String(10), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    buyer_id = Column(String(10), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    lead_id = Column(String(10), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True)
    score = Column(Integer, nullable=False) # 1 to 5
    review_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    business = relationship("Business", back_populates="ratings")
    buyer = relationship("User", back_populates="ratings")
    lead = relationship("Lead", back_populates="ratings")


class LoginHistory(Base):
    """
    Tracks every successful login per user.
    The login_history table already exists in the DB with columns: login_id, user_id, login_time.
    """
    __tablename__ = "login_history"

    login_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(10), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    login_time = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="login_history")


class ChatThread(Base):
    """
    One chat thread is created per (buyer, campaign) pair when a buyer claims a deal.
    Holds unread counters for both sides so either can know when they have new messages.
    """
    __tablename__ = "chat_threads"

    id = Column(String(10), primary_key=True, default=generate_uuid)
    lead_id = Column(String(10), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, unique=True)
    campaign_id = Column(String(10), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    buyer_id = Column(String(10), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    seller_id = Column(String(10), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    last_message_at = Column(DateTime, default=datetime.utcnow)
    last_buyer_message_at = Column(DateTime, nullable=True)
    last_seller_reply_at = Column(DateTime, nullable=True)
    seller_unread_count = Column(Integer, default=1)  # Starts at 1 — the auto-welcome message counts as unread for seller
    buyer_unread_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    lead = relationship("Lead")
    campaign = relationship("Campaign")
    buyer = relationship("User", back_populates="chat_threads_as_buyer", foreign_keys=[buyer_id])
    seller = relationship("User", back_populates="chat_threads_as_seller", foreign_keys=[seller_id])
    messages = relationship("ChatMessage", back_populates="thread", cascade="all, delete-orphan", order_by="ChatMessage.created_at")


class ChatMessage(Base):
    """
    Individual message inside a ChatThread. is_system=True marks the auto-welcome message
    inserted by the server when the thread is first created.
    """
    __tablename__ = "chat_messages"

    id = Column(String(10), primary_key=True, default=generate_uuid)
    thread_id = Column(String(10), ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False)
    sender_id = Column(String(10), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    sender_role = Column(String(10), nullable=False)   # 'BUYER' | 'SELLER' | 'SYSTEM'
    body = Column(Text, nullable=False)
    is_system = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    thread = relationship("ChatThread", back_populates="messages")
    sender = relationship("User", back_populates="sent_messages", foreign_keys=[sender_id])
