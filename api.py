from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import concurrent.futures 
import os

api_app = Flask(__name__)
# Allow CORS for your firebase domain and localhost
CORS(api_app, resources={r"/api/*": {"origins": ["https://void-streaming.web.app", "http://localhost:5000"]}})

BASE_URL = "https://hianime.to"
ANILIST_API = "https://graphql.anilist.co"

class ScraperEngine:
    def __init__(self):
        self.session = requests.Session()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "x-requested-with": "XMLHttpRequest",
            "Referer": f"{BASE_URL}/home"
        }

    def get_poster_from_anilist(self, title):
        query = '''
        query ($search: String) {
          Media (search: $search, type: ANIME) {
            id
            coverImage { large }
            bannerImage
            averageScore
            startDate { year }
          }
        }
        '''
        try:
            response = self.session.post(ANILIST_API, json={'query': query, 'variables': {'search': title}}, timeout=2)
            data = response.json()
            media = data['data']['Media']
            return {
                "poster": media['coverImage']['large'],
                "banner": media['bannerImage'],
                "score": media['averageScore'],
                "year": media['startDate']['year'],
                "al_id": media['id']
            }
        except:
            return {"poster": "https://via.placeholder.com/400x600?text=No+Poster", "banner": "", "score": "N/A", "year": "N/A", "al_id": None}

    def enrich_with_metadata(self, anime_list):
        def fetch_meta(anime):
            meta = self.get_poster_from_anilist(anime['title'])
            anime.update(meta)
            return anime
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            return list(executor.map(fetch_meta, anime_list))

    def get_schedule(self, date_str=None):
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")
        url = f"{BASE_URL}/ajax/schedule/list?tzOffset=0&date={date_str}"
        try:
            r = self.session.get(url, headers=self.headers)
            soup = BeautifulSoup(r.json().get("html", ""), "html.parser")
            anime_list = []
            for item in soup.select("li"):
                title_elem = item.select_one(".film-name")
                if title_elem and title_elem.find('a'):
                    link = title_elem.find('a')['href']
                    anime_list.append({
                        "title": title_elem.text.strip(),
                        "id": link.split("-")[-1].split("?")[0],
                        "full_id": link.split("/")[-1].split("?")[0],
                        "time": item.select_one(".time").text.strip() if item.select_one(".time") else ""
                    })
            return self.enrich_with_metadata(anime_list[:15])
        except: return []

    def search_anime(self, keyword):
        url = f"{BASE_URL}/search?keyword={keyword}"
        try:
            r = self.session.get(url, headers=self.headers)
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            for item in soup.select(".flw-item"):
                name_link = item.select_one(".film-name a")
                if name_link:
                    link = name_link['href']
                    results.append({
                        "title": name_link.text.strip(),
                        "id": link.split("-")[-1].split("?")[0],
                        "full_id": link.split("/")[-1].split("?")[0]
                    })
            return self.enrich_with_metadata(results[:10])
        except: return []

    def get_episodes(self, anime_id):
        try:
            # Extract numeric ID if a full slug was passed
            numeric_id = anime_id.split("-")[-1] if "-" in str(anime_id) else anime_id
            url = f"{BASE_URL}/ajax/v2/episode/list/{numeric_id}"
            
            r = self.session.get(url, headers=self.headers)
            soup = BeautifulSoup(r.json().get("html", ""), "html.parser")
            eps = []
            for e in soup.select(".ep-item"):
                eps.append({
                    "number": e.get('data-number'), 
                    "id": e.get('data-id'), # Critical for MegaPlay
                    "title": e.get('title')
                })
            return eps
        except Exception as e:
            print(f"Error: {e}")
            return []

scraper = ScraperEngine()

@api_app.route('/')
def home(): return "Void API is Running."

@api_app.route('/api/schedule')
def api_schedule(): return jsonify({"status": "success", "data": scraper.get_schedule()})

@api_app.route('/api/search')
def api_search():
    query = request.args.get('q')
    return jsonify({"status": "success", "data": scraper.search_anime(query)}) if query else jsonify({"status": "error"})

@api_app.route('/api/episodes/<anime_id>')
def api_episodes(anime_id):
    return jsonify({"status": "success", "episodes": scraper.get_episodes(anime_id)})

if __name__ == "__main__":
    api_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))