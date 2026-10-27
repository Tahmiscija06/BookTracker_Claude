"""
Book Tracker - jednostavna web aplikacija za evidenciju pročitanih knjiga.

Tehnologije: Python + Flask (web okvir) + Open Library (internet biblioteka).
Baza:        lokalno SQLite (jedna datoteka), na internetu (Vercel) Postgres.
             Koja se koristi određuje varijabla okruženja DATABASE_URL.
Pokretanje:  python app.py   (zatim otvorite http://127.0.0.1:5000)
"""

import hmac
import os
import re
import secrets
import sqlite3
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlparse

from flask import (Flask, flash, g, redirect, render_template, request,
                   session, url_for)
from markupsafe import Markup, escape
from werkzeug.security import check_password_hash, generate_password_hash

import library
from suggestions import GENRES, SUGGESTIONS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "booktracker.db")
KEY_PATH = os.path.join(BASE_DIR, "secret.key")

# Na Vercelu postoji varijabla VERCEL; tamo se datoteke ne mogu trajno spremati.
IS_VERCEL = bool(os.environ.get("VERCEL"))
CONFIG_ERROR = ""  # ako nešto nije podešeno, stranica to jasno ispiše (umjesto rušenja)


def find_database_url():
    """Traži adresu Postgres baze. Vercel je često dodaje s prefiksom
    (npr. STORAGE_DATABASE_URL), pa tražimo i takva imena."""
    for name in ("DATABASE_URL", "POSTGRES_URL"):
        if os.environ.get(name):
            return os.environ[name]
    for suffix in ("_DATABASE_URL", "_POSTGRES_URL"):
        for name, value in sorted(os.environ.items()):
            if name.endswith(suffix) and value.startswith(("postgres://", "postgresql://")):
                return value
    return ""


def related_env_names():
    """Imena (ne vrijednosti!) varijabli koje liče na podešavanja baze."""
    keys = ("DATABASE", "POSTGRES", "NEON", "STORAGE", "PG", "SECRET")
    return sorted(n for n in os.environ if any(k in n.upper() for k in keys))


DATABASE_URL = find_database_url()
USE_PG = bool(DATABASE_URL)
INTEGRITY_ERRORS = (sqlite3.IntegrityError,)

if USE_PG:
    try:
        import psycopg
        from psycopg.rows import dict_row
        INTEGRITY_ERRORS = (psycopg.IntegrityError,)
    except Exception as exc:
        INTEGRITY_ERRORS = ()
        CONFIG_ERROR = f"Ne mogu učitati dodatak za Postgres (psycopg): {type(exc).__name__}: {exc}"
elif IS_VERCEL:
    CONFIG_ERROR = ("Baza podataka nije povezana. Nijedna varijabla okruženja za Postgres "
                    "(DATABASE_URL, POSTGRES_URL ili s prefiksom, npr. STORAGE_DATABASE_URL) "
                    "nije pronađena.")


def load_secret_key():
    """Tajni ključ potpisuje prijave (kolačiće).
    Na internetu ga zadajemo varijablom SECRET_KEY, a lokalno se pravi jednom
    i pamti u datoteci, pa prijave ostaju važeće i nakon ponovnog pokretanja."""
    global CONFIG_ERROR
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    if IS_VERCEL:
        CONFIG_ERROR = CONFIG_ERROR or ("Varijabla okruženja SECRET_KEY nije postavljena.")
        return secrets.token_hex(32)  # privremeni ključ, aplikacija ionako ne radi
    if not os.path.exists(KEY_PATH):
        with open(KEY_PATH, "w") as f:
            f.write(secrets.token_hex(32))
    with open(KEY_PATH) as f:
        return f.read().strip()


# Vercel poslužuje statičke datoteke samo iz mape "public", pa je koristimo
# i lokalno. Datoteke su dostupne na /style.css itd.
app = Flask(__name__, static_folder="public", static_url_path="")
app.secret_key = load_secret_key()
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 30  # 30 dana
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = IS_VERCEL  # na internetu samo preko HTTPS


# ---------------------------------------------------------------- baza ----

class PgConnection:
    """Mali omotač oko Postgres veze, da se koristi isto kao SQLite veza
    (upiti pišu ? umjesto %s, a redovi se čitaju po imenu kolone)."""

    def __init__(self, url):
        self.conn = psycopg.connect(url, row_factory=dict_row, connect_timeout=10,
                                    prepare_threshold=None)

    def execute(self, sql, params=()):
        return self.conn.execute(sql.replace("?", "%s"), params)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


def get_db():
    if "db" not in g:
        if USE_PG:
            g.db = PgConnection(DATABASE_URL)
        else:
            g.db = sqlite3.connect(DB_PATH)
            g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now():
    """Trenutno vrijeme (UTC) kao tekst, isto za SQLite i Postgres."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def ensure_column(db, table, name, ddl):
    """Dodaje kolonu u staru SQLite bazu ako je nema (da vaše knjige ostanu)."""
    cols = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
    if name not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


PG_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS books (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users (id),
        title TEXT NOT NULL,
        author TEXT NOT NULL,
        genre TEXT NOT NULL DEFAULT 'Ostalo',
        total_pages INTEGER NOT NULL DEFAULT 1,
        current_page INTEGER NOT NULL DEFAULT 0,
        favorite INTEGER NOT NULL DEFAULT 0,
        cover_url TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS books_user_idx ON books (user_id)",
]


def init_db():
    if USE_PG:
        conn = psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=10,
                               prepare_threshold=None)
        try:
            for stmt in PG_SCHEMA:
                conn.execute(stmt)
        finally:
            conn.close()
        return
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            genre TEXT NOT NULL DEFAULT 'Ostalo',
            total_pages INTEGER NOT NULL DEFAULT 1,
            current_page INTEGER NOT NULL DEFAULT 0,
            favorite INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        );
        """
    )
    # Nove kolone (dodane u verziji 2); za starije baze ih dodajemo ovdje.
    ensure_column(db, "books", "cover_url", "TEXT NOT NULL DEFAULT ''")
    ensure_column(db, "books", "updated_at", "TEXT NOT NULL DEFAULT ''")
    db.commit()
    db.close()


_db_ready = False


def ensure_db_ready():
    """Pravi tabele ako ne postoje (jednom po pokretanju)."""
    global _db_ready
    if not _db_ready:
        init_db()
        _db_ready = True


# ---------------------------------------------------- zaštita od CSRF ----
# Svaki obrazac nosi tajni token vezan za vašu prijavu. Tako druga stranica
# ne može "u vaše ime" poslati zahtjev (npr. obrisati knjigu).

def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]


@app.template_global("csrf_input")
def csrf_input():
    return Markup(f'<input type="hidden" name="csrf_token" value="{csrf_token()}">')


@app.before_request
def before_each_request():
    if request.endpoint == "static":
        return None
    if CONFIG_ERROR:
        return problem_page(CONFIG_ERROR, show_env=True)
    try:
        ensure_db_ready()
    except Exception as exc:
        app.logger.exception("Greška pri pripremi baze")
        return problem_page("Ne mogu se spojiti s bazom podataka ("
                            + type(exc).__name__ + "). Provjerite da je baza "
                            "povezana s projektom i pokušajte ponovo; detalji su u "
                            "Logs na Vercelu.", show_env=True)
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not hmac.compare_digest(sent, expected):
            flash("Sesija je istekla ili zahtjev nije valjan. Pokušajte ponovo.", "error")
            return redirect(request.referrer if _is_own_url(request.referrer)
                            else url_for("login"))
    return None


def problem_page(message, show_env=False):
    """Jednostavna stranica s objašnjenjem kad aplikacija nije dobro podešena."""
    names = ""
    if show_env:
        found = related_env_names()
        names = ("<p>Pronađene varijable (samo imena): <code>"
                 + (", ".join(str(escape(n)) for n in found) if found else "nijedna")
                 + "</code></p>")
    html = ("<!doctype html><meta charset='utf-8'><title>Book Tracker</title>"
            "<body style='font-family:system-ui;max-width:640px;margin:40px auto;padding:0 16px'>"
            "<h1>Aplikacija još nije potpuno podešena</h1>"
            f"<p>{escape(message)}</p>{names}"
            "<p>Provjerite Settings → Environment Variables na Vercelu, pa uradite Redeploy.</p>")
    return html, 500


def _is_own_url(url):
    return bool(url) and urlparse(url).netloc == request.host


# ------------------------------------------------------------ pomoćno ----

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.template_filter("hue")
def hue_filter(text):
    """Pretvara naslov u broj (0-359) kojim bojimo knjigu bez naslovnice."""
    return sum(ord(c) for c in text or "") % 360


@app.template_global("add_url")
def add_url(item):
    """Link na formu za dodavanje, unaprijed popunjenu podacima knjige."""
    return url_for("add",
                   title=item.get("title") or None,
                   author=item.get("author") or None,
                   genre=item.get("genre") or None,
                   pages=item.get("pages") or None,
                   cover=item.get("cover_url") or None)


@app.context_processor
def inject_nav_counts():
    """Brojevi u gornjem meniju (Čitam, Pročitane, ...)."""
    if "user_id" not in session:
        return {}
    r = get_db().execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN current_page > 0 AND current_page < total_pages
                            THEN 1 ELSE 0 END), 0) AS reading,
          COALESCE(SUM(CASE WHEN current_page >= total_pages THEN 1 ELSE 0 END), 0) AS finished,
          COALESCE(SUM(CASE WHEN current_page = 0 THEN 1 ELSE 0 END), 0) AS todo,
          COALESCE(SUM(favorite), 0) AS favorites
        FROM books WHERE user_id = ?
        """,
        (session["user_id"],),
    ).fetchone()
    return {"nav": {"reading": r["reading"], "finished": r["finished"],
                    "todo": r["todo"], "favorites": r["favorites"]}}


def decorate(row):
    """Pretvara red iz baze u rječnik i dodaje postotak i status."""
    b = dict(row)
    total = b["total_pages"] or 1
    cur = b["current_page"]
    b["percent"] = max(0, min(100, cur * 100 // total))
    if cur >= total:
        b["status"] = "finished"
    elif cur > 0:
        b["status"] = "reading"
    else:
        b["status"] = "todo"
    return b


def clean_cover(url):
    """Prihvatamo samo naslovnice s Open Library (zbog sigurnosti)."""
    url = (url or "").strip()
    return url if url.startswith(library.COVER_PREFIX) and len(url) < 300 else ""


def safe_back(default_endpoint="index"):
    """Vraća korisnika na stranicu s koje je došao (samo ako je naša)."""
    if _is_own_url(request.referrer):
        return request.referrer
    return url_for(default_endpoint)


def read_book_form(editing):
    """Čita i provjerava podatke iz obrasca. Vraća (podaci, greška)."""
    title = request.form.get("title", "").strip()
    author = request.form.get("author", "").strip()
    genre = request.form.get("genre", "Ostalo")
    if genre not in GENRES:
        genre = "Ostalo"
    try:
        total = int(request.form.get("total_pages", "").strip())
        current = int(request.form.get("current_page", "0").strip() or 0)
    except ValueError:
        return None, "Broj stranica mora biti cijeli broj."
    if not title or not author:
        return None, "Naslov i autor su obavezni."
    if len(title) > 200 or len(author) > 120:
        return None, "Naslov ili ime autora je predugo."
    if total < 1 or total > 20000:
        return None, "Ukupan broj stranica mora biti između 1 i 20000."
    if current < 0 or current > total:
        return None, "Pročitane stranice moraju biti između 0 i ukupnog broja stranica."

    if not editing:  # pri dodavanju status određuje početnu stranicu
        status = request.form.get("status", "want")
        if status == "finished":
            current = total
        elif status == "reading":
            current = max(current, 1)
        else:
            current = 0

    return dict(title=title, author=author, genre=genre, total_pages=total,
                current_page=current,
                cover_url=clean_cover(request.form.get("cover_url"))), None


def get_own_book(book_id):
    return get_db().execute(
        "SELECT * FROM books WHERE id = ? AND user_id = ?",
        (book_id, session["user_id"]),
    ).fetchone()


# --------------------------------------------------- prijava / registracija

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        if not re.fullmatch(r"[a-z0-9_.-]{3,30}", username):
            flash("Korisničko ime: 3 do 30 znakova (slova, brojevi, _ . -).", "error")
        elif len(password) < 6:
            flash("Lozinka mora imati najmanje 6 znakova.", "error")
        elif password != password2:
            flash("Lozinke se ne podudaraju.", "error")
        else:
            db = get_db()
            try:
                db.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    (username, generate_password_hash(password)),
                )
                db.commit()
            except INTEGRITY_ERRORS:
                db.rollback()
                flash("To korisničko ime je već zauzeto.", "error")
            else:
                flash("Račun je napravljen. Sada se možete prijaviti.", "success")
                return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("index"))
        flash("Pogrešno korisničko ime ili lozinka.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Odjavljeni ste.", "success")
    return redirect(url_for("login"))


# ------------------------------------------------------------- knjige ----

# Uslovi (u SQL-u) za svaku od stranica s knjigama.
LIST_FILTERS = {
    "reading": "current_page > 0 AND current_page < total_pages",
    "finished": "current_page >= total_pages",
    "todo": "current_page = 0",
    "favorites": "favorite = 1",
}
# Najnovije promijenjene knjige idu prve.
LIST_ORDER = "ORDER BY COALESCE(NULLIF(updated_at, ''), created_at) DESC, id DESC"


def show_list(kind):
    rows = get_db().execute(
        f"SELECT * FROM books WHERE user_id = ? AND {LIST_FILTERS[kind]} {LIST_ORDER}",
        (session["user_id"],),
    ).fetchall()
    return render_template("books.html", kind=kind,
                           books=[decorate(r) for r in rows])


@app.route("/")
@login_required
def index():
    """Početna: samo knjige koje trenutno čitate."""
    return show_list("reading")


@app.route("/read")
@login_required
def finished():
    return show_list("finished")


@app.route("/want")
@login_required
def want():
    return show_list("todo")


@app.route("/favorites")
@login_required
def favorites():
    return show_list("favorites")


@app.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    mine, online, offline = [], [], False
    if q:
        rows = get_db().execute(
            f"SELECT * FROM books WHERE user_id = ? {LIST_ORDER}",
            (session["user_id"],),
        ).fetchall()
        ql = q.lower()
        all_books = [decorate(r) for r in rows]
        mine = [b for b in all_books
                if ql in b["title"].lower() or ql in b["author"].lower()]
        owned = {b["title"].strip().lower() for b in all_books}
        found = library.search_books(q)
        offline = found is None
        online = found or []
        for item in online:
            item["owned"] = item["title"].lower() in owned
    return render_template("search.html", q=q, mine=mine, online=online,
                           offline=offline, error=library.last_error)


@app.route("/add", methods=["GET", "POST"])
@login_required
def add():
    book = {"title": "", "author": "", "genre": "Ostalo",
            "total_pages": "", "current_page": 0, "cover_url": ""}
    status = "want"
    prefilled = False
    if request.method == "POST":
        data, error = read_book_form(editing=False)
        if error:
            flash(error, "error")
            book = dict(book, **request.form.to_dict())
            status = request.form.get("status", "want")
        else:
            if not data["cover_url"]:
                data["cover_url"] = library.find_cover(data["title"], data["author"])
            db = get_db()
            db.execute(
                "INSERT INTO books (user_id, title, author, genre, total_pages,"
                " current_page, cover_url, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session["user_id"], data["title"], data["author"], data["genre"],
                 data["total_pages"], data["current_page"], data["cover_url"],
                 now(), now()),
            )
            db.commit()
            flash("Knjiga je dodana.", "success")
            return redirect({"finished": url_for("finished"),
                             "reading": url_for("index")}
                            .get(request.form.get("status"), url_for("want")))
    else:  # forma popunjena iz pretrage ili prijedloga
        args = request.args
        if args.get("title"):
            prefilled = True
            genre = args.get("genre", "Ostalo")
            pages = args.get("pages", "")
            book.update(
                title=args.get("title", "")[:200],
                author=args.get("author", "")[:120],
                genre=genre if genre in GENRES else "Ostalo",
                total_pages=pages if pages.isdigit() else "",
                cover_url=clean_cover(args.get("cover")),
            )
    return render_template("form.html", book=book, genres=GENRES, editing=False,
                           status=status, prefilled=prefilled)


@app.route("/edit/<int:book_id>", methods=["GET", "POST"])
@login_required
def edit(book_id):
    row = get_own_book(book_id)
    if row is None:
        flash("Knjiga nije pronađena.", "error")
        return redirect(url_for("index"))
    book = dict(row)
    if request.method == "POST":
        data, error = read_book_form(editing=True)
        if error:
            flash(error, "error")
            book = dict(book, **request.form.to_dict())
        else:
            cover = data["cover_url"] or row["cover_url"]
            if not cover:
                cover = library.find_cover(data["title"], data["author"])
            db = get_db()
            db.execute(
                "UPDATE books SET title=?, author=?, genre=?, total_pages=?,"
                " current_page=?, cover_url=?, updated_at=?"
                " WHERE id=? AND user_id=?",
                (data["title"], data["author"], data["genre"], data["total_pages"],
                 data["current_page"], cover, now(), book_id, session["user_id"]),
            )
            db.commit()
            flash("Promjene su spremljene.", "success")
            return redirect(url_for("index"))
    return render_template("form.html", book=book, genres=GENRES, editing=True,
                           status="want", prefilled=False)


@app.route("/progress/<int:book_id>", methods=["POST"])
@login_required
def progress(book_id):
    """Brzo ažuriranje stranice do koje ste stigli (direktno s kartice)."""
    row = get_own_book(book_id)
    if row is None:
        flash("Knjiga nije pronađena.", "error")
        return redirect(url_for("index"))
    try:
        page = int(request.form.get("current_page", "").strip())
    except ValueError:
        flash("Upišite broj stranice (cijeli broj).", "error")
        return redirect(safe_back())
    page = max(0, min(page, row["total_pages"]))
    db = get_db()
    db.execute("UPDATE books SET current_page=?, updated_at=?"
               " WHERE id=? AND user_id=?", (page, now(), book_id, session["user_id"]))
    db.commit()
    if page >= row["total_pages"]:
        flash(f"Čestitamo! Završili ste „{row['title']}“. Nalazi se u Pročitanim.", "success")
    elif page > 0 and row["current_page"] == 0:
        flash(f"Počeli ste čitati „{row['title']}“. Nalazi se na Početnoj.", "success")
    else:
        flash("Napredak je spremljen.", "success")
    return redirect(safe_back())


@app.route("/finish/<int:book_id>", methods=["POST"])
@login_required
def finish(book_id):
    row = get_own_book(book_id)
    if row is None:
        flash("Knjiga nije pronađena.", "error")
        return redirect(url_for("index"))
    db = get_db()
    db.execute("UPDATE books SET current_page=total_pages,"
               " updated_at=? WHERE id=? AND user_id=?",
               (now(), book_id, session["user_id"]))
    db.commit()
    flash(f"Čestitamo! Završili ste „{row['title']}“. Nalazi se u Pročitanim.", "success")
    return redirect(safe_back())


@app.route("/delete/<int:book_id>", methods=["POST"])
@login_required
def delete(book_id):
    db = get_db()
    db.execute("DELETE FROM books WHERE id = ? AND user_id = ?",
               (book_id, session["user_id"]))
    db.commit()
    flash("Knjiga je obrisana.", "success")
    return redirect(safe_back())


@app.route("/favorite/<int:book_id>", methods=["POST"])
@login_required
def favorite(book_id):
    db = get_db()
    db.execute(
        "UPDATE books SET favorite = 1 - favorite WHERE id = ? AND user_id = ?",
        (book_id, session["user_id"]),
    )
    db.commit()
    return redirect(safe_back())


# --------------------------------------------------------- prijedlozi ----

def build_suggestions(user_id, limit=12):
    """Predlaže knjige prema autorima i žanrovima koje čitate ili ste pročitali.

    Težina svake vaše knjige: pročitana ili je trenutno čitate = 2,
    želite je čitati = 0.5, a omiljena dobija još +3.
    Vraća (lista prijedloga, da_li_je_internet_radio).
    """
    rows = get_db().execute(
        "SELECT title, author, genre, favorite, current_page FROM books"
        " WHERE user_id = ?", (user_id,),
    ).fetchall()
    owned = {r["title"].strip().lower() for r in rows}

    authors, genres = {}, {}  # ključ -> [prikazno ime, rezultat, omiljen]
    for r in rows:
        w = (0.5 if r["current_page"] == 0 else 2) + (3 if r["favorite"] else 0)
        fav = bool(r["favorite"])
        a = r["author"].strip()
        for table, key, label in ((authors, a.lower(), a),
                                  (genres, r["genre"], r["genre"])):
            entry = table.setdefault(key, [label, 0.0, False])
            entry[1] += w
            entry[2] = entry[2] or fav
    genres.pop("Ostalo", None)

    jobs = []
    for label, score, fav in sorted(authors.values(), key=lambda e: -e[1])[:3]:
        why = (f"Volite knjige autora {label}" if fav
               else f"Čitate knjige autora {label}")
        jobs.append((score, why, {"author": label}))
    for label, score, fav in sorted(genres.values(), key=lambda e: -e[1])[:3]:
        subject = library.GENRE_SUBJECT.get(label)
        if subject:
            why = (f"Među omiljenima imate žanr „{label}“" if fav
                   else f"Čitate žanr „{label}“")
            jobs.append((score, why, {"q": f'subject:"{subject}"'}))
    jobs.sort(key=lambda j: -j[0])

    results = []
    reachable = True
    if jobs:
        with ThreadPoolExecutor(max_workers=6) as ex:
            fetched = list(ex.map(lambda j: library.popular(j[2]), jobs))
        reachable = any(f is not None for f in fetched)
        lists = []
        for (score, why, _), items in zip(jobs, fetched):
            items = items or []
            with_cover = [i for i in items if i["cover_url"]]
            lists.append((why, with_cover or items))
        seen = set(owned)
        position = 0
        while len(results) < limit and any(position < len(l) for _, l in lists):
            for why, items in lists:
                if position < len(items) and len(results) < limit:
                    item = items[position]
                    if item["title"].lower() in seen:
                        continue
                    seen.add(item["title"].lower())
                    results.append(dict(item, reason=why))
            position += 1

    if len(results) < limit:  # dopuna iz ugrađene liste (ili cijela, bez interneta)
        taken = owned | {r["title"].lower() for r in results}
        extra = []
        for title, author, genre, pages in SUGGESTIONS:
            if title.lower() in taken:
                continue
            if authors.get(author.lower()):
                score, why = 4, f"Čitate knjige autora {author}"
            elif genre in genres:
                score, why = 2, f"Čitate žanr „{genre}“"
            else:
                score, why = 0, "Popularna knjiga koju vrijedi pročitati"
            extra.append((score, dict(title=title, author=author, genre=genre,
                                      pages=pages, cover_url="", reason=why)))
        extra.sort(key=lambda e: -e[0])
        extra = [e for _, e in extra][:limit - len(results)]
        if library.available():  # naslovnice za ugrađene naslove
            with ThreadPoolExecutor(max_workers=6) as ex:
                covers = list(ex.map(
                    lambda e: library.find_cover(e["title"], e["author"]), extra))
            for e, c in zip(extra, covers):
                e["cover_url"] = c
        results.extend(extra)
    return results, reachable and library.available()


@app.route("/suggestions")
@login_required
def suggestions():
    has_books = get_db().execute(
        "SELECT COUNT(*) AS n FROM books WHERE user_id = ?", (session["user_id"],)
    ).fetchone()["n"] > 0
    items, online = build_suggestions(session["user_id"])
    return render_template("suggestions.html", items=items, has_books=has_books,
                           online=online, error=library.last_error)


# ------------------------------------------------------------- start ----

try:
    if not CONFIG_ERROR:
        ensure_db_ready()
except Exception as exc:  # npr. baza trenutno nedostupna; pokušat ćemo ponovo
    print("Upozorenje: baza nije pripremljena:", exc)

if __name__ == "__main__":
    url = "http://127.0.0.1:5000"
    print(f"\nBook Tracker radi. Otvorite u pregledniku: {url}")
    print("Za zaustavljanje pritisnite Ctrl + C.\n")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
