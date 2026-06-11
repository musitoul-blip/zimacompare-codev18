"""ZimaCompare v3 - Vérifications pré-flight bloquantes avant synchronisation."""
import os
import shutil
import time
from pathlib import Path
from typing import List, Tuple

WRITE_TEST_FILE = ".zima_write_test"


def check_source_readable(source: str) -> Tuple[bool, str]:
    p = Path(source)
    if not p.exists():
        return False, f"Source inexistante : {source}"
    if not p.is_dir():
        return False, f"Source n'est pas un répertoire : {source}"
    if not os.access(source, os.R_OK):
        return False, f"Source non lisible : {source}"
    return True, ""


def check_target_accessible(target: str) -> Tuple[bool, str]:
    p = Path(target)
    if not p.exists():
        return False, f"Cible inaccessible ou non montée : {target}"
    if not p.is_dir():
        return False, f"Cible n'est pas un répertoire : {target}"
    return True, ""


def check_target_writable(target: str) -> Tuple[bool, str]:
    test_path = Path(target) / WRITE_TEST_FILE
    try:
        test_path.write_text(f"zima_write_test_{time.time()}")
        return True, ""
    except PermissionError:
        return False, f"Droits d'écriture insuffisants sur la cible : {target}"
    except Exception as e:
        return False, f"Erreur test écriture sur {target} : {e}"
    finally:
        try:
            test_path.unlink(missing_ok=True)
        except Exception:
            pass


def check_disk_space(source: str, target: str, bytes_to_copy: int) -> Tuple[bool, str]:
    try:
        free = shutil.disk_usage(target).free
        if bytes_to_copy > free:
            needed_gb = bytes_to_copy / (1024 ** 3)
            free_gb   = free / (1024 ** 3)
            return False, (
                f"Espace insuffisant sur la cible : {needed_gb:.2f} Go requis, "
                f"{free_gb:.2f} Go disponible."
            )
        return True, ""
    except Exception as e:
        return False, f"Impossible de vérifier l'espace disque : {e}"


def run_preflight(source: str, target: str, bytes_to_copy: int) -> List[str]:
    """Lance les 4 vérifications et retourne la liste d'erreurs (vide = OK)."""
    errors: List[str] = []

    ok, msg = check_source_readable(source)
    if not ok:
        errors.append(msg)

    ok, msg = check_target_accessible(target)
    if not ok:
        errors.append(msg)
        return errors  # inutile d'aller plus loin

    ok, msg = check_target_writable(target)
    if not ok:
        errors.append(msg)

    ok, msg = check_disk_space(source, target, bytes_to_copy)
    if not ok:
        errors.append(msg)

    return errors
