#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LINKS_FILE="$SCRIPT_DIR/links.txt"
COOKIES_FILE="$SCRIPT_DIR/cookies.txt"
OUTPUT_DIR="$SCRIPT_DIR/outputs"
RUNTIME_DIR="$SCRIPT_DIR/.runtime"
BIN_DIR="$SCRIPT_DIR/bin"
CHROME_PROFILE_DIR="${CHROME_PROFILE_DIR:-$SCRIPT_DIR/.chrome-profile}"
CHROME_DEBUG_PORT="${CHROME_DEBUG_PORT:-9222}"
MODE="${1:-download}"

mkdir -p "$OUTPUT_DIR" "$RUNTIME_DIR" "$BIN_DIR"
[[ -f "$LINKS_FILE" ]] || : > "$LINKS_FILE"
[[ -f "$COOKIES_FILE" ]] || : > "$COOKIES_FILE"

usage() {
  cat <<EOF
Usage:
  ./download.sh                Download links.txt, opening Chrome login if needed
  ./download.sh download       Download links.txt
  ./download.sh login          Open Chrome login profile
  ./download.sh login-download Open Chrome login profile, wait, then download
EOF
}

cookie_file_has_content() {
  [[ -n "$(useful_lines "$COOKIES_FILE" || true)" ]]
}

useful_lines() {
  sed '/^[[:space:]]*#/d; /^[[:space:]]*$/d' "$1"
}

ensure_ytdlp() {
  if [[ -n "${YTDLP:-}" && -x "$YTDLP" ]]; then
    printf '%s\n' "$YTDLP"
    return
  fi

  if command -v yt-dlp >/dev/null 2>&1; then
    command -v yt-dlp
    return
  fi

  local local_ytdlp="$BIN_DIR/yt-dlp"
  if [[ ! -x "$local_ytdlp" ]]; then
    printf '[setup] downloading yt-dlp\n' >&2
    if command -v curl >/dev/null 2>&1; then
      curl -L --fail --output "$local_ytdlp" "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
    elif command -v wget >/dev/null 2>&1; then
      wget -O "$local_ytdlp" "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
    else
      printf '[error] curl or wget is required to download yt-dlp\n' >&2
      return 1
    fi
    chmod +x "$local_ytdlp"
  fi

  printf '%s\n' "$local_ytdlp"
}

build_cookie_args() {
  local cookie_lines
  cookie_lines="$(useful_lines "$COOKIES_FILE" || true)"
  if [[ -z "$cookie_lines" ]]; then
    if [[ -d "$CHROME_PROFILE_DIR" ]]; then
      printf '%s\n%s\n' '--cookies-from-browser' "chrome:$CHROME_PROFILE_DIR"
    fi
    return
  fi

  if head -n 1 "$COOKIES_FILE" | grep -q 'Netscape HTTP Cookie File'; then
    printf '%s\n%s\n' '--cookies' "$COOKIES_FILE"
    return
  fi

  if awk -F '\t' 'NF >= 7 && $1 !~ /^[[:space:]]*#/ { found=1 } END { exit found ? 0 : 1 }' "$COOKIES_FILE"; then
    printf '%s\n%s\n' '--cookies' "$COOKIES_FILE"
    return
  fi

  local cookie_jar="$RUNTIME_DIR/cookies.netscape.txt"
  awk -v out="$cookie_jar" '
    function trim(s) {
      gsub(/^[ \t\r\n]+|[ \t\r\n]+$/, "", s)
      return s
    }
    BEGIN {
      print "# Netscape HTTP Cookie File" > out
    }
    /^[[:space:]]*#/ || /^[[:space:]]*$/ {
      next
    }
    {
      line = $0
      sub(/\r$/, "", line)
      raw = raw line ";"
    }
    END {
      count = split(raw, parts, ";")
      for (i = 1; i <= count; i++) {
        part = trim(parts[i])
        eq = index(part, "=")
        if (part == "" || eq <= 1) {
          continue
        }
        name = trim(substr(part, 1, eq - 1))
        value = trim(substr(part, eq + 1))
        if (name == "") {
          continue
        }
        secure = "FALSE"
        if (name ~ /^__Secure-/ || name ~ /^(SID|HSID|SSID|APISID|SAPISID|SIDCC)$/) {
          secure = "TRUE"
        }
        printf ".youtube.com\tTRUE\t/\t%s\t0\t%s\t%s\n", secure, name, value >> out
      }
    }
  ' "$COOKIES_FILE"
  chmod 600 "$cookie_jar" 2>/dev/null || true
  printf '%s\n%s\n' '--cookies' "$cookie_jar"
}

find_chrome_bin() {
  for name in google-chrome google-chrome-stable chromium chromium-browser chrome; do
    if command -v "$name" >/dev/null 2>&1; then
      command -v "$name"
      return
    fi
  done
}

open_login_browser() {
  mkdir -p "$CHROME_PROFILE_DIR"
  printf '[login] profile: %s\n' "$CHROME_PROFILE_DIR"
  printf '[login] remote debugging port: %s\n' "$CHROME_DEBUG_PORT"

  if [[ "$(uname -s)" == "Darwin" ]]; then
    open -na "Google Chrome" --args \
      "--user-data-dir=$CHROME_PROFILE_DIR" \
      "--remote-debugging-port=$CHROME_DEBUG_PORT" \
      "--no-first-run" \
      "https://accounts.google.com/" \
      "https://www.youtube.com/"
    return
  fi

  local chrome_bin
  chrome_bin="$(find_chrome_bin || true)"
  if [[ -z "$chrome_bin" ]]; then
    printf '[error] Google Chrome or Chromium was not found\n' >&2
    return 1
  fi

  "$chrome_bin" \
    "--user-data-dir=$CHROME_PROFILE_DIR" \
    "--remote-debugging-port=$CHROME_DEBUG_PORT" \
    "--no-first-run" \
    "https://accounts.google.com/" \
    "https://www.youtube.com/" >/dev/null 2>&1 &
}

wait_for_login() {
  printf '[login] finish Google/YouTube login in Chrome, then press Enter here to continue.'
  IFS= read -r _
  printf '\n'
}

ensure_login_source() {
  if cookie_file_has_content || [[ -d "$CHROME_PROFILE_DIR" ]]; then
    return
  fi

  printf '[login] no cookies.txt content or saved Chrome profile found; opening Chrome login.\n'
  open_login_browser
  wait_for_login
}

build_js_args() {
  if command -v node >/dev/null 2>&1; then
    printf '%s\n%s\n%s\n%s\n' '--js-runtimes' "node:$(command -v node)" '--remote-components' 'ejs:github'
    return
  fi

  if command -v deno >/dev/null 2>&1; then
    printf '%s\n%s\n%s\n%s\n' '--js-runtimes' "deno:$(command -v deno)" '--remote-components' 'ejs:github'
    return
  fi

  printf '[warn] no node or deno found; YouTube challenge solving may fail\n' >&2
  printf '%s\n%s\n' '--remote-components' 'ejs:github'
}

case "$MODE" in
  login)
    open_login_browser
    exit 0
    ;;
  login-download)
    open_login_browser
    wait_for_login
    ;;
  download)
    ensure_login_source
    ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac

CLEAN_LINKS="$RUNTIME_DIR/links.clean.txt"
useful_lines "$LINKS_FILE" > "$CLEAN_LINKS" || true
if [[ ! -s "$CLEAN_LINKS" ]]; then
  printf '[error] links.txt has no URLs\n' >&2
  exit 1
fi

YTDLP_BIN="$(ensure_ytdlp)"
FFMPEG_ARGS=()
if command -v ffmpeg >/dev/null 2>&1; then
  FFMPEG_ARGS=(--ffmpeg-location "$(dirname -- "$(command -v ffmpeg)")")
elif [[ -x "$BIN_DIR/ffmpeg" ]]; then
  FFMPEG_ARGS=(--ffmpeg-location "$BIN_DIR")
else
  printf '[warn] ffmpeg not found; merged downloads may fail\n' >&2
fi

COOKIE_ARGS=()
while IFS= read -r arg; do
  COOKIE_ARGS+=("$arg")
done < <(build_cookie_args)

JS_ARGS=()
while IFS= read -r arg; do
  JS_ARGS+=("$arg")
done < <(build_js_args)

printf '[run] downloading to %s\n' "$OUTPUT_DIR"
YTDLP_ARGS=()
if ((${#COOKIE_ARGS[@]})); then
  YTDLP_ARGS+=("${COOKIE_ARGS[@]}")
fi
YTDLP_ARGS+=("${JS_ARGS[@]}")
if ((${#FFMPEG_ARGS[@]})); then
  YTDLP_ARGS+=("${FFMPEG_ARGS[@]}")
fi
YTDLP_ARGS+=(
  -f "bestvideo+bestaudio/best"
  --merge-output-format mkv
  -a "$CLEAN_LINKS"
  -o "$OUTPUT_DIR/%(id)s.%(ext)s"
)

"$YTDLP_BIN" "${YTDLP_ARGS[@]}"
