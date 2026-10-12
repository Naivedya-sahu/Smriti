#!/bin/sh
# Read-only QML probe: enumerates selection-related properties; changes no ink.
# Requires the Smriti eye patch loaded and armed so its qml-cmd bridge is live.
set -eu
H=${1:-rm2}
ID=$(date +%s)
JS='(function(){var out=[];function scan(name,o){if(!o)return;for(var k in o){if(/select|bound|rect/i.test(k))out.push(name+"."+k+"="+String(o[k]));}}scan("sceneController",sceneController);try{scan("menuParent",sceneController.parent);}catch(e){}return out.join("\\n");})()'
PAYLOAD=$(printf '{"id":%s,"cmd":"eval","js":%s}' "$ID" "$(printf '%s' "$JS" | PYTHONUTF8=1 ./.venv/Scripts/python.exe -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")
printf '%s' "$PAYLOAD" | ssh "$H" 'mkdir -p /home/root/.smriti; cat > /home/root/.smriti/qml-cmd'
i=0
while [ "$i" -lt 30 ]; do
    OUT=$(ssh "$H" 'cat /home/root/.smriti/qml-out 2>/dev/null || true')
    case "$OUT" in "$ID"*) printf '%s\n' "$OUT"; exit 0;; esac
    i=$((i + 1))
    sleep 0.2
done
echo "probe timed out; arm Smriti and verify smriti-eye.qmd is loaded" >&2
exit 1
