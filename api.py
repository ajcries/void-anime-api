from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_caching import Cache
from slowapi import Limiter, _rate_limit_exceeded_interpret
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import requests
from bs4 import BeautifulSoup
import concurrent.futures 
import os
import re
import random

# --- Configuration & Security ---
api_app = Flask(__name__)
CORS(api_app)

# Rate Limiter: 100 requests per minute per IP
limiter = Limiter(key_func=get_remote_address, app=api_app, default_limits=["100 per minute"])

# Caching: Persistent for 1 hour by default
cache = Cache(api_app, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 3600})

BASE_URL = "https://hianime.to"

class ScraperEngine:
    def __init__(self):
        self.session = requests.Session()
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/118.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) Chrome/119.0.0.0 Safari/537.36"
        ]

    def _get_soup(self, url):
        """Helper to fetch and parse HTML with rotated headers"""
        headers = {"User-Agent": random.choice(self.user_agents)}
        try:
            response = self.session.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")
        except Exception as e:
            print(f"Scraping Error at {url}: {e}")
            return None

    def get_sidebar_list(self, list_type="top-airing"):
        """
        Scrapes sidebar lists: 'top-airing', 'most-popular', 'most-favorite'
        """
        soup = self._get_soup(f"{BASE_URL}/home")
        if not soup: return []

        results = []
        # Target the specific block based on ID
        selector = f"#anime-featured {list_type if list_type == 'top-airing' else ''}"
        # Note: HiAnime often uses specific IDs for these blocks
        # We'll use a more generic approach to find the headings
        
        blocks = soup.select(".block_area-realtime")
        target_block = None
        
        for block in blocks:
            header = block.select_one(".main-heading")
            if header and list_type.replace('-', ' ').lower() in header.text.lower():
                target_block = block
                break
        
        if target_block:
            items = target_block.select("ul li")
            for item in items:
                name_elem = item.select_one(".film-name a")
                if name_elem:
                    results.append({
                        "title": name_elem.text.strip(),
                        "id": name_elem['href'].split('-')[-1],
                        "rank": item.select_one(".number span").text.strip() if item.select_one(".number") else "N/A"
                    })
        return results

# --- New Endpoints ---

@api_app.route('/api/discover')
@cache.cached(timeout=43200) # Cache for 12 hours
def api_discover():
    """Returns a combined object of all popular/trending lists"""
    return jsonify({
        "status": "success",
        "data": {
            "trending": scraper.get_trending(),
            "top_airing": scraper.get_sidebar_list("top-airing"),
            "most_popular": scraper.get_sidebar_list("most-popular"),
            "most_favorite": scraper.get_sidebar_list("most-favorite")
        }
    })

@api_app.errorhandler(RateLimitExceeded)
def _handle_rate_limit_exceeded(e):
    return jsonify({"status": "error", "message": "Too many requests. Please slow down."}), 429
