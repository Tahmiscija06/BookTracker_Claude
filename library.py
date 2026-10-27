"""
Veza s internet bibliotekom Open Library (openlibrary.org).

Open Library je besplatna, javna biblioteka s milionima knjiga i naslovnica.
Ne treba nikakav račun ni ključ. Ovdje koristimo samo Pythonove ugrađene
alate (urllib), pa nema dodatnih instalacija.

Ako internet ne radi, funkcije vraćaju None, a aplikacija nastavlja raditi
s manjom ugrađenom listom knjiga.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

SEARCH_URL = "https://openlibrary.org/search.json"
COVER_URL = "https://covers.openlibrary.org/b/id/{}-M.jpg"
COVER_PREFIX = "https://covers.openlibrary.org/"
FIELDS = "key,title,author_name,cover_i,number_of_pages_median,first_publish_year,subject"
USER_AGENT = "BookTracker/2.0 (personal book tracking app)"  # samo ASCII znakovi!

# Pravila: ako se neka od ovih riječi pojavi u temama knjige, dodjeljujemo žanr.
# Redoslijed je bitan (prvo posebniji žanrovi, "Roman" na kraju).
GENRE_RULES = [
    (("science fiction", "sci-fi"), "Naučna fantastika"),
    (("fantasy",), "Fantastika"),
    (("mystery", "thriller", "detective", "crime", "suspense"), "Krimi / triler"),
    (("biograph", "memoir"), "Biografija"),
    (("history",), "Historija"),
    (("psycholog",), "Psihologija"),
    (("poetry", "poems"), "Poezija"),
    (("self-help", "self help", "personal development", "habits"), "Razvoj ličnosti"),
    (("classic",), "Klasik"),
    (("fiction", "novel"), "Roman"),
]

# Koju "temu" tražimo u biblioteci za svaki naš žanr (kad predlažemo knjige).
GENRE_SUBJECT = {
    "Roman": "fiction",
    "Klasik": "classic literature",
    "Fantastika": "fantasy",
    "Naučna fantastika": "science fiction",
    "Krimi / triler": "mystery",
    "Historija": "history",
    "Biografija": "biography",
    "Psihologija": "psychology",
    "Poezija": "poetry",
    "Razvoj ličnosti": "self-help",
}

_cache = {}          # (upit) -> (vrijeme, rezultat); da ne pitamo isto više puta
_down_until = 0.0    # ako internet ne radi, na minut pauziramo pokušaje
last_error = ""     # zadnja greška (prikazuje se uz poruku o vezi)


def _fetch(url, timeout):
    """Jedini dio koji zaista ide na internet (lako se zamijeni u testovima)."""
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def available():
    """False kratko nakon što je internet zakazao."""
    return time.time() >= _down_until


def guess_genre(subjects):
    text = " | ".join(s.lower() for s in subjects if isinstance(s, str))
    for keywords, genre in GENRE_RULES:
        if any(k in text for k in keywords):
            return genre
    return "Ostalo"


def _norm(doc):
    title = (doc.get("title") or "").strip()
    if not title:
        return None
    authors = doc.get("author_name") or []
    cover_i = doc.get("cover_i")
    pages = doc.get("number_of_pages_median")
    if not isinstance(pages, int) or not 1 <= pages <= 20000:
        pages = 0
    return {
        "title": title,
        "author": authors[0] if authors else "Nepoznat autor",
        "cover_url": COVER_URL.format(cover_i) if cover_i else "",
        "pages": pages,
        "year": doc.get("first_publish_year") or "",
        "genre": guess_genre(doc.get("subject") or []),
    }


def ol_search(params, timeout=6, ttl=6 * 3600):
    """Vraća listu knjiga (može biti prazna) ili None ako upit nije uspio."""
    global _down_until, last_error
    key = tuple(sorted((k, str(v)) for k, v in params.items()))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    if not available():
        return None
    url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
    try:
        data = _fetch(url, timeout)
    except urllib.error.HTTPError as e:
        last_error = f"HTTP {e.code}"
        if e.code == 429 or e.code >= 500:
            _down_until = time.time() + 60
        return None
    except ValueError as e:
        last_error = f"neispravan odgovor ({e})"
        return None
    except Exception as e:  # nema interneta, istekao rok, itd.
        last_error = f"{type(e).__name__}: {e}"
        _down_until = time.time() + 60
        return None

    items, seen = [], set()
    for doc in data.get("docs", []):
        item = _norm(doc)
        if not item:
            continue
        k = (item["title"].lower(), item["author"].lower())
        if k in seen:
            continue
        seen.add(k)
        items.append(item)
    if len(_cache) > 500:
        _cache.clear()
    _cache[key] = (time.time(), items)
    return items


def search_books(query, limit=24):
    """Pretraga po naslovu/autoru. Vraća listu ili None (nema interneta)."""
    return ol_search({"q": query, "limit": limit, "fields": FIELDS})


def popular(params, limit=20):
    """Najbolje ocijenjene knjige za zadani upit (autor ili tema)."""
    base = dict(params, limit=limit, fields=FIELDS)
    res = ol_search(dict(base, sort="rating"))
    if res is None and available():  # ako sortiranje ne uspije, probamo bez
        res = ol_search(base)
    return res


def find_cover(title, author=""):
    """Pokušava naći naslovnicu za knjigu unesenu ručno. Vraća URL ili ''."""
    params = {"title": title, "limit": 5, "fields": FIELDS}
    if author:
        params["author"] = author
    for item in ol_search(params, timeout=4) or []:
        if item["cover_url"]:
            return item["cover_url"]
    return ""
