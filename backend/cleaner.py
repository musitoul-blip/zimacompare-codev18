"""ZimaCompare v3.10 — Nettoyage des fichiers .db (exception lecture seule).

Règles strictes :
  - Suppression UNIQUEMENT des fichiers se terminant par .db (extension exacte,
    insensible à la casse).
  - PROTECTION : si le dossier parent contient au moins un fichier audio
    (.flac, .mp3, .m4a, case-insensitive) — directement ou récursivement
    dans ses sous-dossiers — le .db est marqué "protégé" et ne sera PAS
    supprimé.
  - NEW v3.10 : option `force=True` pour OUTREPASSER la protection audio.
    Les .db restent visibles mais sont tous marqués supprimables, avec
    une raison explicite. C'est l'utilisateur qui prend la responsabilité.
  - Le scan ne modifie RIEN, il produit juste la liste.
  - La suppression nécessite un appel explicite et séparé à `execute_cleanup`.
  - Toutes les actions sont tracées dans les logs.

NEW v3.9 : barre de progression honnête sur scan ET exécution.
Le scan est refondu en 3 phases avec un SEUL os.walk (au lieu de 3 en v3.8) :
  1. Pré-comptage : walk + collecte des listings en mémoire + compte des .db.
  2. Cache audio : itère les listings (pas de re-walk disque) + propagation.
  3. Classification : itère les listings pour produire les CleanCandidate.
L'exécution throttle update_state à ~300 ms pour ne pas saturer le disque.
"""
import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import (
    APP_DATA_ROOT, REPORTS_DIR, AppState, setup_logging, update_state, get_state,
    compile_ignore_spec, ignore_match,  # v3.12
)

logger = setup_logging()

# Extensions à nettoyer (case-insensitive)
DB_EXTENSIONS    = {".db"}
# Extensions audio qui activent la protection (case-insensitive)
AUDIO_EXTENSIONS = {".flac", ".mp3", ".m4a"}

# Fichier de résultats du dernier scan
CLEAN_PLAN_FILE = APP_DATA_ROOT / "clean_plan.json"

# Throttle des update_state pour ne pas saturer les écritures disque.
_UI_REFRESH_S = 0.4   # phases scan
_EXEC_REFRESH_S = 0.3  # phase exécution

_stop_event = threading.Event()


def stop_cleanup():
    _stop_event.set()
    logger.warning("[CLEAN] Arrêt demandé")


@dataclass
class CleanCandidate:
    path:           str             # chemin absolu
    relative_path:  str             # relatif à la racine scannée
    size:           int
    mtime:          float
    parent_dir:     str             # dossier parent (relatif)
    protected:      bool            # True si dossier parent contient de l'audio
    reason:         str             # description de la décision
    audio_files:    List[str] = field(default_factory=list)  # extraits d'audio trouvés


def _scan_db(scan_root: Path, force: bool = False) -> List[CleanCandidate]:
    """Liste tous les .db sous `scan_root`, en marquant comme `protected: True`
    ceux dont le dossier parent contient (récursivement) de l'audio.

    Si `force=True`, la protection audio est désactivée : tous les .db sont
    marqués supprimables, mais la raison reflète la présence éventuelle d'audio
    pour traçabilité.

    Flow en 3 phases (NEW v3.9) :
      1. Pré-comptage : 1 seul os.walk qui mémorise les listings (root, files)
         et compte les .db. Permet d'afficher un total honnête.
      2. Cache audio : itère la liste en mémoire pour détecter les dossiers
         avec audio direct, puis propage aux ancêtres. Pas de re-walk disque.
      3. Classification : itère la liste pour produire les CleanCandidate à
         partir du cache audio.
    """
    if not scan_root.exists() or not scan_root.is_dir():
        raise ValueError(f"Dossier inexistant : {scan_root}")

    if force:
        logger.warning(
            "[CLEAN] ⚠ Mode FORCE activé — la protection audio est désactivée pour ce scan"
        )

    # v3.12 — Compile la spec gitignore pour appliquer .zimaignore aussi au cleaner
    spec = compile_ignore_spec()
    root_str = str(scan_root)
    base_len = len(root_str) + 1

    # ── Phase 1 : pré-comptage (1 seul walk, collecte des listings) ──
    logger.info(f"[CLEAN] Phase 1/3 — pré-comptage : {scan_root}")
    update_state(current_file="Pré-comptage…", progress=0, processed=0, total=0)

    listings: List[Tuple[str, List[str]]] = []
    n_db = 0
    n_ignored = 0
    last_update = time.monotonic()

    for current_root, dirs, files in os.walk(scan_root):
        if _stop_event.is_set():
            logger.warning("[CLEAN] Interrompu en pré-comptage")
            return []
        # v3.12 : filtrage gitignore — on retire dossiers et fichiers ignorés.
        # On modifie `dirs` IN-PLACE pour que os.walk n'y descende pas.
        if spec is not None:
            kept_dirs = []
            for d in dirs:
                full = os.path.join(current_root, d)
                rel = full[base_len:] if len(full) > base_len else d
                if ignore_match(spec, rel, True):
                    n_ignored += 1
                else:
                    kept_dirs.append(d)
            dirs[:] = kept_dirs
            kept_files = []
            for f in files:
                full = os.path.join(current_root, f)
                rel = full[base_len:] if len(full) > base_len else f
                if ignore_match(spec, rel, False) and os.path.splitext(f)[1].lower() not in DB_EXTENSIONS:
                    n_ignored += 1
                else:
                    kept_files.append(f)
            files = kept_files
        listings.append((current_root, files))
        n_db += sum(1 for f in files
                    if os.path.splitext(f)[1].lower() in DB_EXTENSIONS)
        now = time.monotonic()
        if now - last_update > _UI_REFRESH_S:
            update_state(current_file=(
                f"Pré-comptage… {len(listings)} dossier(s), {n_db} fichier(s) .db"
            ))
            last_update = now

    n_dirs = len(listings)
    logger.info(
        f"[CLEAN] Pré-comptage terminé — {n_dirs} dossier(s), {n_db} fichier(s) .db"
        f"{f', {n_ignored} ignoré(s) par .zimaignore' if n_ignored else ''}"
    )

    # Cas limite : tree vide ou rien à analyser. On évite la div par zéro
    # et on saute directement à la fin.
    if n_dirs == 0:
        return []

    # Budget de progression : phase 2 et phase 3 chacune O(n_dirs).
    # On expose total = n_dirs * 2, processed avance de 0 à total.
    total_units = n_dirs * 2
    update_state(
        total=total_units, processed=0, progress=0,
        current_file=f"Analyse de {n_dirs} dossier(s), {n_db} fichier(s) .db…",
    )

    # ── Phase 2 : construction du cache audio ────────────────────────
    logger.info(f"[CLEAN] Phase 2/3 — détection de l'audio…")
    audio_in: Dict[str, List[str]] = {}

    for i, (current_root, files) in enumerate(listings):
        if _stop_event.is_set():
            logger.warning("[CLEAN] Interrompu en analyse audio")
            return []
        local_audios = [f for f in files
                        if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS]
        if local_audios:
            audio_in[current_root] = local_audios[:5]

        now = time.monotonic()
        if now - last_update > _UI_REFRESH_S:
            processed = i + 1
            update_state(
                processed=processed,
                progress=int(processed / total_units * 100),
                current_file=f"Analyse audio… {len(audio_in)} dossier(s) avec audio",
            )
            last_update = now

    # Propagation aux ancêtres : opère sur audio_in en mémoire, pas de walk.
    # On snapshote les clés AVANT mutation pour ne pas itérer + muter en parallèle.
    root_str = str(scan_root)
    seeds = list(audio_in.keys())
    for seed in seeds:
        p = Path(seed).parent
        while True:
            ps = str(p)
            if not ps.startswith(root_str):
                break
            if ps not in audio_in:
                audio_in[ps] = audio_in[seed][:5]
            if ps == root_str or p.parent == p:
                break
            p = p.parent

    logger.info(
        f"[CLEAN] {len(audio_in)} dossier(s) avec audio (direct ou descendant)"
    )

    # ── Phase 3 : classification des .db ─────────────────────────────
    logger.info(f"[CLEAN] Phase 3/3 — classification des .db…")
    candidates: List[CleanCandidate] = []
    offset = n_dirs  # n_dirs unités déjà consommées en phase 2

    for i, (current_root, files) in enumerate(listings):
        if _stop_event.is_set():
            logger.warning("[CLEAN] Interrompu en classification")
            break

        db_files = [f for f in files
                    if os.path.splitext(f)[1].lower() in DB_EXTENSIONS]
        for fname in db_files:
            full = os.path.join(current_root, fname)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, scan_root)
            parent_rel = os.path.relpath(current_root, scan_root)

            # PROTECTION : on regarde le dossier PARENT du .db.
            # Si lui ou ses descendants contiennent de l'audio → protégé.
            # Sauf si `force=True` : l'utilisateur a explicitement choisi
            # d'outrepasser la protection.
            audios_here = audio_in.get(current_root, [])
            if audios_here and not force:
                protected = True
                reason = (f"Dossier contient des fichiers audio "
                          f"({', '.join(audios_here[:3])}"
                          f"{'…' if len(audios_here) >= 3 else ''})")
            elif audios_here and force:
                protected = False
                reason = (f"⚠ Audio présent ({', '.join(audios_here[:3])}"
                          f"{'…' if len(audios_here) >= 3 else ''}) "
                          f"— protection ignorée (mode FORCE)")
            else:
                protected = False
                reason = "Aucun fichier audio dans ce dossier ni ses sous-dossiers"

            candidates.append(CleanCandidate(
                path=full, relative_path=rel, size=st.st_size, mtime=st.st_mtime,
                parent_dir=parent_rel if parent_rel != "." else "(racine)",
                protected=protected, reason=reason,
                audio_files=audios_here,
            ))

        now = time.monotonic()
        if now - last_update > _UI_REFRESH_S:
            processed = offset + i + 1
            update_state(
                processed=processed,
                progress=int(processed / total_units * 100),
                current_file=f"Classification… {len(candidates)}/{n_db} fichier(s) .db",
            )
            last_update = now

    return candidates


def _save_plan(candidates: List[CleanCandidate], root: str, force: bool = False):
    plan = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": root,
        "force": force,
        "total":          len(candidates),
        "deletable":      sum(1 for c in candidates if not c.protected),
        "protected":      sum(1 for c in candidates if c.protected),
        "deletable_size": sum(c.size for c in candidates if not c.protected),
        "candidates": [asdict(c) for c in candidates],
    }
    APP_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = CLEAN_PLAN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
    tmp.replace(CLEAN_PLAN_FILE)
    return plan


def _run_scan(root: str, force: bool = False):
    try:
        update_state(app_state=AppState.SCANNING,
                     current_file="Scan .db en cours…",
                     progress=0, processed=0, total=0,
                     new_count=0, different_count=0, deleted_count=0, identical_count=0,
                     error="")
        candidates = _scan_db(Path(root), force=force)
        plan = _save_plan(candidates, root, force=force)
        logger.info(
            f"[CLEAN] Scan terminé — {plan['total']} .db trouvés "
            f"({plan['deletable']} supprimables, {plan['protected']} protégés)"
            f"{' [mode FORCE]' if force else ''}"
        )
        # On utilise les compteurs existants pour l'affichage post-scan
        update_state(
            app_state=AppState.IDLE,
            current_file="",
            progress=100,
            total=plan["total"],
            processed=plan["total"],
            new_count=plan["deletable"],          # à supprimer
            different_count=plan["protected"],    # protégés
            deleted_count=0,
            identical_count=0,
            bytes_to_copy=plan["deletable_size"],
            scan_done=True,
        )
    except Exception as e:
        logger.error(f"[CLEAN] Erreur scan: {e}", exc_info=True)
        update_state(app_state=AppState.ERROR, error=f"Scan nettoyage : {e}")


def start_scan_db(root: str, force: bool = False) -> bool:
    """Lance un scan .db en thread daemon.

    Si `force=True`, la protection audio est désactivée : tous les .db
    seront marqués supprimables, même dans les dossiers contenant de la musique.
    """
    state = get_state()
    if state["app_state"] not in (AppState.IDLE, AppState.ERROR):
        return False
    _stop_event.clear()
    update_state(source=root, target="", method="cleanup_db", error="")
    threading.Thread(target=_run_scan, args=(root, force), daemon=True).start()
    return True


def load_plan() -> Optional[dict]:
    if not CLEAN_PLAN_FILE.exists():
        return None
    try:
        return json.loads(CLEAN_PLAN_FILE.read_text())
    except Exception:
        return None


def _is_safe_db_path(path: str, root: str) -> bool:
    """Garde-fou : refuse tout chemin qui n'est pas un .db strictement sous root."""
    if not path.lower().endswith(".db"):
        return False
    try:
        real_path = os.path.realpath(path)
        real_root = os.path.realpath(root).rstrip("/") + "/"
        if not (real_path + "/").startswith(real_root):
            return False
        return True
    except Exception:
        return False


def _run_execute(root: str, dry_run: bool):
    """Exécute la suppression à partir du plan enregistré.

    NEW v3.9 : update_state throttlé à ~300 ms pour éviter de saturer les
    écritures disque sur des plans massifs (sinon : 1 fsync par fichier).
    """
    try:
        plan = load_plan()
        if not plan:
            raise RuntimeError("Aucun plan de nettoyage. Lance d'abord un scan.")
        if plan["root"] != root:
            raise RuntimeError(
                f"Le plan a été généré pour {plan['root']!r}, "
                f"pas pour {root!r}. Relance un scan."
            )

        to_delete = [c for c in plan["candidates"] if not c["protected"]]
        total = len(to_delete)
        logger.info(f"[CLEAN] {'SIMULATION' if dry_run else 'SUPPRESSION'} "
                    f"démarrée — {total} fichier(s) ciblé(s)")

        update_state(
            app_state=AppState.SYNCING, method="cleanup_db",
            current_file="Nettoyage en cours…",
            progress=0, processed=0, total=total,
            sync_done=0, sync_errors=0, sync_simulated=0,
            error="",
        )

        done = errors = simulated = 0
        executed_paths: List[str]    = []
        error_paths:    List[dict]   = []
        last_state_update = time.monotonic()

        for i, c in enumerate(to_delete):
            if _stop_event.is_set():
                logger.warning("[CLEAN] Arrêt demandé par l'utilisateur")
                # Flush final pour que l'UI voie le dernier état avant IDLE.
                update_state(
                    app_state=AppState.IDLE, current_file="Annulé",
                    processed=i, progress=int(i / total * 100) if total else 0,
                    sync_done=done, sync_errors=errors, sync_simulated=simulated,
                )
                return

            path = c["path"]

            # ASSERTIONS RUNTIME — triple garde-fou
            if not _is_safe_db_path(path, root):
                errors += 1
                error_paths.append({"path": path, "error": "Chemin refusé par le garde-fou"})
                logger.error(f"[CLEAN] REFUS chemin invalide : {path}")
                # On continue, mais on flush quand même pour l'UI au prochain tick.
            elif dry_run:
                simulated += 1
                logger.info(f"[CLEAN][DRY-RUN] suppression simulée : {c['relative_path']}")
            else:
                try:
                    os.unlink(path)
                    done += 1
                    executed_paths.append(c["relative_path"])
                    logger.info(f"[CLEAN] supprimé : {c['relative_path']}")
                except FileNotFoundError:
                    # Déjà supprimé entre-temps, on considère que c'est OK
                    done += 1
                    logger.info(f"[CLEAN] déjà absent : {c['relative_path']}")
                except Exception as e:
                    errors += 1
                    error_paths.append({"path": c["relative_path"], "error": str(e)})
                    logger.error(f"[CLEAN] erreur sur {c['relative_path']}: {e}")

            # Throttle des updates UI : 1 par ~300 ms, plus un flush final.
            now = time.monotonic()
            is_last = (i + 1 == total)
            if now - last_state_update > _EXEC_REFRESH_S or is_last:
                update_state(
                    processed=i + 1,
                    progress=int((i + 1) / total * 100) if total else 100,
                    current_file=c["relative_path"],
                    sync_done=done, sync_errors=errors, sync_simulated=simulated,
                )
                last_state_update = now

        # Rapport JSON
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode = "simulation" if dry_run else "execution"
        report_path = REPORTS_DIR / f"clean_db_{mode}_{ts}.json"
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "root": root, "dry_run": dry_run,
            "summary": {"total": total, "done": done, "errors": errors, "simulated": simulated},
            "executed": executed_paths, "errors": error_paths,
        }, ensure_ascii=False, indent=2))

        logger.info(f"[CLEAN] Terminé — done={done} errors={errors} simulated={simulated}")
        update_state(app_state=AppState.IDLE, progress=100, current_file="")

    except Exception as e:
        logger.error(f"[CLEAN] Erreur exécution: {e}", exc_info=True)
        update_state(app_state=AppState.ERROR, error=str(e))


def start_execute(root: str, dry_run: bool) -> bool:
    state = get_state()
    if state["app_state"] not in (AppState.IDLE, AppState.ERROR):
        return False
    _stop_event.clear()
    threading.Thread(target=_run_execute, args=(root, dry_run), daemon=True).start()
    return True
