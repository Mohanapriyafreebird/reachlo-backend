"""
AI Campaign Generation Router — REACHLO Backend

Endpoints:
  POST /ai/generate                    — full generation pipeline (text + image)
  POST /ai/publish/{draft_id}          — publish reviewed draft as live campaign
  POST /ai/regenerate-image/{draft_id} — regenerate only the image, text unchanged
  POST /ai/discard/{draft_id}          — discard a draft
  GET  /ai/drafts/{draft_id}           — fetch a draft by ID

Pipeline architecture (single Gemini call):
  1. Read business profile from DB
  2. Load cached business analysis (businesses.ai_business_analysis)
     → If not cached yet, run analyze_business() and store it now
  3. Read market signals (pre-computed, read-only at request time)
  4. Read competitor campaign titles
  5. ONE Gemini call → returns marketing_strategy, buyer_psychology, creative_brief,
     campaign content (title/description/offer/cta), and image_prompt
  6. Programmatic prompt composer → build_flux_prompt() appends quality anchors
  7. FLUX image generation
  8. Store draft (including full pipeline JSON for auditability)

Naming convention:
  draft.campaign_description  → stored as campaigns.description when published
  business.business_description → what the business provides (feeds AI, NOT the campaign copy)
"""

import json
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Business, Campaign, AICampaignDraft, MarketSignal, CategoryPlaybook
from app.schemas import AIGenerateRequest, AIPublishRequest, AICampaignDraftResponse
from app.dependencies import get_current_user
from app.utils.ai_generation import (
    analyze_business,
    generate_full_campaign,
    build_flux_prompt,
    build_ideogram_prompt,
    run_hallucination_guard,
    generate_campaign_image,
    compose_campaign_ad_thumbnail,
    review_campaign_image,
    FLUX_NEGATIVE_PROMPT,
)
from app.models import generate_uuid
from app.config import settings

router = APIRouter(prefix="/ai", tags=["AI Campaign Generation"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require_seller(current_user: User):
    if current_user.role != "SELLER":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only sellers can use AI campaign generation.",
        )


def _get_business_or_403(db: Session, current_user: User) -> Business:
    business = db.query(Business).filter(Business.user_id == current_user.id).first()
    if not business:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business profile not found. Please complete your seller profile.",
        )
    return business


def _get_draft_or_404(db: Session, draft_id: str, business_id: str) -> AICampaignDraft:
    draft = db.query(AICampaignDraft).filter(
        AICampaignDraft.id == draft_id,
        AICampaignDraft.business_id == business_id,
    ).first()
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Draft not found or does not belong to your account.",
        )
    return draft


def _image_url_from_path(filepath: str) -> str:
    """Convert a local file path to a URL-accessible path."""
    rel = filepath.replace("\\", "/")
    if not rel.startswith("/"):
        rel = "/" + rel
    return rel  # served by FastAPI static mount at /uploads


def _get_or_refresh_business_analysis(business: Business, db: Session) -> dict:
    """
    Return the cached business analysis if available.
    If not cached (new seller or business_description changed), run analyze_business()
    and persist the result to the DB before returning.

    This is the ONLY place analyze_business() is called — keeping API usage minimal.
    """
    if business.ai_business_analysis:
        try:
            return json.loads(business.ai_business_analysis)
        except (json.JSONDecodeError, TypeError):
            pass  # corrupted cache — regenerate below

    # Cache miss: run analysis and store
    try:
        analysis = analyze_business(
            business_name=business.name,
            business_description=business.business_description or "",
            usp=business.usp or "",
            category=business.category,
            city=business.city,
            website_url=business.website_url,
        )
        business.ai_business_analysis = json.dumps(analysis)
        db.commit()
        db.refresh(business)
        return analysis
    except Exception as e:
        print(f"[WARN] Business analysis generation failed: {e}")
        return {}  # graceful fallback — pipeline continues without cached analysis


# ---------------------------------------------------------------------------
# POST /ai/generate — full generation pipeline
# ---------------------------------------------------------------------------

@router.post("/generate", response_model=AICampaignDraftResponse, status_code=status.HTTP_201_CREATED)
def generate_ai_campaign(
    request: AIGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    business = _get_business_or_403(db, current_user)
    _require_seller(current_user)

    # Step 1 — Load cached business analysis
    cached_analysis = None
    if business.ai_business_analysis:
        try:
            cached_analysis = json.loads(business.ai_business_analysis)
        except (json.JSONDecodeError, TypeError):
            cached_analysis = None

    # Step 2 — Load existing visual style memory (if returning seller)
    existing_visual_style = None
    if business.ai_visual_style_memory:
        try:
            existing_visual_style = json.loads(business.ai_visual_style_memory)
        except (json.JSONDecodeError, TypeError):
            existing_visual_style = None

    # Derive logo color hint from cached analysis visual_identity_hint (no extra API call)
    logo_color = None
    if cached_analysis:
        logo_color = cached_analysis.get("visual_identity_hint") or None

    # Step 3 — Market signal (pre-computed, read-only at request time)
    market_signal = db.query(MarketSignal).filter(
        MarketSignal.category == business.category,
        MarketSignal.city == business.city,
    ).first()

    market_signals_used = {}
    if market_signal:
        market_signals_used = {
            "festival_name": market_signal.festival_name,
            "festival_date": market_signal.festival_date.isoformat() if market_signal.festival_date else None,
            "days_to_festival": market_signal.days_to_festival,
            "season": market_signal.season,
            "trend_direction": market_signal.trend_direction,
            "trend_context": market_signal.trend_context,
        }

    # Step 4 — Competitor titles (same category + target_cities, ACTIVE, not own campaigns)
    competitor_campaigns = []
    if business.city:
        competitor_campaigns = (
            db.query(Campaign)
            .filter(
                Campaign.category == business.category,
                Campaign.target_cities.like(f"%{business.city}%"),
                Campaign.status == "ACTIVE",
                Campaign.business_id != business.id,
            )
            .order_by(Campaign.view_count.desc())
            .limit(3)
            .all()
        )
    competitor_titles = [c.title for c in competitor_campaigns]

    # Step 5 — ONE Gemini call: all reasoning + content + image_prompt
    try:
        generated = generate_full_campaign(
            business_name=business.name,
            business_description=business.business_description,
            usp=business.usp or "",
            category=business.category,
            city=business.city,
            campaign_topic=request.campaign_topic,
            price_or_deal=request.price_or_deal,
            cached_business_analysis=cached_analysis,
            festival_name=market_signals_used.get("festival_name"),
            days_to_festival=market_signals_used.get("days_to_festival"),
            season=market_signals_used.get("season"),
            trend_direction=market_signals_used.get("trend_direction"),
            competitor_titles=competitor_titles,
            sub_category=business.sub_category,
            logo_color=logo_color,
            existing_visual_style=existing_visual_style,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"AI generation failed: {str(e)}",
        )

    # Extract campaign content from structured response
    campaign_content = generated.get("campaign", {})

    # Extract structured visual pipeline fields for prompt composition
    brand_identity = generated.get("brand_identity", {})
    visual_story = generated.get("visual_story", {})
    photography = generated.get("photography", {})
    poster_copy = generated.get("poster_copy", {})

    # NEW: ad_creative_design — the per-seller background gradient + composition
    # This is the key differentiator between two sellers in the same category
    ad_creative_design = generated.get("ad_creative_design", {})

    print(f"[INFO] Ad creative background: {ad_creative_design.get('background_gradient', 'N/A')}")
    print(f"[INFO] Subject position: {ad_creative_design.get('subject_position', 'N/A')}")
    print(f"[INFO] Key prop: {visual_story.get('key_prop', 'N/A')}")

    # Step 6 — Hallucination guard (pure Python, no latency)
    seller_inputs = {
        "business_description": business.business_description,
        "usp": business.usp,
        "campaign_topic": request.campaign_topic,
        "price_or_deal": request.price_or_deal,
    }
    warnings = run_hallucination_guard(generated, seller_inputs)

    # Step 7 & 8 — Image generation and Quality Reviewer loop
    # build_flux_prompt: ad_creative_design provides per-seller gradient + composition
    ai_image_prompt = generated.get("image_prompt", "")
    full_flux_prompt = build_flux_prompt(
        ai_image_prompt,
        category=business.category,
        sub_category=business.sub_category,
        ad_creative_design=ad_creative_design,
        visual_story=visual_story,
        photography=photography,
        campaign_title=campaign_content.get("title", ""),
        offer_text=campaign_content.get("offer", ""),
        target_audience=campaign_content.get("target_audience", ""),
    )
    print(f"[INFO] FLUX prompt ({len(full_flux_prompt.split())} words): {full_flux_prompt[:200]}...")

    # Build the Ideogram-native prompt — used for the actual image generation.
    # This is optimised for Ideogram v2's native understanding of photography styles,
    # gradient backgrounds, and composition control (max 400 chars for best quality).
    ideogram_prompt = build_ideogram_prompt(
        ai_image_prompt,
        category=business.category,
        sub_category=business.sub_category,
        ad_creative_design=ad_creative_design,
        visual_story=visual_story,
        campaign_title=campaign_content.get("title", ""),
    )
    print(f"[INFO] Ideogram prompt ({len(ideogram_prompt)} chars): {ideogram_prompt[:200]}...")

    image_url = None

    if image_url is None:
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                # Use ideogram_prompt for generation — Ideogram v2 model
                filepath = generate_campaign_image(ideogram_prompt, negative_prompt=FLUX_NEGATIVE_PROMPT)
                image_url = _image_url_from_path(filepath)
                
                # Run Quality Reviewer
                title = campaign_content.get("title", "")
                review = review_campaign_image(filepath, business.name, business.category, title)
                score = review.get("score", 100)
                
                if score >= 82 or attempt == max_attempts - 1:
                    try:
                        filepath = compose_campaign_ad_thumbnail(
                            filepath,
                            campaign_title=campaign_content.get("title", ""),
                            offer_text=campaign_content.get("offer", ""),
                            business_name=business.name,
                            category=business.category,
                            target_audience=campaign_content.get("target_audience", ""),
                            ad_creative_design=ad_creative_design,
                            poster_copy=poster_copy,
                        )
                        image_url = _image_url_from_path(filepath)
                    except Exception as compose_error:
                        print(f"[WARN] Ad thumbnail composition failed, using raw image: {compose_error}")
                    print(f"[INFO] Image accepted with score={score} on attempt {attempt + 1}")
                    break  # Acceptable quality or out of attempts
                    
                # Retry: build a refined prompt using reviewer feedback
                prompt_adjustment = review.get("prompt_adjustment", "").strip()
                if prompt_adjustment:
                    refined_scene = f"{ai_image_prompt.rstrip('.')}. Adjustment: {prompt_adjustment}"
                    full_flux_prompt = build_flux_prompt(
                        refined_scene,
                        category=business.category,
                        sub_category=business.sub_category,
                        ad_creative_design=ad_creative_design,
                        visual_story=visual_story,
                        photography=photography,
                        campaign_title=campaign_content.get("title", ""),
                        offer_text=campaign_content.get("offer", ""),
                        target_audience=campaign_content.get("target_audience", ""),
                    )
                    print(f"[INFO] Retrying image generation (score={score}). Refined Ideogram prompt built.")
                    ideogram_prompt = build_ideogram_prompt(
                        refined_scene,
                        category=business.category,
                        sub_category=business.sub_category,
                        ad_creative_design=ad_creative_design,
                        visual_story=visual_story,
                        campaign_title=campaign_content.get("title", ""),
                    )
                    
            except Exception as e:
                print(f"[WARN] Image generation attempt {attempt + 1} failed: {e}")
                break  # If generation fails completely, don't loop

    # Build pipeline stages JSON for storage (auditability + frontend layout use)
    pipeline_stages = {
        "marketing_strategy": generated.get("marketing_strategy", {}),
        "buyer_psychology": generated.get("buyer_psychology", {}),
        "brand_identity": generated.get("brand_identity", {}),
        "visual_story": visual_story,
        "ad_creative_design": ad_creative_design,
        "photography": photography,
        "poster_copy": poster_copy,
    }

    # Step 9 — Save draft
    # Auto-fill cta_value for WHATSAPP and CALL from the seller's phone number
    draft_cta_type = campaign_content.get("cta_type")
    draft_cta_value = None
    if draft_cta_type and draft_cta_type.upper() in ("WHATSAPP", "CALL"):
        draft_cta_value = current_user.phone  # pre-fill from user's registered phone

    draft = AICampaignDraft(
        id=generate_uuid(),
        business_id=business.id,
        campaign_topic=request.campaign_topic,
        price_or_deal=request.price_or_deal,
        start_date=request.start_date or datetime.utcnow(),
        end_date=request.end_date or (datetime.utcnow() + timedelta(days=30)),
        title=campaign_content.get("title"),
        campaign_description=campaign_content.get("campaign_description"),
        offer=campaign_content.get("offer"),
        cta_type=draft_cta_type,
        cta_value=draft_cta_value,
        target_audience=campaign_content.get("target_audience"),
        target_cities=request.target_cities,
        location_address=request.location_address,
        latitude=request.latitude,
        longitude=request.longitude,
        image_url=image_url,
        # Store the Ideogram prompt so regeneration also uses it
        image_prompt=ideogram_prompt,
        market_signals_used=json.dumps(market_signals_used) if market_signals_used else None,
        hallucination_warnings=json.dumps(warnings) if warnings else None,
        ai_pipeline_stages=json.dumps(pipeline_stages),
        status="DRAFT",
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)

    # Step 10 — Write visual style memory if this is the seller's first campaign
    # Only lock the style once (on first generation). Subsequent generations read it for consistency.
    if not business.ai_visual_style_memory and brand_identity:
        try:
            style_memory = {
                "palette": brand_identity.get("visual_tone", ""),
                "mood": photography.get("mood", ""),
                "subject_type": visual_story.get("hero_subject_description", "")[:120],  # truncate to avoid bloat
                "style_descriptor": brand_identity.get("visual_tone", ""),
            }
            business.ai_visual_style_memory = json.dumps(style_memory)
            db.commit()
            print(f"[INFO] Visual style memory locked for business {business.id}")
        except Exception as e:
            print(f"[WARN] Failed to write visual style memory: {e}")

    return draft


# ---------------------------------------------------------------------------
# GET /ai/drafts/{draft_id}
# ---------------------------------------------------------------------------

@router.get("/drafts/{draft_id}", response_model=AICampaignDraftResponse)
def get_ai_draft(
    draft_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch an existing AI campaign draft by ID. Used by the Draft Review screen."""
    _require_seller(current_user)
    business = _get_business_or_403(db, current_user)

    draft = db.query(AICampaignDraft).filter(
        AICampaignDraft.id == draft_id,
        AICampaignDraft.business_id == business.id,
    ).first()

    if not draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    return draft


# ---------------------------------------------------------------------------
# POST /ai/publish/{draft_id}
# ---------------------------------------------------------------------------

@router.post("/publish/{draft_id}", status_code=status.HTTP_201_CREATED)
def publish_ai_campaign(
    draft_id: str,
    publish_in: AIPublishRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Publish a reviewed AI campaign draft as a live campaign.
    The seller may have edited any field in the draft review screen.
    campaign_description from draft → stored as campaigns.description.

    Returns: {"campaign_id": str, "message": str}
    """
    _require_seller(current_user)
    business = _get_business_or_403(db, current_user)
    draft = _get_draft_or_404(db, draft_id, business.id)

    if draft.status != "DRAFT":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Draft is already {draft.status} and cannot be published again.",
        )

    # Validate CTA value
    cta_value = publish_in.cta_value or draft.cta_value
    cta_type = publish_in.cta_type or draft.cta_type
    if cta_type in ("WHATSAPP", "CALL", "LINK") and not cta_value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="CTA value is required before publishing (e.g. phone number or website URL).",
        )

    # Validate dates
    start_date = publish_in.start_date or draft.start_date or datetime.utcnow()
    end_date = publish_in.end_date or draft.end_date
    if end_date and end_date <= start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End date must be after start date.",
        )

    # Use seller's final (possibly edited) values; fall back to AI-generated values
    campaign_description = publish_in.campaign_description or draft.campaign_description or ""

    new_campaign = Campaign(
        id=generate_uuid(),
        business_id=business.id,
        title=publish_in.title or draft.title or "Campaign",
        description=campaign_description,
        offer=publish_in.offer or draft.offer or "",
        image_url=publish_in.image_url or draft.image_url,
        cta_type=cta_type,
        cta_value=cta_value,
        category=business.category,
        target_audience=publish_in.target_audience or draft.target_audience,
        target_cities=publish_in.target_cities or draft.target_cities,
        location_address=business.location_address,
        latitude=business.latitude,
        longitude=business.longitude,
        start_date=start_date,
        end_date=end_date,
        price=publish_in.price,          # Seller-set exact price (₹ value)
        status="ACTIVE",
        ai_generated=True,
        is_boosted=False,
        view_count=0,
        lead_count=0,
    )
    db.add(new_campaign)
    db.flush()  # get new_campaign.id before commit

    draft.status = "PUBLISHED"
    draft.campaign_id = new_campaign.id
    db.commit()

    return {"campaign_id": new_campaign.id, "message": "Campaign published successfully!"}


# ---------------------------------------------------------------------------
# POST /ai/regenerate-image/{draft_id}
# ---------------------------------------------------------------------------

@router.post("/regenerate-image/{draft_id}")
def regenerate_draft_image(
    draft_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Regenerate only the campaign thumbnail for an existing draft.
    Text content (title, campaign_description, offer, etc.) is unchanged.
    Uses the stored image_prompt from the original generation.

    The seller clicks 'Regenerate' in the draft review screen — this endpoint
    calls FLUX again with the same (or a slightly varied) prompt to get a fresh image.

    Returns: {"image_url": str}
    """
    _require_seller(current_user)
    business = _get_business_or_403(db, current_user)
    draft = _get_draft_or_404(db, draft_id, business.id)

    if draft.status != "DRAFT":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only regenerate images for drafts in DRAFT status.",
        )

    if not draft.image_prompt:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No image prompt stored for this draft.",
        )

    try:
        poster_copy = {}
        ad_creative_design = {}
        if draft.ai_pipeline_stages:
            try:
                stages = json.loads(draft.ai_pipeline_stages)
                poster_copy = stages.get("poster_copy", {}) or {}
                ad_creative_design = stages.get("ad_creative_design", {}) or {}
            except (json.JSONDecodeError, TypeError):
                poster_copy = {}
                ad_creative_design = {}

        filepath = generate_campaign_image(draft.image_prompt)
        try:
            filepath = compose_campaign_ad_thumbnail(
                filepath,
                campaign_title=draft.title or "",
                offer_text=draft.offer or "",
                business_name=business.name,
                category=business.category,
                target_audience=draft.target_audience or "",
                ad_creative_design=ad_creative_design,
                poster_copy=poster_copy,
            )
        except Exception as compose_error:
            print(f"[WARN] Ad thumbnail composition failed during regeneration, using raw image: {compose_error}")
        new_image_url = _image_url_from_path(filepath)
    except Exception as e:
        error_msg = str(e)
        if "Ideogram API error 401" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ideogram API token is invalid or expired. Please update it in the backend.",
            )
        elif "Ideogram API error 402" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ideogram API account has insufficient balance.",
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Image regeneration failed: {error_msg}",
        )

    draft.image_url = new_image_url
    db.commit()

    return {"image_url": new_image_url}


# ---------------------------------------------------------------------------
# POST /ai/discard/{draft_id}
# ---------------------------------------------------------------------------

@router.post("/discard/{draft_id}")
def discard_draft(
    draft_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark a draft as DISCARDED — used when seller taps 'Discard & start over'."""
    _require_seller(current_user)
    business = _get_business_or_403(db, current_user)
    draft = _get_draft_or_404(db, draft_id, business.id)

    draft.status = "DISCARDED"
    db.commit()
    return {"message": "Draft discarded."}


# ---------------------------------------------------------------------------
# POST /ai/refresh-business-analysis
# ---------------------------------------------------------------------------

@router.post("/refresh-business-analysis")
def refresh_business_analysis(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Force-refresh the cached business analysis for the authenticated seller.
    Called automatically by the businesses router when the seller updates
    business_description or USP. Can also be triggered manually.

    Returns: {"message": str}
    """
    _require_seller(current_user)
    business = _get_business_or_403(db, current_user)

    if not business.business_description:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Please add a business description before refreshing the analysis.",
        )

    # Clear existing cache to force regeneration
    business.ai_business_analysis = None
    db.commit()
    db.refresh(business)

    # Regenerate
    analysis = _get_or_refresh_business_analysis(business, db)

    return {
        "message": "Business analysis refreshed successfully.",
        "analysis_keys": list(analysis.keys()) if analysis else [],
    }
