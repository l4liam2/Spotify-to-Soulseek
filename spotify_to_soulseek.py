#!/usr/bin/env python3
"""
spotify_to_soulseek.py — paste a Spotify playlist URL, download its tracks
via sldl (slsk-batchdl).

Just run it:
    python3 spotify_to_soulseek.py

It will prompt for the playlist URL. On first run, it also prompts for your
Soulseek username/password and saves them to config.json (mode 0600) next to
the script. To override defaults inline:
    python3 spotify_to_soulseek.py <url> [--out ~/Music/Soulseek] [--dry-run]

Requires `sldl` on PATH. Install:
    curl -L https://github.com/fiso64/slsk-batchdl/releases/latest/download/sldl_osx-x64.zip -o /tmp/sldl.zip \\
        && unzip -o /tmp/sldl.zip -d /usr/local/bin/ && chmod +x /usr/local/bin/sldl

Limitation: the public embed endpoint usually returns the first ~50 tracks of
a playlist. For longer playlists, use the Spotify Web API instead.
"""

import argparse
import csv
import getpass
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"

PLAYLIST_ID_RE = re.compile(r"playlist[/:]([A-Za-z0-9]+)")
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', re.DOTALL
)


def extract_playlist_id(url: str) -> str:
    m = PLAYLIST_ID_RE.search(url)
    if not m:
        sys.exit(f"Could not find a playlist id in: {url}")
    return m.group(1)


def fetch_tracks(playlist_id: str) -> list[dict]:
    url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    m = NEXT_DATA_RE.search(html)
    if not m:
        sys.exit("Spotify embed page did not contain __NEXT_DATA__ — layout may have changed.")
    data = json.loads(m.group(1))

    track_list = _find_key(data, "trackList")
    if not track_list:
        sys.exit("No trackList found in embed payload — is the playlist public?")

    tracks = []
    for t in track_list:
        title = t.get("title")
        artist = t.get("subtitle")
        if not title or not artist:
            continue
        primary_artist = artist.split(",")[0].strip()
        tracks.append({"title": title, "artist": primary_artist})
    return tracks


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


def load_or_create_config() -> dict:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open() as f:
            return json.load(f)

    print(f"No config found at {CONFIG_PATH}. Let's set one up.")
    user = input("Soulseek username: ").strip()
    password = getpass.getpass("Soulseek password: ")
    out_dir = input("Download directory [~/Music/Soulseek]: ").strip() or "~/Music/Soulseek"
    cfg = {"user": user, "password": password, "out": out_dir}
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    print(f"Saved to {CONFIG_PATH} (mode 0600).")
    return cfg


def write_csv(tracks: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Title", "Artist"])
        for t in tracks:
            w.writerow([t["title"], t["artist"]])


def run_sldl(csv_path: Path, user: str, password: str, out_dir: Path, extra: list[str]) -> int:
    if not shutil.which("sldl"):
        sys.exit("sldl not found on PATH. See install steps at the top of this script.")
    cmd = [
        "sldl", str(csv_path),
        "--user", user,
        "--pass", password,
        "--path", str(out_dir),
        "--input-type", "csv",
        *extra,
    ]
    print(f"Running: sldl {csv_path} --user {user} --pass *** --path {out_dir} --input-type csv {' '.join(extra)}")
    return subprocess.call(cmd)


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("url", nargs="?", help="Public Spotify playlist URL (prompted if omitted)")
    p.add_argument("--out", help="Output directory (overrides config)")
    p.add_argument("--dry-run", action="store_true", help="Print tracks and exit; don't call sldl")
    args, extra = p.parse_known_args()

    url = args.url or input("Spotify playlist URL: ").strip()
    if not url:
        sys.exit("No URL provided.")

    playlist_id = extract_playlist_id(url)
    print(f"Playlist id: {playlist_id}")

    tracks = fetch_tracks(playlist_id)
    print(f"Found {len(tracks)} tracks:")
    for t in tracks:
        print(f"  {t['artist']} — {t['title']}")

    if args.dry_run:
        return

    cfg = load_or_create_config()
    out_dir = Path(args.out or cfg.get("out", "~/Music/Soulseek")).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tmp:
        csv_path = Path(tmp.name)
    write_csv(tracks, csv_path)

    rc = run_sldl(csv_path, cfg["user"], cfg["password"], out_dir, extra)
    sys.exit(rc)


if __name__ == "__main__":
    main()
