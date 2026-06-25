# -*- coding: utf-8 -*-
"""T10 Lot F — base SQLite `audit_registry` (source unique de config des audits).

Une ligne par audit. Colonnes humaines (onglet/classement/poids/health/actif/
decision/note) editables depuis l'app. Le moteur LIT cette base au lieu des
constantes codees en dur (HEALTH_WEIGHTS / INFO_KEYS / SHEET_GROUPS).

Seed initial = etat post-E2 (avec correction covers_bluesound_oversized -> INFO).
"""
import os
import json
import sqlite3
from datetime import datetime, timezone

# Emplacement persistant (volume monte). Override possible pour les tests.
DB_PATH = os.environ.get("ZIMA_AUDIT_REGISTRY_DB", "/app_data/audit_registry.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_registry (
    audit_key        TEXT PRIMARY KEY,
    libelle          TEXT,
    onglet_cible     TEXT,
    classement_cible TEXT,
    dans_health      INTEGER DEFAULT 0,
    poids_cible      REAL    DEFAULT 0.0,
    actif            INTEGER DEFAULT 1,
    ordre            INTEGER DEFAULT 0,
    decision         TEXT    DEFAULT '',
    note             TEXT    DEFAULT '',
    updated_at       TEXT
);
"""

# --- SEED : etat post-E2. (libelle, audit_key) par groupe, ordre preserve. ---
# Correction T10 Lot F : covers_bluesound_oversized classement probleme -> INFO.
_SEED_GROUPS = [
    ("cockpit", [("🎯 Cockpit", "cockpit")]),
    ("kpi", [
        ("📊 KPI Global", "kpi_dashboard"),
        ("📅 KPI Années", "kpi_years"),
        ("🎵 KPI Genres", "kpi_genres"),
        ("👤 KPI Artistes", "kpi_albumartists"),
    ]),
    ("qualite", [
        ("🎧 Qualité Audio", "quality_analysis"),
        ("🔀 Bitrate mixte/album", "bitrate_mixed_album"),
        ("🔊 Incohér. Samplerate", "samplerate_inconsistency"),
        ("🆔 Version ID3 mixte", "id3_version_inconsistency"),
        ("📀 Homogénéité Codec", "codec_homogeneity"),
        ("⏱️ Durée nulle", "duration_zero"),
    ]),
    ("integrite", [
        ("📦 Albums Incomplets", "incomplete_albums"),
        ("🔢 Trous Numérotation", "track_gaps"),
        ("📝 Écarts Album", "album_gaps"),
        ("📋 Écarts Détaillés", "album_gaps_detailed"),
    ]),
    ("metadonnees", [
        ("🏷️ Tags Manquants", "missing_metadata"),
        ("🔣 Mojibake", "mojibake"),
        ("🚫 Sans Genre", "missing_genre_albums"),
        ("📆 Sans Année", "missing_year_albums"),
        ("⚠️ Années Invalides", "invalid_year_format"),
        ("👥 Cohér. Album Artist", "albumartist_consistency"),
        ("💿 Cohér. Nom Album", "album_name_consistency"),
        ("🎭 Incohér. Genre", "genre_inconsistency"),
        ("✏️ Typo AlbumArtist", "albumartist_typo"),
        ("📂 Dossier ≠ AlbumArtist", "folder_artist_mismatch"),
    ]),
    ("doublons", [
        ("🔍 Doublons MD5", "duplicates_md5"),
        ("🎤 Doublons Titre", "duplicates_artist_title"),
    ]),
    ("casse", [
        ("🔠 Casse AlbumArtist", "case_inconsistency_artist"),
        ("🔡 Casse Albums", "case_inconsistency_album"),
        ("🔤 Casse Genres", "case_inconsistency_genre"),
        ("📋 Casse AlbumArtist-Album", "case_by_artist_album"),
    ]),
    ("images", [
        ("🎨 Covers Non-Uniformes", "cover_non_uniform"),
        ("🚫 Pochettes non-JPG", "covers_non_jpg"),
        ("❌ Pochettes corrompues", "covers_invalid"),
        ("🔍 Pochettes trop petites", "covers_too_small"),
        ("🖼️ Images multiples", "multiple_covers"),
    ]),
    ("donnees", [
        ("📁 Données Complètes", "music_tags"),
        ("🪟 Chemins Windows", "windows_path_issues"),
    ]),
    ("informations", [
        ("👤 Artist ≠ AlbumArtist", "albumartist_vs_artist"),
        ("⚡ Anomalies Bitrate", "bitrate_anomalies"),
        ("🖼️ Taille Pochettes", "cover_size"),
        ("📺 Pochettes > Bluesound", "covers_bluesound_oversized"),
        ("📈 Stats Genres", "genre_stats"),
        ("📅 Incohér. Année", "year_inconsistency"),
    ]),
]

# Poids health (etat post-E2 = post Lot A).
_SEED_WEIGHTS = {
    "duplicates_md5": 3.0, "missing_metadata": 2.5, "incomplete_albums": 2.0,
    "track_gaps": 1.5, "samplerate_inconsistency": 1.0, "invalid_year_format": 1.5,
    "genre_inconsistency": 0.8, "albumartist_consistency": 1.2,
    "missing_genre_albums": 1.0, "missing_year_albums": 1.0,
    "case_inconsistency_artist": 0.5,
    "case_inconsistency_genre": 0.5, "cover_non_uniform": 0.3, "multiple_covers": 0.3,
    "mojibake": 0.8,
    # NB T10 Lot A : case_inconsistency_album mis a 0.0 (absent ici = 0.0 par defaut).
    # poids 0 explicites (Lot A + autres) : non listes = 0.0 par defaut
}

# Classements (etat post-E2 + correction bluesound -> INFO).
_SEED_KPI = {"kpi_dashboard", "kpi_years", "kpi_genres", "kpi_albumartists", "genre_stats"}
_SEED_SKIP = {"music_tags"}
_SEED_INFO = {
    "cover_size", "quality_analysis", "albumartist_vs_artist", "duplicates_artist_title",
    "bitrate_mixed_album", "id3_version_inconsistency", "albumartist_typo",
    "folder_artist_mismatch", "album_name_consistency", "bitrate_anomalies",
    "case_inconsistency_album",
    "covers_bluesound_oversized",  # T10 Lot F: correction probleme -> INFO
}

def _classement(key):
    if key in _SEED_SKIP: return "SKIP"
    if key in _SEED_KPI:  return "KPI"
    if key in _SEED_INFO: return "INFO"
    return "probleme"

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def connect(db_path=None):
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn

def init_and_seed(db_path=None, force=False):
    """Cree la table si absente et la seed si vide (ou force=True). Idempotent."""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        cur = conn.execute("SELECT COUNT(*) AS n FROM audit_registry")
        n = cur.fetchone()["n"]
        if n > 0 and not force:
            return False  # deja seedee
        if force:
            conn.execute("DELETE FROM audit_registry")
        ordre = 0
        ts = _now()
        for group, items in _SEED_GROUPS:
            for libelle, key in items:
                poids = _SEED_WEIGHTS.get(key, 0.0)
                conn.execute(
                    "INSERT OR REPLACE INTO audit_registry "
                    "(audit_key, libelle, onglet_cible, classement_cible, dans_health, "
                    " poids_cible, actif, ordre, decision, note, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (key, libelle, group, _classement(key), 1 if poids > 0 else 0,
                     poids, 1, ordre, "", "", ts),
                )
                ordre += 1
        conn.commit()
        return True
    finally:
        conn.close()

# --- Accesseurs pour le moteur (F2 : remplacent les constantes) ---
def get_all(db_path=None):
    conn = connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM audit_registry ORDER BY ordre").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_health_weights(db_path=None):
    return {r["audit_key"]: r["poids_cible"] for r in get_all(db_path)}

def get_info_keys(db_path=None):
    return {r["audit_key"] for r in get_all(db_path) if r["classement_cible"] == "INFO"}

def get_kpi_keys(db_path=None):
    return {r["audit_key"] for r in get_all(db_path) if r["classement_cible"] == "KPI"}

def get_skip_keys(db_path=None):
    return {r["audit_key"] for r in get_all(db_path) if r["classement_cible"] == "SKIP"}

def get_sheet_groups(db_path=None):
    """Reconstitue SHEET_GROUPS {groupe: [(libelle, key), ...]} dans l'ordre."""
    groups = {}
    for r in get_all(db_path):
        groups.setdefault(r["onglet_cible"], []).append((r["libelle"], r["audit_key"]))
    return groups

def export_json(db_path=None):
    return json.dumps(get_all(db_path), ensure_ascii=False, indent=2)

def update_row(audit_key, fields, db_path=None):
    """Met a jour les champs humains d'une ligne. fields = dict de colonnes."""
    allowed = {"libelle", "onglet_cible", "classement_cible", "dans_health",
               "poids_cible", "actif", "ordre", "decision", "note"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return False
    sets["updated_at"] = _now()
    cols = ", ".join("%s = ?" % k for k in sets)
    vals = list(sets.values()) + [audit_key]
    conn = connect(db_path)
    try:
        conn.execute("UPDATE audit_registry SET %s WHERE audit_key = ?" % cols, vals)
        conn.commit()
        return True
    finally:
        conn.close()
