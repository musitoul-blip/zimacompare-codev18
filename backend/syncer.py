"""ZimaCompare v3.4 — Sync avec auto-vérification post-sync.

NEW v3.4 : après un sync RÉEL réussi (non-dry_run, non-annulé, sans erreur),
un scan ultra-rapide est automatiquement déclenché pour confirmer qu'il
n'y a plus aucune différence. Le résultat est exposé dans le state via
`sync_verified` ("pending" → "ok" | "failed").
"""
import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List

from comparators import verify_copy
from config import REPORTS_DIR, AppState, setup_logging, update_state, get_state
from mountcheck import MountGuard, MountLost, precheck_target
from preflight import run_preflight
from scanner import load_scan_results, start_scan, _dir_signature

logger = setup_logging()

_stop_event = threading.Event()
_COPY_CHUNK = 1024 * 1024


class AbortedByUser(Exception): pass
class WouldWriteToSource(Exception): pass
class TargetMountLost(Exception): pass  # NEW : montage cible perdu pendant le sync


def stop_sync():
    _stop_event.set()
    logger.warning("[SYNC] Drapeau d'arrêt levé — interruption demandée")


def _interruptible_copy(src: str, dst: str):
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
            while True:
                if _stop_event.is_set():
                    raise AbortedByUser()
                buf = fsrc.read(_COPY_CHUNK)
                if not buf: break
                fdst.write(buf)
        shutil.copystat(src, dst)
    except AbortedByUser:
        try: os.unlink(dst)
        except OSError: pass
        raise


@dataclass
class SyncAction:
    action: str
    relative_path: str
    source_path: str
    target_path: str
    size: int = 0
    status: str = "pending"
    error_msg: str = ""


def _plan(scan_results, source, target, mirror_deletes) -> List[SyncAction]:
    actions: List[SyncAction] = []
    for r in scan_results:
        rel = r.get("relative_path")
        if not rel:
            continue
        src = os.path.join(source, rel)
        tgt = os.path.join(target, rel)
        is_dir = r.get("is_dir", False); st = r.get("status", "")
        if st == "new":
            actions.append(SyncAction("mkdir" if is_dir else "copy", rel, src, tgt, r.get("source_size", 0)))
        elif st == "different" and not is_dir:
            actions.append(SyncAction("copy", rel, src, tgt, r.get("source_size", 0)))
        elif st == "deleted" and mirror_deletes:
            actions.append(SyncAction("rmdir" if is_dir else "delete", rel, src, tgt, r.get("target_size", 0)))
    depth = lambda a: a.relative_path.count(os.sep)
    return (sorted([a for a in actions if a.action == "mkdir"], key=depth)
            + [a for a in actions if a.action == "copy"]
            + [a for a in actions if a.action == "delete"]
            + sorted([a for a in actions if a.action == "rmdir"], key=depth, reverse=True))


def _assert_target_path(target_path, target_root):
    real_t = os.path.realpath(target_path)
    real_r = os.path.realpath(target_root).rstrip("/") + "/"
    if not (real_t + "/").startswith(real_r) and real_t != real_r.rstrip("/"):
        raise WouldWriteToSource(f"path={real_t} root={real_r}")


def _exec_action(action, dry_run, verify, target_root, guard=None) -> SyncAction:
    if _stop_event.is_set():
        action.status = "skipped"; return action
    if dry_run:
        action.status = "simulated"
        logger.info(f"[DRY-RUN] {action.action}: {action.relative_path}")
        return action
    try:
        # NEW : vérifie que la cible est toujours montée AVANT toute écriture.
        # Si rclone/CIFS a décroché, le device id a changé → MountLost.
        if guard is not None:
            guard.check()
        _assert_target_path(action.target_path, target_root)
        if action.action == "mkdir":
            Path(action.target_path).mkdir(parents=True, exist_ok=True)
        elif action.action == "copy":
            _interruptible_copy(action.source_path, action.target_path)
            if verify and not verify_copy(Path(action.source_path), Path(action.target_path)):
                raise RuntimeError("Vérification post-copie échouée")
        elif action.action == "delete":
            p = Path(action.target_path)
            if p.exists(): p.unlink()
        elif action.action == "rmdir":
            p = Path(action.target_path)
            if p.exists(): shutil.rmtree(p)
        action.status = "done"
    except AbortedByUser:
        action.status = "skipped"; action.error_msg = "Annulé par l'utilisateur"
    except MountLost as e:
        # NEW : montage cible perdu — on lève l'arrêt d'urgence global.
        action.status = "error"
        action.error_msg = f"MONTAGE CIBLE PERDU : {e}"
        logger.error(f"[SYNC] {action.error_msg}")
        _stop_event.set()  # stoppe toute la sync immédiatement
        raise TargetMountLost(str(e))
    except WouldWriteToSource as e:
        action.status = "error"; action.error_msg = f"BLOQUÉ (garde-fou) : {e}"
        logger.error(f"[SYNC] {action.error_msg}")
    except Exception as e:
        action.status = "error"; action.error_msg = str(e)
        logger.error(f"[SYNC] ERREUR {action.action} {action.relative_path}: {e}")
    return action


def _auto_verify(source: str, target: str):
    """NEW v3.4 : déclenche un scan ultra-rapide pour vérifier que la sync
    a effectivement aligné les deux côtés. Lancé en thread daemon."""
    logger.info("[VERIFY] Lancement vérification post-sync (scan ultra-rapide)…")
    update_state(sync_verified="pending", sync_verified_msg="Vérification en cours…")

    # On attend ~1s que le state IDLE soit bien posé par _run_sync, puis on relance.
    time.sleep(1.0)

    # Lance un scan ultra_fast sans toucher au flag sync_verified
    ok = start_scan(source, target, "ultra_fast", chunk_mb=4, clear_verification=False)
    if not ok:
        update_state(sync_verified="failed",
                     sync_verified_msg="Impossible de lancer la vérification (état non IDLE)")
        return

    # Polling jusqu'à fin du scan
    deadline = time.monotonic() + 1800  # 30 min max
    while time.monotonic() < deadline:
        time.sleep(0.5)
        s = get_state()
        if s["app_state"] == AppState.IDLE and s.get("scan_done"):
            diffs = s["new_count"] + s["different_count"] + s["deleted_count"]
            if diffs == 0:
                msg = f"Vérification OK — source et cible sont strictement identiques."
                logger.info(f"[VERIFY] {msg}")
                update_state(sync_verified="ok", sync_verified_msg=msg)
            else:
                msg = (f"{diffs} différence(s) résiduelle(s) détectée(s) après sync : "
                       f"{s['new_count']} nouveaux, {s['different_count']} modifiés, "
                       f"{s['deleted_count']} supprimés.")
                logger.warning(f"[VERIFY] {msg}")
                update_state(sync_verified="failed", sync_verified_msg=msg)
            return
        if s["app_state"] == AppState.ERROR:
            update_state(sync_verified="failed",
                         sync_verified_msg=f"Erreur durant la vérification : {s.get('error', '')}")
            return

    update_state(sync_verified="failed", sync_verified_msg="Timeout de la vérification (>30 min)")


def _run_sync(source, target, dry_run, verify, mirror_deletes, max_workers, auto_verify):
    try:
        logger.info(f"[SYNC] Démarrage — source={source} (LECTURE SEULE) target={target} dry_run={dry_run}")
        update_state(app_state=AppState.SYNCING, progress=0, processed=0,
                     sync_done=0, sync_errors=0, sync_simulated=0, error="",
                     sync_verified="", sync_verified_msg="")

        scan_results = load_scan_results()
        if not scan_results:
            raise RuntimeError("Aucun résultat de scan disponible — lancez un scan d'abord.")

        if not dry_run:
            errors = run_preflight(source, target, get_state()["bytes_to_copy"])
            if errors:
                msg = " | ".join(errors)
                update_state(app_state=AppState.ERROR, error=msg); return

        # NEW : contrôle statique du montage cible avant d'aller plus loin.
        if not dry_run:
            expect_net = target.startswith("/network/")
            pre = precheck_target(target, expect_network=expect_net)
            if pre:
                logger.error(f"[SYNC] Pré-contrôle montage échoué : {pre}")
                update_state(app_state=AppState.ERROR, error=pre); return

        if mirror_deletes:
            _st = get_state()
            _live_tsig = "%.0f:%d" % _dir_signature(Path(target))
            if _st.get("source_changed") or _st.get("target_changed") or (_st.get("target_sig") and _live_tsig != _st["target_sig"]):
                update_state(app_state=AppState.ERROR, error="Plan perime : source ou cible modifiee depuis le scan. Relancez un scan avant la synchro.")
                return
        actions = _plan(scan_results, source, target, mirror_deletes)
        total = len(actions)
        if total == 0:
            update_state(app_state=AppState.IDLE, progress=100); return

        # NEW : armement du garde-montage (sauf en dry-run, aucune écriture).
        guard = None
        if not dry_run:
            guard = MountGuard(target)
            try:
                guard.arm()
            except MountLost as e:
                logger.error(f"[SYNC] Armement garde-montage impossible : {e}")
                update_state(app_state=AppState.ERROR,
                             error=f"Cible non sûre : {e}")
                return

        done_c = err_c = sim_c = 0
        start  = time.monotonic()
        seq    = [a for a in actions if a.action in ("mkdir", "rmdir")]
        par    = [a for a in actions if a.action in ("copy", "delete")]
        completed: List[SyncAction] = []
        mount_lost_msg = ""  # NEW : renseigné si le montage décroche

        def _process(batch, workers):
            nonlocal done_c, err_c, sim_c, mount_lost_msg
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_exec_action, a, dry_run, verify, target, guard): a
                           for a in batch}
                for fut in as_completed(futures):
                    try:
                        a = fut.result()
                    except TargetMountLost as e:
                        # NEW : un worker a détecté la perte de montage.
                        mount_lost_msg = str(e)
                        pool.shutdown(wait=False, cancel_futures=True)
                        break
                    completed.append(a)
                    if a.status == "done": done_c += 1
                    elif a.status == "error": err_c += 1
                    elif a.status == "simulated": sim_c += 1
                    processed = done_c + err_c + sim_c
                    elapsed = time.monotonic() - start
                    fps = processed / elapsed if elapsed > 0 else 0
                    eta = int((total - processed) / fps) if fps > 0 else 0
                    update_state(processed=processed, total=total,
                                 progress=int(processed/total*100),
                                 current_file=a.relative_path,
                                 fps=round(fps, 1), eta_seconds=eta,
                                sync_done=done_c, sync_errors=err_c, sync_simulated=sim_c)
                    if _stop_event.is_set():
                        pool.shutdown(wait=False, cancel_futures=True); break

        try:
            _process(seq, 1)
            if not _stop_event.is_set() and not mount_lost_msg:
                _process(par, max(1, max_workers))
        finally:
            if guard is not None:
                guard.disarm()

        _save_report(completed, source, target, dry_run)

        # NEW : montage perdu en cours de route → état ERROR explicite.
        if mount_lost_msg:
            msg = (f"SYNC INTERROMPUE — la cible s'est déconnectée pendant "
                   f"l'opération ({mount_lost_msg}). Les fichiers restants "
                   f"n'ont PAS été copiés. Vérifiez le montage (rclone/NAS) "
                   f"avant de relancer.")
            logger.error(f"[SYNC] {msg}")
            update_state(app_state=AppState.ERROR, error=msg, current_file="")
            return

        if _stop_event.is_set():
            update_state(app_state=AppState.IDLE, current_file="Annulé")
            return

        update_state(app_state=AppState.IDLE, progress=100, current_file="",
                     last_sync_at=datetime.now().isoformat(timespec="seconds"))

        # NEW v3.4 : auto-vérification après sync RÉEL réussi
        if auto_verify and not dry_run and err_c == 0:
            threading.Thread(target=_auto_verify, args=(source, target), daemon=True).start()
        elif not dry_run and err_c > 0:
            update_state(sync_verified="failed",
                         sync_verified_msg=f"Vérification non lancée — {err_c} erreur(s) durant le sync.")

    except Exception as e:
        logger.error(f"[SYNC] Erreur fatale: {e}", exc_info=True)
        update_state(app_state=AppState.ERROR, error=str(e))


def _save_report(actions, source, target, dry_run):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    mode = "simulation" if dry_run else "execution"
    path = REPORTS_DIR / f"sync_{mode}_{ts}.json"
    data = {
        "timestamp": datetime.now().isoformat(),
        "source": source, "target": target, "dry_run": dry_run,
        "summary": {
            "done":   sum(1 for a in actions if a.status in ("done", "simulated")),
            "errors": sum(1 for a in actions if a.status == "error"),
            "total":  len(actions),
        },
        "actions": [
            {"action": a.action, "path": a.relative_path, "size": a.size,
             "status": a.status, "error": a.error_msg}
            for a in actions
        ],
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    logger.info(f"[SYNC] Rapport : {path.name}")


def start_sync(source, target, dry_run, verify, mirror_deletes, max_workers,
               auto_verify: bool = True) -> bool:
    if get_state()["app_state"] not in (AppState.IDLE, AppState.ERROR):
        return False
    _stop_event.clear()
    update_state(dry_run=dry_run)
    t = threading.Thread(target=_run_sync,
                         args=(source, target, dry_run, verify, mirror_deletes,
                               max_workers, auto_verify),
                         daemon=True)
    t.start()
    return True
