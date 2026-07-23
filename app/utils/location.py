import os
import requests
from typing import Optional, Dict, Any, List
from app.config import settings


GOOGLE_PLACES_KEY = getattr(settings, 'GOOGLE_PLACES_API_KEY', None)
GOOGLE_MAPS_KEY = getattr(settings, 'GOOGLE_MAPS_API_KEY', None)


def reverse_geocode(lat: float, lng: float) -> Optional[Dict[str, Any]]:
    """Reverse geocode coordinates to a human-readable address using Google Geocoding API."""
    if not GOOGLE_MAPS_KEY:
        return None
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        'latlng': f"{lat},{lng}",
        'key': GOOGLE_MAPS_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        if data.get('status') == 'OK' and data.get('results'):
            primary = data['results'][0]
            return {
                'formatted_address': primary.get('formatted_address'),
                'place_id': primary.get('place_id'),
                'raw': primary,
            }
    except Exception:
        return None
    return None


def get_place_details(place_id: str) -> Optional[Dict[str, Any]]:
    """Fetch place details (address, lat/lng) from Place Details API."""
    if not GOOGLE_PLACES_KEY:
        return None
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        'place_id': place_id,
        'key': GOOGLE_PLACES_KEY,
        'fields': 'place_id,formatted_address,geometry'
    }
    try:
        resp = requests.get(url, params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        if data.get('status') == 'OK' and data.get('result'):
            res = data['result']
            geom = res.get('geometry', {}).get('location', {})
            return {
                'formatted_address': res.get('formatted_address'),
                'place_id': res.get('place_id'),
                'latitude': geom.get('lat'),
                'longitude': geom.get('lng'),
                'raw': res,
            }
    except Exception:
        return None
    return None


def places_autocomplete(input_text: str, sessiontoken: Optional[str] = None, location: Optional[tuple] = None, radius: Optional[int] = None, components: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    """Proxy to Google Places Autocomplete. Returns list of prediction objects.

    - `location` is (lat, lng) to bias results.
    - `radius` biases results within meters.
    - `components` can be used to restrict to country, e.g. 'country:in'.
    """
    if not GOOGLE_PLACES_KEY:
        return None
    url = "https://maps.googleapis.com/maps/api/place/autocomplete/json"
    params = {
        'input': input_text,
        'key': GOOGLE_PLACES_KEY,
    }
    if sessiontoken:
        params['sessiontoken'] = sessiontoken
    if location and len(location) == 2:
        params['location'] = f"{float(location[0])},{float(location[1])}"
    if radius:
        params['radius'] = int(radius)
    if components:
        params['components'] = components

    try:
        resp = requests.get(url, params=params, timeout=6)
        resp.raise_for_status()
        data = resp.json()
        if data.get('status') in ('OK', 'ZERO_RESULTS'):
            return data.get('predictions', [])
    except Exception:
        return None
    return None
