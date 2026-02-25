from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_caching import Cache
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import requests
from bs4 import BeautifulSoup
import random
import re
from urllib.parse import quote_plus

# --- Configuration & Security ---
api_app = Flask(__name__)
CORS(api_app)

limiter = Limiter(key_func=get_remote_address, default_limits=["100 per minute"])
limiter.init_app(api_app)

# Caching: 1 hour default, but some endpoints use shorter times
cache = Cache(api_app, config={
    'CACHE_TYPE': 'SimpleCache',
    'CACHE_DEFAULT_TIMEOUT': 3600
})

BASE_URL = "https://hianime.to"
AJAX_BASE = f"{BASE_URL}/ajax"

class ScraperEngine:
    def __init__(self):
        self.session = requests.Session()
        # Use a rotating list of modern user agents
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        ]
        # Set a default timeout for all requests
        self.session.timeout = 10

    def _get_soup(self, url, timeout=10):
        """Fetch and parse HTML with rotating headers and timeout"""
        headers = {"User-Agent": random.choice(self.user_agents)}
        try:
            response = self.session.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")
        except Exception as e:
            print(f"Scraping Error at {url}: {e}")
            return None

    def _get_ajax(self, endpoint, params=None):
        """Fetch AJAX JSON endpoint (used for episode lists)"""
        url = f"{AJAX_BASE}/{endpoint}"
        headers = {"User-Agent": random.choice(self.user_agents), "X-Requested-With": "XMLHttpRequest"}
        try:
            resp = self.session.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("application/json"):
                return resp.json()
            return None
        except:
            return None

    # --- Existing methods (trending, sidebar) ---
    def get_trending(self):
        """Scrapes the trending anime from the home page"""
        soup = self._get_soup(f"{BASE_URL}/home")
        if not soup: return []
        
        trending = []
        # Selector may change; this is based on current HiAnime structure
        items = soup.select(".trending .item, #anime-trending .item")
        for item in items:
            rank_elem = item.select_one(".number span")
            title_elem = item.select_one(".film-title")
            link_elem = item.select_one("a")
            if title_elem and link_elem:
                trending.append({
                    "rank": rank_elem.text.strip() if rank_elem else "N/A",
                    "title": title_elem.text.strip(),
                    "id": link_elem['href'].split('/')[-1]
                })
        return trending

    def get_sidebar_list(self, list_type="top-airing"):
        """Scrapes sidebar lists: 'top-airing', 'most-popular', 'most-favorite'"""
        soup = self._get_soup(f"{BASE_URL}/home")
        if not soup: return []

        results = []
        # Find the block containing the list type
        search_term = list_type.replace('-', ' ').lower()
        # Look for a heading that contains the search term
        blocks = soup.select(".block_area-realtime, .block_area-sidebar")
        target_block = None
        for block in blocks:
            header = block.select_one(".main-heading, .block-heading")
            if header and search_term in header.text.lower():
                target_block = block
                break

        if target_block:
            items = target_block.select("ul li")
            for item in items:
                name_elem = item.select_one(".film-name a")
                if name_elem:
                    rank_elem = item.select_one(".number span")
                    results.append({
                        "rank": rank_elem.text.strip() if rank_elem else "N/A",
                        "title": name_elem.text.strip(),
                        "id": name_elem['href'].split('/')[-1]
                    })
        return results

    # --- NEW: Search ---
    def search(self, keyword, page=1):
        """Search anime by keyword"""
        url = f"{BASE_URL}/search?keyword={quote_plus(keyword)}&page={page}"
        soup = self._get_soup(url)
        if not soup:
            return {"items": [], "total_pages": 1, "current_page": page}

        items = []
        # Each result is typically in a div with class "flw-item"
        for el in soup.select(".flw-item"):
            link = el.select_one("a.film-poster")
            if not link:
                continue
            href = link.get("href", "")
            slug = href.strip("/").split("/")[-1] if href else ""
            # Extract anime ID from slug (usually last numeric part)
            id_match = re.search(r'-(\d+)$', slug)
            anime_id = id_match.group(1) if id_match else slug

            title_tag = el.select_one(".film-name a")
            title = title_tag.get_text(strip=True) if title_tag else "???"

            # Optional: extract poster, type, year
            poster_img = link.select_one("img")
            poster = poster_img.get("data-src") or poster_img.get("src") if poster_img else None
            if poster and poster.startswith("//"):
                poster = "https:" + poster

            typ = el.select_one(".fd-infor .type")
            typ = typ.get_text(strip=True) if typ else None

            year = el.select_one(".fd-infor .year")
            year = year.get_text(strip=True) if year else None

            items.append({
                "id": anime_id,
                "slug": slug,
                "title": title,
                "poster": poster,
                "type": typ,
                "year": year
            })

        # Pagination: find number of pages
        pagination = soup.select_one(".pagination")
        total_pages = 1
        if pagination:
            last_page_link = pagination.select("a.page-link")[-2] if len(pagination.select("a.page-link")) > 1 else None
            if last_page_link and last_page_link.text.isdigit():
                total_pages = int(last_page_link.text)

        return {"items": items, "total_pages": total_pages, "current_page": page}

    # --- NEW: Get anime info (details) ---
    def get_anime_info(self, anime_id):
        """Fetch detailed info about an anime using its ID/slug"""
        url = f"{BASE_URL}/{anime_id}"  # anime_id can be slug like 'one-piece-100'
        soup = self._get_soup(url)
        if not soup:
            return None

        # Title
        title_elem = soup.select_one("h2.film-name, .anisc-name")
        title = title_elem.get_text(strip=True) if title_elem else "Unknown"

        # Synopsis
        synopsis_elem = soup.select_one(".film-description .text, .anisc-description .text")
        synopsis = synopsis_elem.get_text(strip=True) if synopsis_elem else None

        # Poster
        poster_elem = soup.select_one(".film-poster img, .anisc-poster img")
        poster = poster_elem.get("src") or poster_elem.get("data-src") if poster_elem else None
        if poster and poster.startswith("//"):
            poster = "https:" + poster

        # Additional info (genres, status, etc.)
        info = {}
        for row in soup.select(".anisc-info .item, .film-info .row"):
            name = row.select_one(".name, .item-head")
            value = row.select_one(".value, a, .item-content")
            if name and value:
                key = name.get_text(strip=True).rstrip(":").lower().replace(" ", "_")
                val = value.get_text(strip=True)
                if key == "genres":
                    # Convert to list
                    val = [g.strip() for g in val.split(",") if g.strip()]
                info[key] = val

        return {
            "id": anime_id,
            "title": title,
            "synopsis": synopsis,
            "poster": poster,
            **info
        }

    # --- NEW: Get episodes for an anime ---
    def get_episodes(self, anime_id):
        """Fetch episode list for given anime ID/slug"""
        url = f"{BASE_URL}/{anime_id}"
        soup = self._get_soup(url)
        if not soup:
            return []

        episodes = []

        # Try to extract anime ID from page for AJAX call
        # Often the page contains a data-id attribute or script with anime ID
        anime_ajax_id = None
        # Look for a meta tag or script
        script_tags = soup.find_all("script")
        for script in script_tags:
            if script.string and "anime_id" in script.string:
                match = re.search(r'anime_id\s*=\s*["\']?(\d+)', script.string)
                if match:
                    anime_ajax_id = match.group(1)
                    break

        # If we found AJAX ID, fetch episode list from AJAX endpoint
        if anime_ajax_id:
            ajax_data = self._get_ajax(f"v2/episode/list/{anime_ajax_id}")
            if ajax_data and "html" in ajax_data:
                ep_soup = BeautifulSoup(ajax_data["html"], "html.parser")
                for a in ep_soup.select("a"):
                    ep_num = a.get("data-number") or a.select_one(".number")
                    ep_num = ep_num.get_text(strip=True) if hasattr(ep_num, "get_text") else str(ep_num) if ep_num else ""
                    ep_id = a.get("data-id") or a.get("href", "").split("?ep=")[-1]
                    ep_title = a.get("title") or a.select_one(".title")
                    ep_title = ep_title.get_text(strip=True) if hasattr(ep_title, "get_text") else str(ep_title) if ep_title else ""
                    if ep_num:
                        episodes.append({
                            "number": ep_num,
                            "id": ep_id,
                            "title": ep_title or f"Episode {ep_num}"
                        })

        # Fallback: try to scrape episode list from static page
        if not episodes:
            ep_items = soup.select("#detail-ss-list .ss-list a, .episode-list .ep-item")
            for el in ep_items:
                num = el.select_one(".number, .ep-num")
                num = num.get_text(strip=True) if num else el.get_text(strip=True)
                ep_id = el.get("data-id") or el.get("href", "").split("?ep=")[-1]
                title = el.select_one(".title, .ep-name")
                title = title.get_text(strip=True) if title else ""
                if num:
                    episodes.append({
                        "number": num,
                        "id": ep_id,
                        "title": title or f"Episode {num}"
                    })

        # Sort by episode number (if numeric)
        try:
            episodes.sort(key=lambda x: float(x["number"]) if x["number"].replace(".", "").isdigit() else 9999)
        except:
            pass

        return episodes


# Initialize the engine
scraper = ScraperEngine()

# --- Endpoints ---

@api_app.route('/api/discover')
@cache.cached(timeout=43200)  # 12 hours
def api_discover():
    """Returns combined lists of trending, top airing, most popular, most favorite"""
    try:
        return jsonify({
            "status": "success",
            "data": {
                "trending": scraper.get_trending(),
                "top_airing": scraper.get_sidebar_list("top-airing"),
                "most_popular": scraper.get_sidebar_list("most-popular"),
                "most_favorite": scraper.get_sidebar_list("most-favorite")
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@api_app.route('/api/search')
@cache.cached(timeout=3600, query_string=True)  # 1 hour per query
def api_search():
    """Search anime by keyword"""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"status": "error", "message": "Missing query parameter 'q'"}), 400
    page = int(request.args.get("page", 1))
    try:
        data = scraper.search(q, page)
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@api_app.route('/api/anime/<path:anime_id>')
@cache.cached(timeout=7200)  # 2 hours
def api_anime_info(anime_id):
    """Get detailed info for an anime (by ID or slug)"""
    try:
        info = scraper.get_anime_info(anime_id)
        if not info:
            return jsonify({"status": "error", "message": "Anime not found"}), 404
        return jsonify({"status": "success", "data": info})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@api_app.route('/api/episodes/<path:anime_id>')
@cache.cached(timeout=3600)  # 1 hour
def api_episodes(anime_id):
    """Get episode list for an anime"""
    try:
        episodes = scraper.get_episodes(anime_id)
        return jsonify({"status": "success", "data": episodes})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@api_app.errorhandler(RateLimitExceeded)
def _handle_rate_limit_exceeded(e):
    return jsonify({"status": "error", "message": "Too many requests. Please slow down."}), 429

if __name__ == "__main__":
    api_app.run(debug=False, host="0.0.0.0", port=5000)
