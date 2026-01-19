from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import concurrent.futures 
import os
import re
import json
from urllib.parse import urlparse, parse_qs, quote_plus

api_app = Flask(__name__)
CORS(api_app, resources={r"/api/*": {"origins": ["https://void-streaming.web.app", "http://localhost:5000", "http://localhost:8000"]}})

BASE_URL = "https://hianime.to"
ANILIST_API = "https://graphql.anilist.co"

class ScraperEngine:
    def __init__(self):
        self.session = requests.Session()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "x-requested-with": "XMLHttpRequest",
            "Referer": f"{BASE_URL}/home",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
        self.session.headers.update(self.headers)
    
    def extract_ids_from_url(self, url):
        """Extract anime ID and episode ID from URL"""
        parsed_url = urlparse(url)
        path_parts = parsed_url.path.split('/')
        
        anime_id = None
        anime_slug = None
        
        for part in path_parts:
            if part.startswith('watch'):
                continue
            if '-' in part and any(char.isdigit() for char in part):
                anime_slug = part
                match = re.search(r'(\d+)$', part)
                if match:
                    anime_id = match.group(1)
                break
        
        query_params = parse_qs(parsed_url.query)
        episode_id = query_params.get('ep', [None])[0]
        
        return {
            'anime_id': anime_id,
            'anime_slug': anime_slug,
            'episode_id': episode_id,
            'full_url': url
        }
    
    def get_poster_from_anilist(self, title=None, anilist_id=None):
        """Get anime metadata from AniList"""
        if anilist_id:
            query = '''
            query ($id: Int) {
              Media(id: $id, type: ANIME) {
                id
                title {
                  romaji
                  english
                  userPreferred
                }
                description
                averageScore
                startDate { year month day }
                endDate { year month day }
                genres
                status
                episodes
                duration
                format
                coverImage { large extraLarge }
                bannerImage
                season
                seasonYear
                studios { nodes { name } }
              }
            }
            '''
            variables = {'id': int(anilist_id)}
        elif title:
            query = '''
            query ($search: String) {
              Media(search: $search, type: ANIME) {
                id
                title {
                  romaji
                  english
                  userPreferred
                }
                description
                averageScore
                startDate { year month day }
                endDate { year month day }
                genres
                status
                episodes
                duration
                format
                coverImage { large extraLarge }
                bannerImage
                season
                seasonYear
                studios { nodes { name } }
              }
            }
            '''
            variables = {'search': title}
        else:
            return None
        
        try:
            response = self.session.post(ANILIST_API, 
                                        json={'query': query, 'variables': variables}, 
                                        timeout=5)
            data = response.json()
            if 'data' in data and data['data']['Media']:
                media = data['data']['Media']
                return {
                    "id": media['id'],
                    "title": {
                        "romaji": media['title']['romaji'],
                        "english": media['title']['english'],
                        "userPreferred": media['title']['userPreferred']
                    },
                    "description": media['description'],
                    "averageScore": media['averageScore'],
                    "startDate": media['startDate'],
                    "endDate": media['endDate'],
                    "genres": media['genres'],
                    "status": media['status'],
                    "episodes": media['episodes'],
                    "duration": media['duration'],
                    "format": media['format'],
                    "coverImage": media['coverImage'],
                    "bannerImage": media['bannerImage'],
                    "season": media['season'],
                    "seasonYear": media['seasonYear'],
                    "studios": media['studios']['nodes'] if media['studios'] else []
                }
        except Exception as e:
            print(f"AniList error: {e}")
        return None

    def enrich_with_metadata(self, anime_list):
        """Enrich anime list with AniList metadata"""
        def fetch_meta(anime):
            meta = self.get_poster_from_anilist(anime['title'])
            if meta:
                anime.update({
                    "poster": meta['coverImage']['large'] if meta['coverImage'] else "https://via.placeholder.com/400x600?text=No+Poster",
                    "banner": meta['bannerImage'] or "",
                    "score": meta['averageScore'] or "N/A",
                    "year": meta['startDate']['year'] if meta['startDate'] else "N/A",
                    "al_id": meta['id'],
                    "genres": meta['genres'][:3] if meta.get('genres') else []
                })
            else:
                anime.update({
                    "poster": "https://via.placeholder.com/400x600?text=No+Poster",
                    "banner": "",
                    "score": "N/A",
                    "year": "N/A",
                    "al_id": None,
                    "genres": []
                })
            return anime
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            return list(executor.map(fetch_meta, anime_list))

    def get_schedule(self, date_str=None):
        """Get daily anime schedule"""
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
        except Exception as e:
            print(f"Schedule error: {e}")
            return []

    def search_anime(self, keyword, limit=15):
        """Search for anime"""
        url = f"{BASE_URL}/search?keyword={quote_plus(keyword)}"
        try:
            r = self.session.get(url, headers=self.headers)
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            
            # Try different selectors for search results
            items = soup.select(".flw-item")
            if not items:
                items = soup.select(".film_list-wrap")
            
            for item in items[:limit]:
                name_link = item.select_one(".film-name a")
                if not name_link:
                    name_link = item.select_one("h3 a") or item.select_one("h2 a")
                
                if name_link:
                    link = name_link['href']
                    title = name_link.text.strip()
                    
                    # Extract ID from URL
                    anime_id = None
                    slug_match = re.search(r'-(\d+)(?:\?|$)', link)
                    if slug_match:
                        anime_id = slug_match.group(1)
                    
                    if not anime_id:
                        # Try to extract from full slug
                        slug_parts = link.split('/')
                        for part in slug_parts:
                            if '-' in part and any(char.isdigit() for char in part):
                                match = re.search(r'(\d+)$', part)
                                if match:
                                    anime_id = match.group(1)
                                    break
                    
                    results.append({
                        "title": title,
                        "id": anime_id or "unknown",
                        "full_id": link.split("/")[-1].split("?")[0]
                    })
            
            return self.enrich_with_metadata(results)
        except Exception as e:
            print(f"Search error: {e}")
            return []

    def get_episodes(self, anime_id):
        """Get episodes for an anime"""
        try:
            # Extract numeric ID if a full slug was passed
            numeric_id = anime_id.split("-")[-1] if "-" in str(anime_id) else anime_id
            url = f"{BASE_URL}/ajax/v2/episode/list/{numeric_id}"
            
            r = self.session.get(url, headers=self.headers)
            soup = BeautifulSoup(r.json().get("html", ""), "html.parser")
            eps = []
            
            episode_items = soup.select(".ep-item")
            if not episode_items:
                episode_items = soup.select(".ss-item")
            
            for e in episode_items:
                episode_id = e.get('data-id') or e.get('href')
                if not episode_id and 'href' in e.attrs:
                    # Extract from href
                    ep_match = re.search(r'ep=(\d+)', e['href'])
                    episode_id = ep_match.group(1) if ep_match else None
                
                episode_number = e.get('data-number')
                if not episode_number:
                    # Try to extract from text
                    num_match = re.search(r'(\d+)', e.text)
                    episode_number = num_match.group(1) if num_match else "1"
                
                eps.append({
                    "number": int(episode_number) if episode_number.isdigit() else 1,
                    "id": episode_id or f"ep_{len(eps)+1}",
                    "title": e.get('title') or f"Episode {episode_number}"
                })
            
            # Sort by episode number
            eps.sort(key=lambda x: x['number'])
            return eps
        except Exception as e:
            print(f"Episodes error: {e}")
            return []

    def get_anime_details(self, anime_id):
        """Get detailed anime information"""
        try:
            # Extract numeric ID
            numeric_id = anime_id.split("-")[-1] if "-" in str(anime_id) else anime_id
            url = f"{BASE_URL}/watch/{anime_id}" if "-" in str(anime_id) else f"{BASE_URL}/watch/{numeric_id}"
            
            r = self.session.get(url, headers=self.headers)
            soup = BeautifulSoup(r.text, "html.parser")
            
            details = {}
            
            # Extract title
            title_elem = soup.find('h2', class_='film-name') or soup.find('h1', class_='film-name')
            details['title'] = title_elem.text.strip() if title_elem else "Unknown"
            
            # Extract description
            desc_elem = soup.find('div', class_='film-description')
            details['description'] = desc_elem.text.strip() if desc_elem else ""
            
            # Extract details
            info_items = soup.find_all('div', class_='item')
            for item in info_items:
                title_elem = item.find('div', class_='item-title')
                value_elem = item.find('div', class_='item-list')
                if title_elem and value_elem:
                    key = title_elem.text.strip().lower().replace(' ', '_').replace(':', '')
                    values = [v.text.strip() for v in value_elem.find_all('a')]
                    details[key] = values if len(values) > 1 else values[0] if values else None
            
            # Extract poster
            poster_elem = soup.find('img', class_='film-poster-img')
            details['poster'] = poster_elem['src'] if poster_elem and 'src' in poster_elem.attrs else None
            
            return details
        except Exception as e:
            print(f"Anime details error: {e}")
            return {"title": "Unknown", "description": "", "poster": None}

    def get_seasons(self, anime_title):
        """Get all seasons/franchise of an anime"""
        try:
            # Clean title to get base title
            base_title = re.sub(r'(?i)(season\s+\d+|part\s+\d+|\d+(?:st|nd|rd|th)\s+season|cour\s+\d+)', '', anime_title).strip()
            
            # Search for base title
            search_results = self.search_anime(base_title, limit=20)
            
            # Filter results that are likely to be part of the franchise
            franchise = []
            for result in search_results:
                result_title = result['title'].lower()
                if base_title.lower() in result_title or result_title in base_title.lower():
                    franchise.append({
                        'id': result['id'],
                        'title': result['title'],
                        'poster': result.get('poster', ''),
                        'score': result.get('score', 'N/A'),
                        'year': result.get('year', 'N/A')
                    })
            
            # Sort by title (natural sort for seasons)
            franchise.sort(key=lambda x: [
                int(c) if c.isdigit() else c.lower() 
                for c in re.split(r'(\d+)', x['title'])
            ])
            
            return franchise[:10]  # Limit to 10 seasons
        except Exception as e:
            print(f"Seasons error: {e}")
            return []

    def get_recommendations(self, anime_id, genre=None, limit=12):
        """Get anime recommendations"""
        try:
            # First try to get recommendations from the anime page
            numeric_id = anime_id.split("-")[-1] if "-" in str(anime_id) else anime_id
            url = f"{BASE_URL}/watch/{anime_id}" if "-" in str(anime_id) else f"{BASE_URL}/watch/{numeric_id}"
            
            r = self.session.get(url, headers=self.headers)
            soup = BeautifulSoup(r.text, "html.parser")
            
            recommendations = []
            
            # Look for recommendations section
            rec_section = soup.find('div', class_='block_area')
            if rec_section and ('Recommendations' in rec_section.text or 'You might also like' in rec_section.text):
                rec_items = rec_section.find_all('div', class_='film_list-wrap')
                if not rec_items:
                    rec_items = rec_section.find_all('div', class_='flw-item')
                
                for item in rec_items[:limit]:
                    link = item.find('a', href=re.compile(r'/watch/'))
                    if link and link.get('href'):
                        href = link['href']
                        anime_id_match = re.search(r'-(\d+)(?:\?|$)', href)
                        if anime_id_match:
                            title_elem = item.find('h3', class_='film-name') or link
                            recommendations.append({
                                'id': anime_id_match.group(1),
                                'title': title_elem.text.strip() if title_elem else 'Unknown',
                                'url': href
                            })
            
            # If no recommendations found on page, use AniList genre-based recommendations
            if not recommendations and genre:
                # Fallback to AniList API for recommendations
                query = '''
                query ($genre: String, $perPage: Int) {
                  Page(page: 1, perPage: $perPage) {
                    media(genre: $genre, type: ANIME, sort: POPULARITY_DESC) {
                      id
                      title {
                        userPreferred
                        english
                      }
                      coverImage {
                        large
                        extraLarge
                      }
                      averageScore
                      format
                    }
                  }
                }
                '''
                variables = {
                    'genre': genre,
                    'perPage': limit
                }
                
                try:
                    response = self.session.post(ANILIST_API, 
                                                json={'query': query, 'variables': variables}, 
                                                timeout=5)
                    data = response.json()
                    if 'data' in data:
                        for media in data['data']['Page']['media']:
                            recommendations.append({
                                'id': media['id'],
                                'title': media['title']['userPreferred'] or media['title']['english'],
                                'poster': media['coverImage']['large'] if media['coverImage'] else None,
                                'score': media['averageScore'],
                                'format': media['format']
                            })
                except:
                    pass
            
            return recommendations[:limit]
        except Exception as e:
            print(f"Recommendations error: {e}")
            return []

    def get_anime_by_id(self, anime_id):
        """Get anime information by ID"""
        try:
            # Try to get from hianime
            url = f"{BASE_URL}/watch/{anime_id}" if "-" in anime_id else None
            
            if not url:
                # Search for anime by ID
                search_url = f"{BASE_URL}/ajax/search?keyword={anime_id}"
                r = self.session.get(search_url, headers=self.headers)
                data = r.json()
                
                if data.get('html'):
                    soup = BeautifulSoup(data['html'], 'html.parser')
                    first_result = soup.find('a', href=re.compile(r'/watch/'))
                    if first_result:
                        url = BASE_URL + first_result['href']
            
            if url:
                r = self.session.get(url, headers=self.headers)
                soup = BeautifulSoup(r.text, "html.parser")
                
                # Extract title
                title_elem = soup.find('h2', class_='film-name') or soup.find('h1', class_='film-name')
                title = title_elem.text.strip() if title_elem else "Unknown"
                
                # Get AniList metadata
                metadata = self.get_poster_from_anilist(title)
                
                if metadata:
                    return metadata
                
                # Fallback to basic info
                return {
                    "id": anime_id,
                    "title": {"userPreferred": title, "english": title, "romaji": title},
                    "description": "",
                    "coverImage": {"large": "", "extraLarge": ""},
                    "bannerImage": "",
                    "averageScore": None,
                    "genres": [],
                    "startDate": {"year": None}
                }
            
            return None
        except Exception as e:
            print(f"Get anime by ID error: {e}")
            return None

scraper = ScraperEngine()

@api_app.route('/')
def home(): 
    return jsonify({
        "status": "success",
        "message": "Void Anime API",
        "endpoints": {
            "/api/schedule": "Get daily schedule",
            "/api/search?q=<query>": "Search anime",
            "/api/episodes/<anime_id>": "Get episodes",
            "/api/seasons?title=<title>": "Get anime seasons/franchise",
            "/api/recommendations/<anime_id>": "Get recommendations",
            "/api/anime/<anime_id>": "Get anime details by ID",
            "/api/anilist/<anilist_id>": "Get AniList metadata"
        }
    })

@api_app.route('/api/schedule')
def api_schedule(): 
    return jsonify({
        "status": "success", 
        "data": scraper.get_schedule()
    })

@api_app.route('/api/search')
def api_search():
    query = request.args.get('q')
    if query:
        return jsonify({
            "status": "success", 
            "data": scraper.search_anime(query)
        })
    return jsonify({"status": "error", "message": "Query parameter 'q' is required"})

@api_app.route('/api/episodes/<anime_id>')
def api_episodes(anime_id):
    episodes = scraper.get_episodes(anime_id)
    return jsonify({
        "status": "success", 
        "episodes": episodes,
        "count": len(episodes)
    })

@api_app.route('/api/seasons')
def api_seasons():
    title = request.args.get('title')
    anime_id = request.args.get('id')
    
    if not title and anime_id:
        # Try to get title from anime ID
        anime_info = scraper.get_anime_by_id(anime_id)
        if anime_info:
            title = anime_info['title']['userPreferred']
    
    if title:
        seasons = scraper.get_seasons(title)
        return jsonify({
            "status": "success",
            "data": seasons,
            "count": len(seasons)
        })
    
    return jsonify({
        "status": "error", 
        "message": "Title or ID parameter is required"
    })

@api_app.route('/api/recommendations/<anime_id>')
def api_recommendations(anime_id):
    genre = request.args.get('genre')
    limit = request.args.get('limit', 12, type=int)
    
    recommendations = scraper.get_recommendations(anime_id, genre, limit)
    return jsonify({
        "status": "success",
        "data": recommendations,
        "count": len(recommendations)
    })

@api_app.route('/api/anime/<anime_id>')
def api_anime(anime_id):
    anime_info = scraper.get_anime_by_id(anime_id)
    if anime_info:
        return jsonify({
            "status": "success",
            "data": anime_info
        })
    return jsonify({
        "status": "error",
        "message": "Anime not found"
    })

@api_app.route('/api/anilist/<anilist_id>')
def api_anilist(anilist_id):
    metadata = scraper.get_poster_from_anilist(anilist_id=anilist_id)
    if metadata:
        return jsonify({
            "status": "success",
            "data": metadata
        })
    return jsonify({
        "status": "error",
        "message": "AniList metadata not found"
    })

@api_app.route('/api/franchise')
def api_franchise():
    """Combined endpoint for frontend franchise building"""
    title = request.args.get('title')
    base_title = request.args.get('base_title')
    
    if not title and base_title:
        title = base_title
    
    if title:
        # Clean title to remove season indicators
        clean_title = re.sub(r'(?i)(season\s+\d+|part\s+\d+|\d+(?:st|nd|rd|th)\s+season|cour\s+\d+)', '', title).strip()
        
        # Search for franchise
        results = scraper.search_anime(clean_title, limit=20)
        
        # Filter and sort
        franchise = []
        for result in results:
            result_title = result['title'].lower()
            if clean_title.lower() in result_title or result_title in clean_title.lower():
                franchise.append({
                    'id': result['id'],
                    'title': result['title'],
                    'poster': result.get('poster', ''),
                    'score': result.get('score', 'N/A'),
                    'year': result.get('year', 'N/A'),
                    'al_id': result.get('al_id')
                })
        
        # Natural sort
        franchise.sort(key=lambda x: [
            int(c) if c.isdigit() else c.lower() 
            for c in re.split(r'(\d+)', x['title'])
        ])
        
        return jsonify({
            "status": "success",
            "data": franchise[:10]
        })
    
    return jsonify({
        "status": "error",
        "message": "Title parameter is required"
    })

if __name__ == "__main__":
    api_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)), debug=True)
