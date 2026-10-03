from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_caching import Cache
import requests
from datetime import datetime

# --- Configuration ---
api_app = Flask(__name__)
CORS(api_app)

cache = Cache(api_app, config={
    'CACHE_TYPE': 'SimpleCache',
    'CACHE_DEFAULT_TIMEOUT': 3600
})

ANILIST_API    = "https://graphql.anilist.co"
MEGAPLAY_BASE  = "https://megaplay.buzz/stream"


# ─────────────────────────────────────────────────────────────────────────────
#  AniList GraphQL client
# ─────────────────────────────────────────────────────────────────────────────

class AniListClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept":       "application/json",
        })

    def query(self, query: str, variables: dict | None = None):
        try:
            r = self.session.post(
                ANILIST_API,
                json={"query": query, "variables": variables or {}},
                timeout=15,
            )
            if r.status_code == 429:
                print("[AniList] rate limited")
                return None
            r.raise_for_status()
            payload = r.json()
            if "errors" in payload:
                print(f"[AniList] errors: {payload['errors']}")
                return None
            return payload.get("data")
        except Exception as e:
            print(f"[AniList] request failed: {e}")
            return None


anilist = AniListClient()


# ─────────────────────────────────────────────────────────────────────────────
#  GraphQL fragments — kept small so we can reuse them across queries
# ─────────────────────────────────────────────────────────────────────────────

MEDIA_CARD = """
  id
  idMal
  title { romaji english native }
  coverImage { large extraLarge color }
  bannerImage
  format
  status
  episodes
  duration
  averageScore
  season
  seasonYear
  genres
  nextAiringEpisode { episode airingAt timeUntilAiring }
"""

MEDIA_FULL = """
  id
  idMal
  title { romaji english native }
  description(asHtml: false)
  coverImage { extraLarge large color }
  bannerImage
  format
  status
  episodes
  duration
  averageScore
  meanScore
  popularity
  favourites
  season
  seasonYear
  startDate { year month day }
  endDate   { year month day }
  genres
  studios(isMain: true) { nodes { id name } }
  trailer { id site }
  nextAiringEpisode { episode airingAt timeUntilAiring }
  streamingEpisodes { title thumbnail url site }
  recommendations(sort: RATING_DESC, perPage: 12) {
    nodes {
      mediaRecommendation {
        id
        idMal
        title { romaji english }
        coverImage { large }
        format
        episodes
        averageScore
      }
    }
  }
"""


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _shape_card(m: dict) -> dict:
    """Normalize an AniList Media node into a flat card object."""
    if not m:
        return {}
    title = m.get("title") or {}
    return {
        "anilist_id": m.get("id"),
        "mal_id":     m.get("idMal"),
        "title":      title.get("english") or title.get("romaji") or title.get("native"),
        "title_romaji":  title.get("romaji"),
        "title_native":  title.get("native"),
        "cover":      (m.get("coverImage") or {}).get("extraLarge")
                      or (m.get("coverImage") or {}).get("large"),
        "banner":     m.get("bannerImage"),
        "color":      (m.get("coverImage") or {}).get("color"),
        "format":     m.get("format"),
        "status":     m.get("status"),
        "episodes":   m.get("episodes"),
        "duration":   m.get("duration"),
        "score":      m.get("averageScore"),
        "season":     m.get("season"),
        "year":       m.get("seasonYear"),
        "genres":     m.get("genres") or [],
        "next_episode": (m.get("nextAiringEpisode") or {}).get("episode"),
        "next_airing_at": (m.get("nextAiringEpisode") or {}).get("airingAt"),
    }


def _available_episode_count(m: dict) -> int:
    """
    AniList gives total `episodes` for finished shows but often `null` for
    currently-airing ones. `nextAiringEpisode.episode - 1` gives the last aired.
    """
    eps = m.get("episodes")
    if eps:
        return int(eps)
    nxt = (m.get("nextAiringEpisode") or {}).get("episode")
    if nxt and nxt > 1:
        return int(nxt) - 1
    return 0


def _embed_url(anilist_id=None, mal_id=None, episode=1, language="sub"):
    if language not in ("sub", "dub"):
        language = "sub"
    if anilist_id:
        return f"{MEGAPLAY_BASE}/ani/{anilist_id}/{episode}/{language}"
    if mal_id:
        return f"{MEGAPLAY_BASE}/mal/{mal_id}/{episode}/{language}"
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@api_app.route("/api/discover")
@cache.cached(timeout=21600)  # 6 hours
def api_discover():
    """Trending / Popular this season / Top rated / Upcoming."""
    try:
        season = None
        month = datetime.utcnow().month
        if   month in (1, 2, 3):    season = "WINTER"
        elif month in (4, 5, 6):    season = "SPRING"
        elif month in (7, 8, 9):    season = "SUMMER"
        else:                       season = "FALL"

        def page(sort, extra=""):
            q = f"""
            query ($page: Int, $sort: [MediaSort], $season: MediaSeason) {{
              Page(page: $page, perPage: 20) {{
                media(type: ANIME, sort: $sort, season: $season, isAdult: false {extra}) {{
                  {MEDIA_CARD}
                }}
              }}
            }}
            """
            vars_ = {"page": 1, "sort": [sort]}
            if "$season" in q:
                vars_["season"] = season
            data = anilist.query(q, vars_)
            return [( _shape_card(m) ) for m in (data or {}).get("Page", {}).get("media", [])]

        return jsonify({
            "status": "success",
            "data": {
                "trending":  page("TRENDING_DESC"),
                "popular":   page("POPULARITY_DESC"),
                "top_rated": page("SCORE_DESC"),
                "upcoming":  page("POPULARITY_DESC", ", status: NOT_YET_RELEASED"),
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@api_app.route("/api/search")
@cache.cached(timeout=1800, query_string=True)
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"status": "error", "message": "Missing 'q'"}), 400

    page_num = max(1, int(request.args.get("page", 1)))
    per_page = min(50, max(1, int(request.args.get("per_page", 20))))

    query = f"""
    query ($search: String, $page: Int, $perPage: Int) {{
      Page(page: $page, perPage: $perPage) {{
        pageInfo {{ total currentPage lastPage hasNextPage }}
        media(type: ANIME, search: $search, isAdult: false, sort: SEARCH_MATCH) {{
          {MEDIA_CARD}
        }}
      }}
    }}
    """
    data = anilist.query(query, {"search": q, "page": page_num, "perPage": per_page})
    if not data:
        return jsonify({"status": "error", "message": "AniList query failed"}), 502

    page_obj = data.get("Page", {})
    return jsonify({
        "status": "success",
        "data": {
            "items":       [_shape_card(m) for m in page_obj.get("media", [])],
            "page_info":   page_obj.get("pageInfo", {}),
            "current_page": page_num,
        }
    })


@api_app.route("/api/anime/<int:anilist_id>")
@cache.cached(timeout=21600)  # 6 hours
def api_anime(anilist_id):
    query = f"""
    query ($id: Int) {{
      Media(id: $id, type: ANIME) {{
        {MEDIA_FULL}
      }}
    }}
    """
    data = anilist.query(query, {"id": anilist_id})
    if not data or not data.get("Media"):
        return jsonify({"status": "error", "message": "Anime not found"}), 404

    m = data["Media"]
    card = _shape_card(m)

    recs = []
    for node in (m.get("recommendations") or {}).get("nodes", []):
        r = node.get("mediaRecommendation")
        if r:
            recs.append(_shape_card(r))

    return jsonify({
        "status": "success",
        "data": {
            **card,
            "description": m.get("description"),
            "mean_score":  m.get("meanScore"),
            "popularity":  m.get("popularity"),
            "favourites":  m.get("favourites"),
            "start_date":  m.get("startDate"),
            "end_date":    m.get("endDate"),
            "studios":     [s["name"] for s in (m.get("studios") or {}).get("nodes", [])],
            "trailer":     m.get("trailer"),
            "streaming":   m.get("streamingEpisodes") or [],
            "recommendations": recs,
            "available_episodes": _available_episode_count(m),
        }
    })


@api_app.route("/api/episodes/<int:anilist_id>")
@cache.cached(timeout=3600)
def api_episodes(anilist_id):
    """
    AniList has no per-episode records, only a total count.
    We synthesize the episode list and pre-build both SUB and DUB embed URLs.
    """
    query = f"""
    query ($id: Int) {{
      Media(id: $id, type: ANIME) {{
        id
        idMal
        title {{ romaji english }}
        episodes
        nextAiringEpisode {{ episode airingAt }}
        streamingEpisodes {{ title thumbnail url site }}
      }}
    }}
    """
    data = anilist.query(query, {"id": anilist_id})
    if not data or not data.get("Media"):
        return jsonify({"status": "error", "message": "Anime not found"}), 404

    m = data["Media"]
    anilist_id_v = m["id"]
    mal_id       = m.get("idMal")
    total        = _available_episode_count(m)

    # AniList's `streamingEpisodes` sometimes carries real episode titles
    title_map = {}
    for se in (m.get("streamingEpisodes") or []):
        t = (se.get("title") or "").strip()
        if t:
            title_map[len(title_map) + 1] = t

    episodes = []
    for n in range(1, total + 1):
        episodes.append({
            "number": n,
            "title":  title_map.get(n) or f"Episode {n}",
            "embed_sub": _embed_url(anilist_id=anilist_id_v, episode=n, language="sub"),
            "embed_dub": _embed_url(anilist_id=anilist_id_v, episode=n, language="dub"),
        })

    return jsonify({
        "status": "success",
        "data": {
            "anilist_id": anilist_id_v,
            "mal_id":     mal_id,
            "total":      total,
            "episodes":   episodes,
        }
    })


@api_app.route("/api/embed")
@cache.cached(timeout=86400, query_string=True)
def api_embed():
    """
    Direct embed URL builder.
    /api/embed?anilist_id=21&episode=1&lang=sub
    /api/embed?mal_id=21&episode=1&lang=dub
    """
    try:
        anilist_id = request.args.get("anilist_id", type=int)
        mal_id     = request.args.get("mal_id", type=int)
        episode    = max(1, request.args.get("episode", default=1, type=int))
        lang       = request.args.get("lang", "sub").lower()

        if not anilist_id and not mal_id:
            return jsonify({"status": "error", "message": "Provide anilist_id or mal_id"}), 400

        url = _embed_url(anilist_id=anilist_id, mal_id=mal_id,
                         episode=episode, language=lang)
        return jsonify({
            "status": "success",
            "data": {
                "embed_url": url,
                "iframe": f'<iframe src="{url}" width="100%" height="100%" '
                          f'frameborder="0" scrolling="no" allowfullscreen></iframe>',
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    api_app.run(debug=False, host="0.0.0.0", port=5000)
