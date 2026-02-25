from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_caching import Cache
import requests
from bs4 import BeautifulSoup
import random
import re
from urllib.parse import quote_plus

# --- Configuration ---
api_app = Flask(__name__)
CORS(api_app)

# Caching: 1 hour default, some endpoints override with shorter/longer times
cache = Cache(api_app, config={
    'CACHE_TYPE': 'SimpleCache',
    'CACHE_DEFAULT_TIMEOUT': 3600
})

BASE_URL = "https://hianime.to"
AJAX_BASE = f"{BASE_URL}/ajax"

class ScraperEngine:
    def __init__(self):
        self.session = requests.Session()
        # Rotating modern user agents
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        ]
        self.session.timeout = 10

    def _get_soup(self, url, timeout=10):
        """Fetch and parse HTML with rotating headers"""
        headers = {"User-Agent": random.choice(self.user_agents)}
        try:
            response = self.session.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")
        except Exception as e:
            print(f"Scraping Error at {url}: {e}")
            return None

    def _get_ajax(self, endpoint, params=None):
        """Fetch AJAX JSON endpoint"""
        url = f"{AJAX_BASE}/{endpoint}"
        headers = {
            "User-Agent": random.choice(self.user_agents),
            "X-Requested-With": "XMLHttpRequest"
        }
        try:
            resp = self.session.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("application/json"):
                return resp.json()
            return None
        except:
            return None

    def get_trending(self):
        soup = self._get_soup(f"{BASE_URL}/home")
        if not soup:
            return []

        trending = []
        # Find section by heading text (more reliable than class names)
        trending_section = None
        for h in soup.find_all(['h2', 'h3', 'div'], string=re.compile(r'(?i)trending', re.I)):
            parent = h.find_parent(['section', 'div'])
            if parent:
                trending_section = parent
                break

        if trending_section:
            # Look for links with anime titles (broad match)
            items = trending_section.select('a[href*="/"]')
            for i, item in enumerate(items[:15], 1):  # limit to reasonable number
                title = item.get_text(strip=True)
                if not title or len(title) < 3 or 'episode' in title.lower():
                    continue
                href = item.get('href', '')
                if not href or '/home' in href or href.startswith('#'):
                    continue
                slug = href.strip('/').split('/')[-1]
                if slug and '-' in slug:
                    trending.append({
                        "rank": f"{i:02d}",
                        "title": title,
                        "id": slug
                    })
        return trending

    def get_sidebar_list(self, list_type="top-airing"):
        soup = self._get_soup(f"{BASE_URL}/home")
        if not soup:
            return []

        results = []
        # Normalize to title case for matching
        search_term = list_type.replace('-', ' ').title()

        # Find heading containing the term
        target_heading = soup.find(['h2', 'h3', 'div'], string=re.compile(re.escape(search_term), re.I))
        if target_heading:
            block = target_heading.find_parent(['div', 'section', 'aside'])
            if block:
                items = block.select('a[href*="/"]')
                for item in items:
                    title = item.get_text(strip=True)
                    if not title or len(title) < 3:
                        continue
                    href = item.get('href', '')
                    slug = href.strip('/').split('/')[-1] if href else ""
                    if slug and '-' in slug:
                        # Try to find rank if nearby
                        rank_elem = item.find_previous(['span', 'div'], string=re.compile(r'^\d+$'))
                        rank = rank_elem.get_text(strip=True) if rank_elem else "N/A"
                        results.append({
                            "rank": rank,
                            "title": title,
                            "id": slug
                        })
        return results

    def search(self, keyword, page=1):
        url = f"{BASE_URL}/search?keyword={quote_plus(keyword)}&page={page}"
        soup = self._get_soup(url)
        if not soup:
            return {"items": [], "total_pages": 1, "current_page": page}

        items = []
        # Very broad selector then filter
        candidates = soup.select('a[href*="/"]')
        for el in candidates:
            href = el.get('href', '')
            if not href or '/search' in href or '/home' in href or href.startswith('#'):
                continue
            slug = href.strip('/').split('/')[-1]
            if not slug or '-' not in slug:
                continue

            id_match = re.search(r'-(\d+)$', slug)
            anime_id = id_match.group(1) if id_match else slug

            title = el.get_text(strip=True)
            if not title:
                title_elem = el.select_one('span, div, p')
                title = title_elem.get_text(strip=True) if title_elem else "???"

            poster = None
            img = el.select_one('img')
            if img:
                poster = img.get('data-src') or img.get('src')
                if poster and poster.startswith('//'):
                    poster = 'https:' + poster

            # Try to find type/year nearby
            info_text = ""
            next_sib = el.find_next(['span', 'div', 'small'])
            if next_sib:
                info_text = next_sib.get_text(strip=True)
            typ = info_text.split('-')[0].strip() if info_text else None

            items.append({
                "id": anime_id,
                "slug": slug,
                "title": title,
                "poster": poster,
                "type": typ,
                "year": None  # often not reliably present
            })

        # Pagination
        pagination = soup.find(['nav', 'div'], class_=re.compile(r'pag|page'))
        total_pages = 1
        if pagination:
            last_link = pagination.find('a', string=re.compile(r'\d+'))  # last numeric link
            if last_link and last_link.text.isdigit():
                total_pages = int(last_link.text)

        return {"items": items, "total_pages": total_pages, "current_page": page}

    def get_anime_info(self, anime_id):
        url = f"{BASE_URL}/{anime_id}"
        soup = self._get_soup(url)
        if not soup:
            return None

        # Title - broad search
        title_elem = soup.find(['h1', 'h2'], string=True) or soup.find(['div', 'span'], class_=re.compile(r'(?i)title|name'))
        title = title_elem.get_text(strip=True) if title_elem else "Unknown"

        # Synopsis
        synopsis_elem = soup.find(['div', 'p'], string=re.compile(r'(?i)plot|summary|synopsis|overview|description', re.I)) \
                       or soup.find(['div', 'p'], class_=re.compile(r'(?i)desc|overview|synopsis'))
        synopsis = synopsis_elem.get_text(strip=True) if synopsis_elem else None

        # Poster
        poster_elem = soup.find('img', attrs={"src": re.compile(r'(cover|poster|banner)')}) \
                      or soup.find('img', class_=re.compile(r'(?i)poster|cover'))
        poster = poster_elem.get('src') or poster_elem.get('data-src') if poster_elem else None
        if poster and poster.startswith('//'):
            poster = "https:" + poster

        # Info block - very generic fallback
        info = {}
        possible_rows = soup.find_all(['div', 'li', 'span'], class_=re.compile(r'(?i)info|meta|detail|row|item'))
        for row in possible_rows:
            name = row.find(['span', 'div'], class_=re.compile(r'(?i)name|label|key'))
            value = row.find(['span', 'div', 'a'], class_=re.compile(r'(?i)value|content|text'))
            if name and value:
                key = name.get_text(strip=True).rstrip(':').lower().replace(' ', '_')
                val = value.get_text(strip=True)
                if key == "genres":
                    val = [g.strip() for g in val.split(',') if g.strip()]
                if key and val:
                    info[key] = val

        return {
            "id": anime_id,
            "title": title,
            "synopsis": synopsis,
            "poster": poster,
            **info
        }

    def get_episodes(self, anime_id):
        url = f"{BASE_URL}/{anime_id}"
        soup = self._get_soup(url)
        if not soup:
            return []

        episodes = []
        anime_ajax_id = None

        # Extract anime_id from scripts (this part usually still works)
        script_tags = soup.find_all("script")
        for script in script_tags:
            if script.string and "anime_id" in script.string:
                match = re.search(r'anime_id\s*[:=]\s*["\']?(\d+)', script.string)
                if match:
                    anime_ajax_id = match.group(1)
                    break

        if anime_ajax_id:
            ajax_data = self._get_ajax(f"v2/episode/list/{anime_ajax_id}")
            if ajax_data and "html" in ajax_data and ajax_data["html"]:
                ep_soup = BeautifulSoup(ajax_data["html"], "html.parser")
                episode_links = ep_soup.select('a[data-id], a[data-num], a.episode, a[href*="ep="]')
                for a in episode_links:
                    ep_num = (
                        a.get("data-number")
                        or a.get("data-num")
                        or a.get("data-episode-number")
                        or a.select_one('[class*="num"], .number')
                    )
                    ep_num = ep_num.get_text(strip=True) if hasattr(ep_num, "get_text") else str(ep_num) if ep_num else ""
                    ep_id = a.get("data-id") or a.get("href", "").split("?ep=")[-1].split('#')[0]
                    ep_title = a.get("title") or a.select_one('[class*="title"], .name')
                    ep_title = ep_title.get_text(strip=True) if hasattr(ep_title, "get_text") else ""
                    if ep_num and ep_id:
                        episodes.append({
                            "number": ep_num,
                            "id": ep_id,
                            "title": ep_title or f"Episode {ep_num}"
                        })

        # Fallback: direct scraping if AJAX didn't work
        if not episodes:
            ep_containers = soup.select('[class*="episode"], [class*="ep-"], ul li a[href*="ep="]')
            for el in ep_containers:
                num_elem = el.find(['span', 'div'], class_=re.compile(r'(?i)num|episode|ep-'))
                num = num_elem.get_text(strip=True) if num_elem else el.get_text(strip=True).split()[0]
                ep_id = el.get("data-id") or el.get("href", "").split("?ep=")[-1].split('#')[0]
                title_elem = el.find(['span', 'div'], class_=re.compile(r'(?i)title|name'))
                title = title_elem.get_text(strip=True) if title_elem else ""
                if num and ep_id:
                    episodes.append({
                        "number": num,
                        "id": ep_id,
                        "title": title or f"Episode {num}"
                    })

        # Sort numerically where possible
        try:
            episodes.sort(key=lambda x: float(x["number"]) if re.match(r'^\d+(\.\d+)?$', str(x["number"])) else 9999)
        except:
            pass

        return episodes


# Initialize scraper
scraper = ScraperEngine()


# ── Endpoints ────────────────────────────────────────────────────────────────

@api_app.route('/api/discover')
@cache.cached(timeout=43200)  # 12 hours
def api_discover():
    try:
        return jsonify({
            "status": "success",
            "data": {
                "trending": scraper.get_trending(),
                "top_airing": scraper.get_sidebar_list("top-airing"),
                "most_popular": scraper.get_sidebar_list("most-popular"),
                "most-favorite": scraper.get_sidebar_list("most-favorite")
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@api_app.route('/api/search')
@cache.cached(timeout=3600, query_string=True)
def api_search():
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
    try:
        episodes = scraper.get_episodes(anime_id)
        return jsonify({"status": "success", "data": episodes})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    api_app.run(debug=False, host="0.0.0.0", port=5000)
