#!/usr/bin/env bash
#
# Regenerate the README screenshot from a live etop session.
#
# The script copies the working tree's etop_tui package to the target (or
# runs it in place), starts etop inside a detached tmux pane of an exact
# size, captures the coloured screen with "tmux capture-pane -e" and renders
# it to a PNG via tools/render_ansi.py.
#
# Examples:
#   tools/screenshot.sh                      # this machine, 116x6
#   tools/screenshot.sh -H dev24G            # remote host with a GPU
#   tools/screenshot.sh -H dev24G -x 100 -y 8 -o /tmp/shot.png
#
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

HOST=""
COLS=116
ROWS=6
OUTPUT="$REPO_ROOT/docs/screenshot.png"
TITLE=""
DELAY=4
KEEP=0

usage() {
    cat <<'EOF'
Usage: tools/screenshot.sh [-H host] [-x cols] [-y rows] [-o out.png]
                           [-t title] [-d seconds] [-k]

  -H host    run etop over ssh on this host instead of locally
  -x cols    terminal width  (default 116)
  -y rows    terminal height (default 6)
  -o path    output PNG      (default docs/screenshot.png)
  -t title   window title text (default: host name)
  -d secs    seconds to let etop draw before capturing (default 4)
  -k         keep the raw ANSI capture next to the PNG
  -h         show this help
EOF
}

die() {
    echo "error: $*" >&2
    exit 1
}

while getopts ":H:x:y:o:t:d:kh" opt; do
    case "$opt" in
        H) HOST=$OPTARG ;;
        x) COLS=$OPTARG ;;
        y) ROWS=$OPTARG ;;
        o) OUTPUT=$OPTARG ;;
        t) TITLE=$OPTARG ;;
        d) DELAY=$OPTARG ;;
        k) KEEP=1 ;;
        h) usage; exit 0 ;;
        :) die "option -$OPTARG needs a value" ;;
        *) usage; exit 2 ;;
    esac
done

command -v tmux >/dev/null || die "tmux is required"
command -v python3 >/dev/null || die "python3 is required"
python3 -c 'import PIL' 2>/dev/null || die "Pillow is required: pip install pillow"

case "$OUTPUT" in
    /*) ;;
    *) OUTPUT="$PWD/$OUTPUT" ;;
esac
mkdir -p "$(dirname "$OUTPUT")"

SESSION="etop-shot-$$"
ANSI=$(mktemp "/tmp/etop-shot-XXXXXX.ansi")
REMOTE_DIR=""

cleanup() {
    tmux kill-session -t "$SESSION" 2>/dev/null || true
    if [ -n "$REMOTE_DIR" ]; then
        ssh -o BatchMode=yes -o ConnectTimeout=5 "$HOST" \
            "rm -rf '$REMOTE_DIR'" 2>/dev/null || true
    fi
    if [ "$KEEP" -eq 0 ]; then
        rm -f "$ANSI"
    else
        echo "kept raw capture: $ANSI"
    fi
}
trap cleanup EXIT

if [ -n "$HOST" ]; then
    ssh -o BatchMode=yes -o ConnectTimeout=5 "$HOST" true 2>/dev/null \
        || die "cannot reach $HOST over ssh (try: ssh $HOST)"
    ssh -o BatchMode=yes -o ConnectTimeout=5 "$HOST" 'command -v python3' \
        >/dev/null || die "python3 is missing on $HOST"

    REMOTE_DIR=$(ssh -o BatchMode=yes -o ConnectTimeout=5 "$HOST" \
        'mktemp -d /tmp/etop-shot-XXXXXX')
    tar --exclude=__pycache__ -czf - -C "$REPO_ROOT" etop_tui \
        | ssh -o BatchMode=yes -o ConnectTimeout=5 "$HOST" \
            "tar -xzf - -C '$REMOTE_DIR'" \
        || die "failed to copy etop_tui to $HOST"

    TITLE=${TITLE:-$HOST}
    RUN="cd '$REMOTE_DIR' && LANG=C.UTF-8 TERM=xterm-256color python3 -m etop_tui"
    TMUX_CMD="ssh -t -o BatchMode=yes '$HOST' \"$RUN\""
else
    TITLE=${TITLE:-$(hostname)}
    RUN="cd '$REPO_ROOT' && LANG=C.UTF-8 TERM=xterm-256color python3 -m etop_tui"
    TMUX_CMD=$RUN
fi

tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" -x "$COLS" -y "$ROWS" "$TMUX_CMD" \
    || die "failed to start tmux session"

sleep "$DELAY"

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    die "the etop session exited early; is etop importable on the target?"
fi

tmux capture-pane -e -p -t "$SESSION" > "$ANSI"

if ! grep -q 'CPU' "$ANSI"; then
    echo "--- captured screen ---" >&2
    sed 's/\x1b\[[0-9;]*m//g' "$ANSI" | sed '/^$/d' >&2
    die "capture does not look like an etop screen"
fi

python3 "$REPO_ROOT/tools/render_ansi.py" "$ANSI" \
    -o "$OUTPUT" -t "$TITLE"
