import json
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
import math
import time
import threading
from app.utils.location import get_place_details, reverse_geocode, places_autocomplete as places_autocomplete_util
from app.config import settings
import json as _json

# Optional Redis client
_redis_client = None
try:
    if settings.REDIS_URL:
        try:
            import redis as _redis_pkg
            _redis_client = _redis_pkg.from_url(settings.REDIS_URL, decode_responses=True)
        except Exception:
            _redis_client = None
except Exception:
    _redis_client = None
from app.database import get_db
from app.models import User, Business, Campaign, CampaignView
from app.schemas import CampaignCreate, CampaignUpdate, CampaignResponse
from app.dependencies import get_current_user, get_optional_current_user

router = APIRouter(prefix="/campaigns", tags=["Campaigns"])

# Simple in-memory rate limiter and cache for Places Autocomplete
_rate_limit_store: dict = {}
_rate_limit_lock = threading.Lock()
_RATE_LIMIT = 30  # requests
_RATE_WINDOW = 60  # seconds

_autocomplete_cache: dict = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 30  # seconds


def _parse_stored_image_urls(campaign: Campaign) -> list[str]:
    urls: list[str] = []
    if campaign.image_urls:
        try:
            parsed = json.loads(campaign.image_urls)
            if isinstance(parsed, list):
                urls = [u for u in parsed if u]
        except json.JSONDecodeError:
            pass
    if not urls and campaign.image_url:
        urls = [campaign.image_url]
    return urls[:5]


def _normalize_image_urls(image_url: Optional[str], image_urls: Optional[list[str]]) -> tuple[Optional[str], Optional[str], list[str]]:
    urls = [u for u in (image_urls or []) if u]
    if image_url and image_url not in urls:
        urls.insert(0, image_url)
    urls = urls[:5]
    primary = urls[0] if urls else image_url
    stored = json.dumps(urls) if urls else None
    return primary, stored, urls


def _enrich_campaign_response(campaign: Campaign, business: Optional[Business] = None) -> CampaignResponse:
    urls = _parse_stored_image_urls(campaign)
    biz = business or campaign.business

    loc_address = campaign.location_address
    lat = campaign.latitude
    lng = campaign.longitude
    place_id = campaign.google_place_id

    # If a Google Place ID exists, prefer authoritative place details.
    if place_id:
        details = get_place_details(place_id)
        if details:
            loc_address = details.get('formatted_address') or loc_address
            lat = details.get('latitude') or lat
            lng = details.get('longitude') or lng
            place_id = details.get('place_id') or place_id

    # If coordinates exist but no address, reverse geocode them.
    if (lat is not None and lng is not None) and not loc_address:
        rg = reverse_geocode(lat, lng)
        if rg:
            loc_address = rg.get('formatted_address')
            if not place_id:
                place_id = rg.get('place_id')

    # Reject null/zero coordinates.
    if lat is not None and lng is not None:
        try:
            if float(lat) == 0.0 and float(lng) == 0.0:
                lat = None
                lng = None
        except Exception:
            lat = None
            lng = None

    res = CampaignResponse(
        id=campaign.id,
        business_id=campaign.business_id,
        title=campaign.title,
        description=campaign.description,
        offer=campaign.offer,
        image_url=urls[0] if urls else campaign.image_url,
        image_urls=urls,
        cta_type=campaign.cta_type,
        cta_value=campaign.cta_value,
        category=campaign.category,
        target_audience=campaign.target_audience,
        price=campaign.price,
        start_date=campaign.start_date,
        end_date=campaign.end_date,
        status=campaign.status,
        is_boosted=campaign.is_boosted,
        boost_until=campaign.boost_until,
        view_count=campaign.view_count,
        lead_count=campaign.lead_count,
        created_at=campaign.created_at,
        location_address=loc_address,
        latitude=lat,
        longitude=lng,
        google_place_id=place_id,
        business_name=biz.name if biz else "Unknown Business",
        business_verified=bool(biz.verified) if biz else False,
    )
    return res



@router.get("/nearby")
def get_nearby_campaigns(
    latitude: float,
    longitude: float,
    radius_km: float = 10.0,
    max_results: int = 20,
    db: Session = Depends(get_db),
):
    """Return active campaigns that have GPS coordinates, sorted by distance (km).

    Parameters:
    - latitude, longitude: buyer coordinates
    - radius_km: search radius in kilometers (default 10, max 10)
    - max_results: maximum number of results to return
    """
    from sqlalchemy import func

    # Enforce a buyer-facing maximum radius of 10 km for nearby campaigns.
    radius_km = min(max(float(radius_km), 0.1), 10.0)

    # Haversine formula — clamp acos argument between -1 and 1 to avoid math domain errors
    haversine_arg = (
        func.cos(func.radians(latitude)) *
        func.cos(func.radians(Campaign.latitude)) *
        func.cos(func.radians(Campaign.longitude) - func.radians(longitude)) +
        func.sin(func.radians(latitude)) *
        func.sin(func.radians(Campaign.latitude))
    )
    # Clamp value to valid acos domain [-1, 1] to prevent NaN
    clamped = func.least(func.greatest(haversine_arg, -1.0), 1.0)
    distance_expr = 6371.0 * func.acos(clamped)

    try:
        # Fetch active, non-expired campaigns within the radius, ordered by distance
        results = (
            db.query(Campaign, distance_expr.label("distance"))
            .join(Business)
            .filter(
                Campaign.status == "ACTIVE",
                Campaign.latitude.isnot(None),
                Campaign.longitude.isnot(None),
                Campaign.image_url.isnot(None),
                distance_expr <= radius_km,
                (Campaign.end_date.is_(None)) | (Campaign.end_date >= datetime.utcnow())
            )
            .order_by(distance_expr.asc())
            .limit(max_results)
            .all()
        )
    except Exception as e:
        print(f"[nearby] DB query error: {e}")
        return []

    out = []
    for camp, dist in results:
        try:
            dist_val = float(dist) if dist is not None else 0.0
            # Debugging log: print buyer vs campaign coordinates and calculated distance
            print(f"Buyer: {latitude},{longitude} | Campaign: {camp.latitude},{camp.longitude} | Distance: {dist_val:.2f} KM")
            enriched = _enrich_campaign_response(camp)
            obj = enriched.dict()
            obj['distance_km'] = round(dist_val, 3)
            out.append(obj)
        except Exception as e:
            print(f"[nearby] Error processing campaign {getattr(camp, 'id', '?')}: {e}")

    return out



@router.get("/places/autocomplete")
def places_autocomplete(
    request: Request,
    input: str = Query(..., min_length=1),
    sessiontoken: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    radius: Optional[int] = Query(None, description="Bias results within this radius in meters"),
):
    """Proxy endpoint for Google Places Autocomplete. Returns predictions as provided by Google."""
    if not settings.GOOGLE_PLACES_API_KEY:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google Places API key not configured on server.")
    # Rate limiting by client IP
    client_ip = "unknown"
    try:
        client_ip = request.client.host or "unknown"
    except Exception:
        pass

    now = time.time()
    # First try Redis-backed rate limiting (atomic)
    if _redis_client:
        try:
            rl_key = f"rl:{client_ip}"
            cnt = _redis_client.incr(rl_key)
            if cnt == 1:
                _redis_client.expire(rl_key, _RATE_WINDOW)
            if int(cnt) > _RATE_LIMIT:
                raise HTTPException(status_code=429, detail="Rate limit exceeded for Places Autocomplete")
        except HTTPException:
            raise
        except Exception:
            # fallback to in-memory
            with _rate_limit_lock:
                entry = _rate_limit_store.get(client_ip)
                if not entry or now - entry.get('start', 0) > _RATE_WINDOW:
                    _rate_limit_store[client_ip] = {'start': now, 'count': 1}
                else:
                    if entry.get('count', 0) >= _RATE_LIMIT:
                        raise HTTPException(status_code=429, detail="Rate limit exceeded for Places Autocomplete")
                    entry['count'] = entry.get('count', 0) + 1
    else:
        with _rate_limit_lock:
            entry = _rate_limit_store.get(client_ip)
            if not entry or now - entry.get('start', 0) > _RATE_WINDOW:
                _rate_limit_store[client_ip] = {'start': now, 'count': 1}
            else:
                if entry.get('count', 0) >= _RATE_LIMIT:
                    raise HTTPException(status_code=429, detail="Rate limit exceeded for Places Autocomplete")
                entry['count'] = entry.get('count', 0) + 1

    loc = None
    if latitude is not None and longitude is not None:
        loc = (latitude, longitude)

    # Build cache key
    cache_key = f"pa:{input}|{sessiontoken or ''}|{latitude or ''}|{longitude or ''}|{radius or ''}"

    # Try Redis cache first
    if _redis_client:
        try:
            cached_raw = _redis_client.get(cache_key)
            if cached_raw:
                try:
                    return _json.loads(cached_raw)
                except Exception:
                    pass
        except Exception:
            pass

    # Fallback to in-memory cache
    with _cache_lock:
        cached = _autocomplete_cache.get(cache_key)
        if cached and now - cached.get('ts', 0) < _CACHE_TTL:
            return cached.get('value')

    preds = places_autocomplete_util(input, sessiontoken=sessiontoken, location=loc, radius=radius)
    if preds is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch predictions from Google Places API")

    # Store in Redis if available
    if _redis_client:
        try:
            _redis_client.setex(cache_key, _CACHE_TTL, _json.dumps(preds))
        except Exception:
            pass

    # Also update in-memory cache as fallback
    with _cache_lock:
        _autocomplete_cache[cache_key] = {'ts': now, 'value': preds}

    return preds


@router.get("/places/details")
def places_details(place_id: str = Query(..., min_length=1)):
    """Return Place Details for a given Google Place ID (proxy to Place Details API)."""
    if not settings.GOOGLE_PLACES_API_KEY:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google Places API key not configured on server.")

    details = get_place_details(place_id)
    if details is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch place details from Google Places API")
    return details


@router.get("", response_model=List[CampaignResponse])
def get_campaigns(
    category: Optional[str] = None,
    city: Optional[str] = None,
    seller_mode: bool = False,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user)
):
    query = db.query(Campaign).join(Business)

    if seller_mode:
        if not current_user or current_user.role != "SELLER":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only sellers can view campaigns in seller mode."
            )
        business = db.query(Business).filter(Business.user_id == current_user.id).first()
        if not business:
            return []

        query = query.filter(
            Campaign.business_id == business.id
        )
    else:
        query = query.filter(
            Campaign.status == "ACTIVE",
            Campaign.image_url.isnot(None),
            Campaign.image_url != "",
            (Campaign.end_date.is_(None)) | (Campaign.end_date >= datetime.utcnow())
        )

        if category and category != "All":
            query = query.filter(Campaign.category == category)
        # city filter removed to show all live campaigns irrespective of buyer's city

    campaigns = query.order_by(Campaign.is_boosted.desc(), Campaign.created_at.desc()).all()

    return [_enrich_campaign_response(c) for c in campaigns]


@router.post("/filter-active", response_model=List[str])
def filter_active_campaigns(
    campaign_ids: List[str],
    db: Session = Depends(get_db)
):
    """Takes a list of campaign IDs and returns only those that are still ACTIVE and not expired."""
    active_ids = db.query(Campaign.id).filter(
        Campaign.id.in_(campaign_ids),
        Campaign.status == "ACTIVE",
        (Campaign.end_date.is_(None)) | (Campaign.end_date >= datetime.utcnow())
    ).all()
    return [r[0] for r in active_ids]


@router.post("", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
def create_campaign(
    campaign_in: CampaignCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role != "SELLER":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only sellers can create campaigns."
        )

    business = db.query(Business).filter(Business.user_id == current_user.id).first()
    if not business:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Seller business profile not found."
        )

    primary_url, stored_urls, _ = _normalize_image_urls(campaign_in.image_url, campaign_in.image_urls)

    new_campaign = Campaign(
        business_id=business.id,
        title=campaign_in.title,
        description=campaign_in.description,
        offer=campaign_in.offer,
        image_url=primary_url,
        image_urls=stored_urls,
        cta_type=campaign_in.cta_type or "WhatsApp",
        cta_value=campaign_in.cta_value or business.whatsapp_number or current_user.phone,
        category=campaign_in.category or business.category,
        target_audience=campaign_in.target_audience,
        price=campaign_in.price,
        start_date=campaign_in.start_date.replace(tzinfo=None) if campaign_in.start_date else datetime.utcnow(),
        end_date=campaign_in.end_date.replace(tzinfo=None) if campaign_in.end_date else None,
        target_cities=campaign_in.target_cities,
        # optional location fields
        location_address=getattr(campaign_in, 'location_address', None),
        latitude=getattr(campaign_in, 'latitude', None),
        longitude=getattr(campaign_in, 'longitude', None),
        status="ACTIVE"
    )

    db.add(new_campaign)
    db.commit()
    db.refresh(new_campaign)

    return _enrich_campaign_response(new_campaign, business)


@router.put("/{id}", response_model=CampaignResponse)
def update_campaign(
    id: str,
    campaign_in: CampaignUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    campaign = db.query(Campaign).filter(Campaign.id == id).first()
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found."
        )

    business = db.query(Business).filter(Business.id == campaign.business_id).first()
    if not business or business.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this campaign's business profile."
        )

    update_data = campaign_in.dict(exclude_unset=True)

    # If location update present, enrich using Google APIs
    if 'google_place_id' in update_data and update_data.get('google_place_id'):
        details = get_place_details(update_data.get('google_place_id'))
        if details:
            update_data['location_address'] = details.get('formatted_address')
            update_data['latitude'] = details.get('latitude')
            update_data['longitude'] = details.get('longitude')

    if ('latitude' in update_data and 'longitude' in update_data) and not update_data.get('location_address'):
        try:
            rg = reverse_geocode(update_data.get('latitude'), update_data.get('longitude'))
            if rg:
                update_data['location_address'] = rg.get('formatted_address')
                if 'google_place_id' not in update_data:
                    update_data['google_place_id'] = rg.get('place_id')
        except Exception:
            pass

    if "image_urls" in update_data or "image_url" in update_data:
        incoming_urls = update_data.pop("image_urls", None)
        incoming_primary = update_data.pop("image_url", campaign.image_url)
        primary_url, stored_urls, _ = _normalize_image_urls(incoming_primary, incoming_urls)
        campaign.image_url = primary_url
        campaign.image_urls = stored_urls

    for field, value in update_data.items():
        if field in ["start_date", "end_date"] and value is not None:
            value = value.replace(tzinfo=None)
        setattr(campaign, field, value)

    db.commit()
    db.refresh(campaign)

    return _enrich_campaign_response(campaign, business)


@router.delete("/{id}", response_model=CampaignResponse)
def delete_campaign(
    id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    campaign = db.query(Campaign).filter(Campaign.id == id).first()
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found."
        )

    business = db.query(Business).filter(Business.id == campaign.business_id).first()
    if not business or business.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this campaign's business profile."
        )

    campaign.status = "DELETED"
    db.commit()
    db.refresh(campaign)

    return _enrich_campaign_response(campaign, business)


@router.post("/{id}/view", response_model=CampaignResponse)
def track_campaign_view(
    id: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user)
):
    campaign = db.query(Campaign).filter(Campaign.id == id).first()
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found."
        )

    if current_user:
        existing_view = db.query(CampaignView).filter(
            CampaignView.campaign_id == campaign.id,
            CampaignView.viewer_id == current_user.id
        ).first()
        if not existing_view:
            campaign.view_count = (campaign.view_count or 0) + 1
            db.add(CampaignView(
                campaign_id=campaign.id,
                viewer_id=current_user.id
            ))

    db.commit()
    db.refresh(campaign)

    business = db.query(Business).filter(Business.id == campaign.business_id).first()
    return _enrich_campaign_response(campaign, business)
