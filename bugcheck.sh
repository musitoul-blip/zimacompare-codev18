#!/bin/sh
# =====================================================================
#  bugcheck.sh  -  filet anti-bug ZimaCompare&Tag v10  (v2)
#  Usage : sudo -v && sudo sh bugcheck.sh   (conteneur zimacompare-v13 en service)
#  Verdict PASS / A VOIR. Ignore le bruit connu (RUF012/B007/F401/unicode),
#  ne signale que les VRAIS bugs + erreurs runtime.
# =====================================================================
C=zimacompare-v13
PORT=8514
FAIL=0

echo "===== 1/5  SYNTAXE (compileall) ====="
if sudo docker exec $C sh -lc "cd /app && python3 -m compileall -q ."; then
  echo "  OK"
else
  echo "  >> ECHEC SYNTAXE"; FAIL=1
fi

echo "===== 2/5  RUFF (vrais bugs uniquement) ====="
RUFF_REAL="F821,F823,F811,F841,F501,F502,F506,F522,F601,F602,F631,F632,F633,F701,F702,F706,F707,PLE,B006,B012,B018"
OUT=$(sudo docker exec $C sh -lc "pip install ruff -q 2>/dev/null; cd /app && ruff check --select $RUFF_REAL --exclude '__pycache__' . 2>&1")
echo "$OUT" | tail -20
HARD=$(echo "$OUT" | grep -E "F821|F823|F811|F50|F60|F63|F70|PLE|B006|B012|B018")
if [ -n "$HARD" ]; then
  echo "  >> BUGS POTENTIELS (voir ci-dessus)"; FAIL=1
else
  echo "  OK (au pire des var inutilisees cosmetiques F841)"
fi

echo "===== 3/5  IMPORTS (runtime, meme sys.path que l'app) ====="
IMP=$(sudo docker exec -i $C python3 - <<'PY'
import importlib, sys, os
ok = True
sys.path.insert(0, '/app')              # racine backend
sys.path.insert(0, '/app/tagaudit')     # comme tagscan.py (core/providers/audit/engine/export)
mods = [f[:-3] for f in os.listdir('/app') if f.endswith('.py') and f != '__init__.py']
for sub in ('core', 'providers', 'audit', 'engine', 'export'):
    d = '/app/tagaudit/' + sub
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith('.py') and f != '__init__.py':
                mods.append(sub + '.' + f[:-3])
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        line = (str(e).splitlines() or [''])[0]
        print("IMPORT KO:", m, "->", type(e).__name__, line)
        ok = False
print("ALLOK" if ok else "SOMEFAIL")
PY
)
echo "$IMP" | grep -v ALLOK | grep -v SOMEFAIL
if echo "$IMP" | grep -q "^ALLOK$\|ALLOK"; then echo "  OK"; else echo "  >> IMPORT(S) CASSE(S)"; FAIL=1; fi

echo "===== 4/5  SMOKE ENDPOINTS GET (5xx/000 = bug handler ; 4xx = validation, OK) ====="
for p in /api/health /api/status /api/file-types /api/profiles /api/tag/progress /api/tag/report.html /api/smart/devices; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT$p" 2>/dev/null)
  case "$code" in
    5*|000) echo "  $code $p   <<< BUG"; FAIL=1 ;;
    *)      echo "  $code $p" ;;
  esac
done

echo "===== 5/5  AUDITS ENREGISTRES SANS METHODE (specifique projet) ====="
MISS=$(sudo docker exec $C sh -lc "cd /app/tagaudit/audit && REF=\$(grep -oE 'self\._audit_[a-z0-9_]+' audit_engine.py | sed 's/self\.//' | sort -u); DEF=\$(grep -oE 'def _audit_[a-z0-9_]+' audit_engine.py | sed 's/def //' | sort -u); for r in \$REF; do echo \"\$DEF\" | grep -qx \"\$r\" || echo \"  MANQUE: \$r\"; done")
if [ -n "$MISS" ]; then echo "$MISS"; echo "  >> AUDIT(S) FANTOME(S)"; FAIL=1; else echo "  OK (tous les audits enregistres ont leur methode)"; fi

echo "================================================================"
if [ $FAIL = 0 ]; then echo "VERDICT : PASS  -  aucun bug bloquant detecte"; else echo "VERDICT : A VOIR  -  items '>>' ci-dessus"; fi
