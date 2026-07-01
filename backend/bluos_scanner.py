# -*- coding: utf-8 -*-
"""
================================================================================
 BluOS Artwork Scanner — module backend (ZimaCompare&Tag v16+)
================================================================================
Transformation du script CLI `bluos_artwork_scanner.py` en module backend.
- Volet A (réseau) : scan_network(ip, port, timeout) -> {player, results}
- Volet B (fichiers) : diagnose_library(source_path) -> results

Tous les seuils/params sont lus via bluos_config (Lot 1) :
  get_bluos_param(key, default). Editables dans l'UI.

Règles officielles BluOS : support.bluos.net/hc/en-us/articles/360000368827
  - Ordre pochette dossier : folder.jpg > cover.jpg > folder.png > cover.png,
    sinon image JPEG/PNG intégrée. .bmp jamais reconnu.
  - Externe : "Optimiser" ON -> 600 Ko-4 Mo redim. auto 600x600 ; >=4 Mo non traité.
              "Optimiser" OFF -> < 1200x1200 px ET < 600 Ko.
  - Intégrée : JPEG/PNG uniquement, < 600 Ko, même image sur toutes les pistes.
"""

import base64
import hashlib
import io
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

# ----------------------------------------------------------------------------
# Logger backend (fallback print si import indisponible hors app)
# ----------------------------------------------------------------------------
try:
    from tagaudit.core.logger import get_logger
    _LOG = get_logger("bluos")

    def _default_log(msg):
        _LOG.info(msg)
except Exception:  # pragma: no cover - fallback CLI/tests
    def _default_log(msg):
        print(msg)

# ----------------------------------------------------------------------------
# Accès aux paramètres éditables (Lot 1 : table bluos_config)
# ----------------------------------------------------------------------------
try:
    from tagaudit.core.audit_registry import get_bluos_param
except Exception:  # pragma: no cover
    def get_bluos_param(key, default=None, db_path=None):
        return default

# ----------------------------------------------------------------------------
# Dépendances optionnelles (dégradation gracieuse)
# ----------------------------------------------------------------------------
try:
    import requests
    HAS_REQUESTS = True
except ImportError:  # pragma: no cover
    HAS_REQUESTS = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import mutagen  # noqa: F401
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

# ----------------------------------------------------------------------------
# Constantes fixes (non éditables : liste d'extensions reconnues par BluOS)
# ----------------------------------------------------------------------------
VALID_EXTERNAL_NAMES = {
    "folder.jpg", "folder.jpeg", "cover.jpg", "cover.jpeg",
    "folder.png", "cover.png",
}
AUDIO_EXTENSIONS = {
    ".flac", ".mp3", ".m4a", ".mp4", ".aac", ".alac",
    ".ogg", ".opus", ".wav", ".aiff", ".aif", ".wma",
}


# ----------------------------------------------------------------------------
# Helpers : chargement des paramètres éditables depuis bluos_config
# ----------------------------------------------------------------------------
def _load_params():
    """Lit les seuils BluOS depuis bluos_config (Lot 1) avec casts + défauts."""
    def _int(key, dflt):
        try:
            return int(float(get_bluos_param(key, dflt)))
        except (TypeError, ValueError):
            return dflt
    return {
        "embedded_max_bytes": _int("embedded_max_kb", 600) * 1024,
        "external_autoresize_min": _int("external_autoresize_min_kb", 600) * 1024,
        "external_autoresize_max": _int("external_autoresize_max_kb", 4096) * 1024,
        "external_no_optimize_max_px": _int("external_no_optimize_max_px", 1200),
        "placeholder_max_bytes": _int("placeholder_max_kb", 50) * 1024,
        "placeholder_min_count": _int("placeholder_min_count", 4),
    }


# ============================================================================
# PARTIE 1 — Scan réseau via l'API locale BluOS
# ============================================================================
class BluOSClient:
    """Petit client pour l'API locale BluOS (port 11000, HTTP, XML)."""

    def __init__(self, ip, port=11000, timeout=10):
        self.base = f"http://{ip}:{port}"
        self.timeout = timeout
        self.session = requests.Session()

    def _get(self, path):
        r = self.session.get(f"{self.base}{path}", timeout=self.timeout)
        r.raise_for_status()
        return r

    def check_reachable(self):
        """Vérifie que le lecteur répond bien à /SyncStatus (nom, modèle)."""
        r = self._get("/SyncStatus")
        root = ET.fromstring(r.content)
        return {
            "name": root.get("name", "?"),
            "model": root.get("modelName", "?"),
        }

    def browse(self, key=None):
        path = "/Browse"
        if key:
            path += f"?key={quote(key, safe='')}"
        r = self._get(path)
        return ET.fromstring(r.content)

    def fetch_image(self, image_url):
        if image_url.startswith("http://") or image_url.startswith("https://"):
            url = image_url
        else:
            sep = "&" if "?" in image_url else "?"
            url = f"{self.base}{image_url}{sep}followRedirects=1"
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.content, r.headers.get("Content-Type", "")


def find_albums_browse_key(client, log=_default_log, debug=False):
    """Retrouve l'entrée 'bibliothèque locale' puis la vue 'Albums'."""
    root = client.browse()
    if debug:
        items = root.findall("item")
        log("  [debug] menu racine : {} éléments : ".format(len(items))
            + ", ".join(f"'{it.get('text')}'->{it.get('browseKey')}" for it in items))

    local_key = None
    for item in root.findall("item"):
        bk = item.get("browseKey", "")
        if bk.startswith("LocalMusic"):
            local_key = bk
            break
    if not local_key:
        raise RuntimeError(
            "Impossible de trouver la bibliothèque locale (LocalMusic) sur ce "
            "lecteur. Vérifiez qu'un dossier réseau ou une clé USB est bien "
            "configuré comme source musicale dans BluOS Controller."
        )

    lib = client.browse(local_key)
    lib_items = lib.findall("item")
    if debug:
        log("  [debug] bibliothèque locale : {} éléments : ".format(len(lib_items))
            + ", ".join(f"'{it.get('text')}'->{it.get('browseKey')}" for it in lib_items))

    candidates = []
    for item in lib_items:
        text = (item.get("text") or "").strip().lower()
        bk = item.get("browseKey")
        if bk and "album" in text:
            candidates.append((text == "albums", bk, item.get("text")))
    if not candidates:
        raise RuntimeError(
            "Impossible de trouver la vue 'Albums' de la bibliothèque locale."
        )
    candidates.sort(key=lambda c: not c[0])  # correspondance exacte en premier
    chosen = candidates[0]
    log(f"  Vue albums trouvée : '{chosen[2]}'")
    return chosen[1]


def _summarize_types(items):
    counts = {}
    for it in items:
        t = it.get("type") or "?"
        counts[t] = counts.get(t, 0) + 1
    return ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))


def collect_local_albums(client, albums_key, log=_default_log, debug=False, max_depth=8):
    """Parcourt récursivement la vue Albums de la bibliothèque locale."""
    albums = []
    seen_albums = set()
    visited_keys = set()
    calls = [0]

    def walk(key, depth):
        if depth > max_depth or key in visited_keys:
            return
        visited_keys.add(key)
        page_key = key
        page = 0
        while page_key:
            page += 1
            calls[0] += 1
            browse = client.browse(page_key)
            items = browse.findall("item")

            if debug and depth <= 2:
                log(f"  [debug] profondeur {depth}, clé='{page_key[:60]}', "
                    f"{len(items)} éléments : {_summarize_types(items)}")

            for it in items:
                t = it.get("type")
                bk = it.get("browseKey")
                if t == "album":
                    uid = (it.get("text"), it.get("text2"), it.get("image"))
                    if uid not in seen_albums:
                        seen_albums.add(uid)
                        albums.append({
                            "title": it.get("text") or "(sans titre)",
                            "artist": it.get("text2") or "",
                            "image": it.get("image"),
                        })
                elif t == "track":
                    continue
                elif bk:
                    walk(bk, depth + 1)

            if len(albums) and len(albums) % 100 < 5:
                log(f"  ... {len(albums)} albums trouvés jusqu'ici "
                    f"({calls[0]} requêtes envoyées)")

            page_key = browse.get("nextKey")

    walk(albums_key, 0)
    log(f"  ... terminé : {len(albums)} albums, {calls[0]} requêtes au lecteur.")
    return albums


def analyze_network_artwork(client, albums, params, log=_default_log, stop_event=None,
                            progress_cb=None):
    """Télécharge la pochette de chaque album et repère celles qui posent
    vraiment problème (petite taille + répétée = icône générique BluOS).

    params : dict issu de _load_params() (placeholder_max_bytes, placeholder_min_count).
    stop_event : threading.Event optionnel pour interruption (Lot 3).
    progress_cb : callable(idx, total) optionnel pour publier la progression (Lot 3).
    """
    placeholder_max_bytes = params["placeholder_max_bytes"]
    placeholder_min_count = params["placeholder_min_count"]

    results = []
    small_clusters = {}

    total = len(albums)
    for idx, alb in enumerate(albums, start=1):
        if stop_event is not None and stop_event.is_set():
            log("  ... scan réseau interrompu par l'utilisateur.")
            break
        if idx % 50 == 0 or idx == total:
            log(f"  ... vérification des pochettes {idx}/{total}")
        if progress_cb is not None:
            progress_cb(idx, total)

        entry = {
            "artist": alb["artist"],
            "title": alb["title"],
            "status": "ok",
            "detail": "",
        }
        if not alb["image"]:
            entry["status"] = "missing"
            entry["detail"] = "Le lecteur ne renvoie aucune image pour cet album."
        else:
            try:
                data, ctype = client.fetch_image(alb["image"])
                if not data or "image" not in ctype:
                    entry["status"] = "missing"
                    entry["detail"] = f"Réponse invalide du lecteur ({ctype or 'vide'})."
                else:
                    size = len(data)
                    entry["size"] = size
                    if size < placeholder_max_bytes:
                        h = hashlib.md5(data).hexdigest()
                        cluster = small_clusters.setdefault(h, {
                            "count": 0, "size": size, "data": data, "ctype": ctype, "idxs": []
                        })
                        cluster["count"] += 1
                        cluster["idxs"].append(len(results))
            except Exception as e:
                entry["status"] = "error"
                entry["detail"] = f"Erreur réseau : {e}"
        results.append(entry)

    flagged_thumb_count = 0
    for h, cluster in small_clusters.items():
        if cluster["count"] >= placeholder_min_count:
            thumb_uri = "data:" + (cluster["ctype"] or "image/jpeg") + ";base64," + \
                base64.b64encode(cluster["data"]).decode("ascii")
            flagged_thumb_count += 1
            for i in cluster["idxs"]:
                results[i]["status"] = "placeholder"
                results[i]["thumb"] = thumb_uri
                results[i]["detail"] = (
                    f"Image de {cluster['size'] / 1024:.0f} Ko, identique à "
                    f"{cluster['count'] - 1} autres albums : taille et répétition "
                    f"typiques de l'icône générique BluOS. Vérifiez la miniature "
                    f"ci-contre — si c'est une vraie pochette (coffret/série), "
                    f"ignorez cette ligne."
                )

    log(f"  ... {flagged_thumb_count} image(s) suspecte(s) identifiée(s) "
        f"(petite taille + répétée).")
    return results


# ============================================================================
# PARTIE 2 — Scan des fichiers audio locaux
# ============================================================================
def _extract_embedded_bytes(filepath):
    """Octets bruts de la première pochette intégrée, ou None."""
    if not HAS_MUTAGEN:
        return None

    from mutagen import File as MutagenFile

    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".flac":
            from mutagen.flac import FLAC
            audio = FLAC(filepath)
            if audio.pictures:
                return audio.pictures[0].data

        elif ext in (".mp3", ".wav", ".aiff", ".aif"):
            from mutagen.id3 import ID3
            try:
                tags = ID3(filepath)
            except Exception:
                mf = MutagenFile(filepath)
                tags = getattr(mf, "tags", None)
            if tags and hasattr(tags, "getall"):
                apics = tags.getall("APIC")
                if apics:
                    return apics[0].data

        elif ext in (".m4a", ".mp4", ".aac", ".alac"):
            from mutagen.mp4 import MP4
            audio = MP4(filepath)
            covr = audio.tags.get("covr") if audio.tags else None
            if covr:
                return bytes(covr[0])

        elif ext in (".ogg", ".opus"):
            mf = MutagenFile(filepath)
            pics = mf.get("metadata_block_picture") if mf else None
            if pics:
                from mutagen.flac import Picture
                raw = base64.b64decode(pics[0])
                return Picture(raw).data

        else:
            mf = MutagenFile(filepath)
            if mf and mf.tags:
                for key in list(mf.tags.keys()):
                    if "APIC" in key or "covr" in key.lower():
                        val = mf.tags[key]
                        if isinstance(val, list):
                            val = val[0]
                        data = getattr(val, "data", None)
                        if data is None and not isinstance(val, str):
                            try:
                                data = bytes(val)
                            except Exception:
                                data = None
                        if data:
                            return data
    except Exception:
        return None
    return None


def _identify_image(data):
    """Renvoie (format, largeur, hauteur) d'une image à partir de ses octets."""
    if not HAS_PIL:
        return ("inconnu", None, None)
    try:
        img = Image.open(io.BytesIO(data))
        return (img.format or "inconnu", img.width, img.height)
    except Exception:
        return ("illisible", None, None)


def scan_local_folders(root_path, params, log=_default_log, stop_event=None,
                       progress_cb=None):
    """Parcourt root_path ; chaque dossier avec de l'audio est diagnostiqué.

    params : dict issu de _load_params() (seuils externes/embedded).
    """
    embedded_max_bytes = params["embedded_max_bytes"]
    external_autoresize_min = params["external_autoresize_min"]
    external_autoresize_max = params["external_autoresize_max"]
    external_no_optimize_max_px = params["external_no_optimize_max_px"]

    results = []
    folder_count = 0

    for dirpath, _dirnames, filenames in os.walk(root_path):
        if stop_event is not None and stop_event.is_set():
            log("  ... scan fichiers interrompu par l'utilisateur.")
            break
        audio_files = [f for f in filenames if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS]
        if not audio_files:
            continue

        folder_count += 1
        if folder_count % 25 == 0:
            log(f"  ... {folder_count} dossiers d'album analysés")
        if progress_cb is not None:
            progress_cb(folder_count, None)

        entry = {
            "folder": dirpath,
            "issues": [],
            "notes": [],
            "tracks_checked": len(audio_files),
        }

        lower_files = {f.lower(): f for f in filenames}

        for candidate in ("folder.bmp", "cover.bmp"):
            if candidate in lower_files:
                real = lower_files[candidate]
                entry["issues"].append(
                    f"« {real} » est au format BMP : BluOS ne recherche que les "
                    f"extensions .jpg/.jpeg/.png pour la pochette de dossier, ce "
                    f"fichier est donc totalement ignoré par l'indexation."
                )

        external_found = None
        for name in VALID_EXTERNAL_NAMES:
            if name in lower_files:
                external_found = lower_files[name]
                break

        if external_found:
            fp = os.path.join(dirpath, external_found)
            try:
                size = os.path.getsize(fp)
            except OSError:
                size = None
            if size is not None:
                entry["external_art_file"] = external_found
                entry["external_art_size"] = size
                if size >= external_autoresize_max:
                    entry["issues"].append(
                        f"« {external_found} » pèse {size / 1024 / 1024:.1f} Mo (≥ 4 Mo) : "
                        f"trop lourd pour être traité par BluOS, même avec "
                        f"« Optimiser les pochettes » activé."
                    )
                elif size >= external_autoresize_min:
                    entry["notes"].append(
                        f"« {external_found} » pèse {size / 1024:.0f} Ko (entre 600 Ko et 4 Mo) : "
                        f"BluOS le redimensionnera automatiquement SI l'option "
                        f"« Optimiser les pochettes » est activée. Si elle est "
                        f"désactivée, cette pochette ne s'affichera pas."
                    )
                elif HAS_PIL:
                    try:
                        with open(fp, "rb") as fh:
                            fmt, w, h = _identify_image(fh.read())
                        if w and h and (w > external_no_optimize_max_px or h > external_no_optimize_max_px):
                            entry["notes"].append(
                                f"« {external_found} » fait {w}x{h}px : si « Optimiser les "
                                f"pochettes » est désactivée dans BluOS, la résolution doit "
                                f"rester sous 1200x1200px, sinon cette pochette ne s'affichera pas."
                            )
                    except Exception:
                        pass

        embedded_hashes = {}
        reported = set()
        if HAS_MUTAGEN:
            for f in audio_files:
                fp = os.path.join(dirpath, f)
                data = _extract_embedded_bytes(fp)
                if not data:
                    continue
                fmt, w, h = _identify_image(data)
                embedded_hashes[f] = hashlib.md5(data).hexdigest()

                if fmt and fmt.upper() not in ("JPEG", "PNG") and "format" not in reported:
                    entry["issues"].append(
                        f"Pochette intégrée au format {fmt} détectée (ex. « {f} ») : "
                        f"BluOS n'accepte que le JPEG et le PNG en pochette intégrée "
                        f"— un BMP, même petit, ne sera jamais indexé."
                    )
                    reported.add("format")

                if len(data) >= embedded_max_bytes and "size" not in reported:
                    entry["issues"].append(
                        f"Pochette intégrée de {len(data) / 1024:.0f} Ko (ex. « {f} ») : "
                        f"au-delà de 600 Ko, BluOS n'indexe pas la pochette intégrée, "
                        f"quel que soit le réglage « Optimiser les pochettes »."
                    )
                    reported.add("size")
        elif audio_files and not external_found:
            entry["notes"].append(
                "Le module 'mutagen' n'est pas installé : impossible d'inspecter les "
                "pochettes intégrées dans les fichiers audio de ce dossier."
            )

        distinct_hashes = set(embedded_hashes.values())
        if len(distinct_hashes) > 1:
            entry["issues"].append(
                f"Les pochettes intégrées diffèrent d'une piste à l'autre "
                f"({len(distinct_hashes)} images différentes trouvées parmi "
                f"{len(embedded_hashes)} pistes analysées) : BluOS attend la "
                f"même image sur toutes les pistes d'un album."
            )
        elif not embedded_hashes and not external_found and HAS_MUTAGEN:
            entry["issues"].append(
                "Aucune pochette trouvée : ni fichier folder.jpg/cover.jpg dans "
                "le dossier, ni pochette intégrée dans les fichiers audio."
            )

        if entry["issues"] or entry["notes"]:
            results.append(entry)

    return results


# ============================================================================
# Rapprochement des deux volets (best effort, par nom d'artiste/album)
# ============================================================================
def _normalize(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def cross_reference(network_results, folder_results):
    """Marque les dossiers locaux correspondant à un album fautif réseau."""
    flagged = [r for r in network_results if r["status"] in ("missing", "placeholder", "error")]
    flagged_norms = [
        (_normalize(r["artist"]) + _normalize(r["title"]), r)
        for r in flagged
    ]
    for folder_entry in folder_results:
        folder_norm = _normalize(os.path.basename(folder_entry["folder"])) + \
            _normalize(os.path.basename(os.path.dirname(folder_entry["folder"])))
        for norm, net_entry in flagged_norms:
            if norm and (norm in folder_norm or folder_norm in norm):
                folder_entry["matched_network_album"] = f"{net_entry['artist']} — {net_entry['title']}"
                break
    return folder_results


# ============================================================================
# API PUBLIQUE — fonctions pures pour le backend (Lot 2)
# ============================================================================
def scan_network(ip=None, port=None, timeout=None, log=_default_log,
                 stop_event=None, progress_cb=None, debug=False):
    """Volet A : scanne le Node BluOS et renvoie les albums fautifs.

    Params lus depuis bluos_config si non fournis. Renvoie :
      {"player": {name, model}, "results": [...], "flagged": int}
    Lève RuntimeError si le lecteur est injoignable ou la lib introuvable.
    """
    if not HAS_REQUESTS:
        raise RuntimeError("Le module 'requests' est requis pour le scan réseau BluOS.")

    if ip is None:
        ip = get_bluos_param("bluos_ip", "192.168.1.121")
    if port is None:
        try:
            port = int(float(get_bluos_param("bluos_port", 11000)))
        except (TypeError, ValueError):
            port = 11000
    if timeout is None:
        try:
            timeout = int(float(get_bluos_param("bluos_timeout", 10)))
        except (TypeError, ValueError):
            timeout = 10

    params = _load_params()
    log(f"Connexion à {ip}:{port} ...")
    client = BluOSClient(ip, port, timeout)
    player_info = client.check_reachable()
    log(f"Connecté : {player_info['name']} ({player_info['model']})")

    albums_key = find_albums_browse_key(client, log=log, debug=debug)
    albums = collect_local_albums(client, albums_key, log=log, debug=debug)
    log(f"{len(albums)} albums trouvés dans la bibliothèque locale.")

    results = analyze_network_artwork(client, albums, params, log=log,
                                      stop_event=stop_event, progress_cb=progress_cb)
    flagged = [r for r in results if r["status"] != "ok"]
    log(f"-> {len(flagged)} album(s) avec une pochette manquante ou générique.")
    return {"player": player_info, "results": results, "flagged": len(flagged)}


def diagnose_library(source_path, network_results=None, log=_default_log,
                     stop_event=None, progress_cb=None):
    """Volet B : diagnostique un dossier local. Renvoie la liste des dossiers
    avec un problème (issues/notes). Croise avec network_results si fourni.
    """
    if not source_path or not os.path.isdir(source_path):
        raise RuntimeError(f"Chemin introuvable : {source_path!r}")
    params = _load_params()
    log(f"Scan du dossier local '{source_path}'...")
    folder_results = scan_local_folders(source_path, params, log=log,
                                        stop_event=stop_event, progress_cb=progress_cb)
    if network_results:
        folder_results = cross_reference(network_results, folder_results)
    log(f"-> {len(folder_results)} dossier(s) avec un diagnostic à examiner.")
    return folder_results
