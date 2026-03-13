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

BASE_URL = "https://aniwatchtv.to"
AJAX_BASE = f"{BASE_URL}/ajax"


class ScraperEngine:
    def __init__(self):
        self.session = requests.Session()
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ]

    def _headers(self):
        return {
            "User-Agent": random.choice(self.user_agents),
            "Referer": BASE_URL,
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _get_soup(self, url, timeout=12):
        """Fetch and parse HTML with rotating headers."""
        try:
            response = self.session.get(url, headers=self._headers(), timeout=timeout)
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")
        except Exception as e:
            print(f"[ScraperEngine] Error fetching {url}: {e}")
            return None

    def _get_ajax(self, endpoint, params=None):
        """Fetch an AJAX JSON endpoint."""
        url = f"{AJAX_BASE}/{endpoint}"
        headers = {**self._headers(), "X-Requested-With": "XMLHttpRequest"}
        try:
            resp = self.session.get(url, params=params, headers=headers, timeout=12)
            if resp.status_code == 200 and "application/json" in resp.headers.get("content-type", ""):
                return resp.json()
            return None
        except Exception as e:
            print(f"[ScraperEngine] AJAX error at {url}: {e}")
            return None

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_img(tag):
        if not tag:
            return None
        src = tag.get("data-src") or tag.get("src") or ""
        return ("https:" + src) if src.startswith("//") else (src or None)

    @staticmethod
    def _slug_to_id(href):
        """Return the numeric ID appended at the end of a slug, or the slug itself."""
        slug = href.strip("/").split("/")[-1]
        m = re.search(r"-(\d+)$", slug)
        return m.group(1) if m else slug

    # ── Scrapers ─────────────────────────────────────────────────────────────

    def get_trending(self):
        soup = self._get_soup(f"{BASE_URL}/home")
        if not soup:
            return []

        trending = []
        # aniwatchtv uses the same sidebar widget structure as hianime
        # The trending block has items like: <div class="swiper-slide"> … </div>
        # with <a href="/slug"> and a rank <div class="number"><span>01</span></div>
        section = soup.find("div", class_=re.compile(r"(?i)trending"))
        if not section:
            # Broader fallback: find a heading that says "Trending"
            heading = soup.find(string=re.compile(r"(?i)^trending$"))
            section = heading.find_parent(["section", "div"]) if heading else None

        if section:
            for i, item in enumerate(section.select("a[href]"), 1):
                href = item.get("href", "")
                if not href or href in ("/", "#") or "/home" in href:
                    continue
                slug = href.strip("/").split("/")[-1]
                if not slug or "-" not in slug:
                    continue
                title_tag = item.find(["h3", "h4", "div", "span"],
                                      class_=re.compile(r"(?i)title|name"))
                title = title_tag.get_text(strip=True) if title_tag else item.get_text(strip=True)
                if not title or len(title) < 2:
                    continue
                rank_tag = item.find(["span", "div"], class_=re.compile(r"(?i)number|rank"))
                rank = rank_tag.get_text(strip=True) if rank_tag else f"{i:02d}"
                trending.append({"rank": rank, "title": title, "id": slug})
                if len(trending) >= 15:
                    break

        return trending

    def get_sidebar_list(self, list_type="top-airing"):
        """
        Scrape one of the ranked sidebar lists on the home page.
        list_type values: "top-airing" | "most-popular" | "most-favorite"
        """
        soup = self._get_soup(f"{BASE_URL}/home")
        if not soup:
            return []

        results = []
        search_term = list_type.replace("-", " ")

        # Find the tab/heading whose text matches (case-insensitive)
        heading = soup.find(
            string=re.compile(re.escape(search_term), re.I)
        )
        block = heading.find_parent(["div", "section", "aside", "ul"]) if heading else None

        if block:
            for item in block.select("li, div.item, div[class*='item']"):
                link = item.find("a", href=True)
                if not link:
                    continue
                href = link["href"]
                slug = href.strip("/").split("/")[-1]
                if not slug or "-" not in slug:
                    continue
                title_tag = link.find(["h3", "span", "div"], class_=re.compile(r"(?i)title|name")) \
                            or link
                title = title_tag.get_text(strip=True)
                if not title or len(title) < 2:
                    continue
                rank_tag = item.find(string=re.compile(r"^\d+$"))
                rank = rank_tag.strip() if rank_tag else "N/A"
                results.append({"rank": rank, "title": title, "id": slug})

        return results

    def search(self, keyword, page=1):
        url = f"{BASE_URL}/search?keyword={quote_plus(keyword)}&page={page}"
        soup = self._get_soup(url)
        if not soup:
            return {"items": [], "total_pages": 1, "current_page": page}

        items = []
        # aniwatchtv uses .flw-item cards in search results (same as hianime)
        for card in soup.select(".flw-item"):
            link = card.select_one("a.film-poster-ahref, a[href*='/']")
            if not link:
                continue
            href = link.get("href", "")
            slug = href.strip("/").split("/")[-1]
            if not slug or "-" not in slug:
                continue
            anime_id = self._slug_to_id(href)

            title_tag = card.select_one(".film-name, h3.film-name, .film-detail h3")
            title = title_tag.get_text(strip=True) if title_tag else "???"

            img_tag = card.select_one("img")
            poster = self._resolve_img(img_tag)

            type_tag = card.select_one(".fdi-item, .tick-item, .film-infor span")
            anime_type = type_tag.get_text(strip=True) if type_tag else None

            items.append({
                "id": anime_id,
                "slug": slug,
                "title": title,
                "poster": poster,
                "type": anime_type,
            })

        # Pagination: find the last numbered page link
        total_pages = 1
        pag = soup.select_one(".pagination, nav[aria-label*='page'], ul.pagination")
        if pag:
            page_links = pag.select("a[href]")
            for a in reversed(page_links):
                txt = a.get_text(strip=True)
                if txt.isdigit():
                    total_pages = int(txt)
                    break

        return {"items": items, "total_pages": total_pages, "current_page": page}

    def get_anime_info(self, anime_id):
        url = f"{BASE_URL}/{anime_id}"
        soup = self._get_soup(url)
        if not soup:
            return None

        # Title
        title_tag = (
            soup.select_one("h2.film-name, h1.film-name, .anisc-detail h2, .anisc-detail h1")
            or soup.find(["h1", "h2"])
        )
        title = title_tag.get_text(strip=True) if title_tag else "Unknown"

        # Synopsis
        synopsis_tag = soup.select_one(
            ".film-description .text, .anisc-detail .film-description, [class*='description'], [class*='overview']"
        )
        synopsis = synopsis_tag.get_text(strip=True) if synopsis_tag else None

        # Poster
        poster_tag = soup.select_one(
            ".film-poster img, .anisc-poster img, img[src*='cover'], img[src*='poster']"
        )
        poster = self._resolve_img(poster_tag)

        # Info block (genres, status, aired, studios, etc.)
        info = {}
        for row in soup.select(".item-list a, .anisc-info .item"):
            name_tag = row.select_one(".item-head, span.name, .item-title")
            value_tag = row.select_one("span.name, a, .item-value")
            if name_tag and value_tag:
                key = name_tag.get_text(strip=True).rstrip(":").lower().replace(" ", "_")
                val = value_tag.get_text(strip=True)
                info[key] = val
            elif not name_tag:
                # Genre links directly
                pass

        # Genres — explicit selector
        genre_links = soup.select(".anisc-info a[href*='/genre/'], .item-list a[href*='/genre/']")
        if genre_links:
            info["genres"] = [a.get_text(strip=True) for a in genre_links]

        return {
            "id": anime_id,
            "title": title,
            "synopsis": synopsis,
            "poster": poster,
            **info,
        }

    def get_episodes(self, anime_id):
        url = f"{BASE_URL}/{anime_id}"
        soup = self._get_soup(url)
        if not soup:
            return []

        episodes = []
        anime_ajax_id = None

        # Extract the numeric anime_id embedded in the page scripts / data attributes
        # Method 1: data attribute on the episode list container
        ep_container = soup.select_one("#episodes-btn, [data-id], #detail-dp-btn")
        if ep_container:
            anime_ajax_id = ep_container.get("data-id")

        # Method 2: inline script
        if not anime_ajax_id:
            for script in soup.find_all("script"):
                text = script.string or ""
                m = re.search(r'(?:anime_id|animeId)\s*[:=]\s*["\']?(\d+)', text)
                if m:
                    anime_ajax_id = m.group(1)
                    break

        # Method 3: numeric suffix of the slug
        if not anime_ajax_id:
            m = re.search(r"-(\d+)$", anime_id)
            if m:
                anime_ajax_id = m.group(1)

        if anime_ajax_id:
            ajax_data = self._get_ajax(f"v2/episode/list/{anime_ajax_id}")
            if ajax_data and ajax_data.get("html"):
                ep_soup = BeautifulSoup(ajax_data["html"], "html.parser")
                for a in ep_soup.select("a[data-id][data-number], a[href*='?ep=']"):
                    ep_num = a.get("data-number") or a.get("data-num") or ""
                    ep_id = a.get("data-id") or a.get("href", "").split("?ep=")[-1].split("#")[0]
                    title_tag = a.select_one(".ep-name, .title, [class*='title']")
                    ep_title = (title_tag.get_text(strip=True) if title_tag else None) \
                               or a.get("title") or f"Episode {ep_num}"
                    if ep_num and ep_id:
                        episodes.append({
                            "number": ep_num,
                            "id": ep_id,
                            "title": ep_title,
                        })

        # Fallback: direct page scraping
        if not episodes:
            for el in soup.select("[class*='ep-'] a, ul.ep-list li a, a[href*='?ep=']"):
                num_tag = el.select_one("[class*='num'], [class*='number']")
                num = num_tag.get_text(strip=True) if num_tag else ""
                ep_id = el.get("data-id") or el.get("href", "").split("?ep=")[-1].split("#")[0]
                title_tag = el.select_one("[class*='title'], [class*='name']")
                title = title_tag.get_text(strip=True) if title_tag else f"Episode {num}"
                if num and ep_id:
                    episodes.append({"number": num, "id": ep_id, "title": title})

        # Sort numerically
        try:
            episodes.sort(
                key=lambda x: float(x["number"])
                if re.match(r"^\d+(\.\d+)?$", str(x["number"]))
                else 9999
            )
        except Exception:
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
                "trending":      scraper.get_trending(),
                "top_airing":    scraper.get_sidebar_list("top-airing"),
                "most_popular":  scraper.get_sidebar_list("most-popular"),
                "most_favorite": scraper.get_sidebar_list("most-favorite"),
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
