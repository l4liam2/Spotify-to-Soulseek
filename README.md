# Spotify-to-Soulseek

Paste a Spotify playlist URL, get the songs downloaded from Soulseek.

A small Python wrapper around [`sldl` (slsk-batchdl)](https://github.com/fiso64/slsk-batchdl). It pulls the track list from a public Spotify playlist (no Spotify API key needed) and hands the list to `sldl` to download from the Soulseek network.

## Requirements

- macOS or Linux
- Python 3.9+
- `pip install -r requirements.txt` (installs [`textual`](https://github.com/Textualize/textual) for the TUI)
- [`sldl`](https://github.com/fiso64/slsk-batchdl) on your `PATH`
- A Soulseek account (created automatically on first login — see below)

## Installing `sldl`

### macOS (Apple Silicon)

```bash
curl -L https://github.com/fiso64/sldl/releases/latest/download/sldl_osx-arm64.zip -o /tmp/sldl.zip
unzip -o /tmp/sldl.zip -d /tmp/sldl
sudo mv /tmp/sldl/sldl /usr/local/bin/sldl
sudo chmod +x /usr/local/bin/sldl
sudo xattr -cr /usr/local/bin/sldl
sudo codesign --force --deep --sign - /usr/local/bin/sldl
sldl --help | head -5
```

The `xattr` and `codesign` lines are needed because macOS Gatekeeper blocks unsigned binaries by default. If `sldl --help` prints help text, you're good.

### macOS (Intel)

Same as above but replace the URL with:
```
https://github.com/fiso64/sldl/releases/latest/download/sldl_osx-x64.zip
```

Not sure which Mac you have? Run `uname -m` — `arm64` is Apple Silicon, `x86_64` is Intel.

### Linux

```bash
curl -L https://github.com/fiso64/sldl/releases/latest/download/sldl_linux-x64.zip -o /tmp/sldl.zip
unzip -o /tmp/sldl.zip -d /tmp/sldl
sudo mv /tmp/sldl/sldl /usr/local/bin/sldl
sudo chmod +x /usr/local/bin/sldl
```

## Setup

1. Clone this repo:
   ```bash
   git clone https://github.com/l4liam2/Spotify-to-Soulseek.git
   cd Spotify-to-Soulseek
   ```

2. Install the Python dependency (just `textual`):
   ```bash
   pip3 install -r requirements.txt
   ```

3. (Optional) Create a Soulseek account — there is no signup form. If you log in with a username that doesn't exist on the network, it's auto-created. So just pick any username/password on first run.

## Usage

### Launch the TUI (default)

```bash
python3 spotify_to_soulseek.py
```

A full-screen terminal app opens. The flow:

1. **Settings** (first run only) — enter your Soulseek username, password, and download folder. Saved to `config.json` (mode `0600`, gitignored). Soulseek auto-creates accounts on first login, so any username + password works.
2. **URL screen** — paste a public Spotify playlist URL. Press **Enter** or click *Fetch tracks*. If you've used the app before, the URL field pre-fills with your last playlist and a *Resume last session* button appears.
3. **Track selection** — every track is shown as a checkbox, all selected by default. Use:
   - `A` — select all
   - `N` — deselect all
   - click any checkbox to toggle
   - `D` — start downloading
   - `Esc` — back to the URL screen
4. **Download screen** — live colored log of `sldl` output, with success lines in green and failures in red. Skipped (already-downloaded) tracks are noted.
5. **While downloading** — press `C` (or click **Cancel**) to stop `sldl` immediately. SIGTERM is sent first; if `sldl` doesn't quit within 3 seconds, SIGKILL is sent.
6. **After it finishes (or you cancel)** — two retry options appear:
   - `R` — **Retry**: re-run sldl on the same selection. Already-downloaded tracks are skipped automatically, so this only re-attempts misses.
   - `Y` — **Retry + YouTube fallback**: same as above but adds `--yt-dlp` so tracks not found on Soulseek fall back to YouTube. Requires [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) installed (`brew install yt-dlp`).

Press **Ctrl+Q** anytime to quit — any running `sldl` is killed as part of shutdown. Closing the terminal window or `kill`ing the Python process will also terminate `sldl` (signal handlers cover SIGTERM/SIGHUP/SIGINT).

### Legacy CLI mode

The original non-interactive flow still works for scripting:

```bash
# Pass URL as argument → uses CLI mode
python3 spotify_to_soulseek.py "https://open.spotify.com/playlist/<id>"

# Force CLI even without URL (prompts for one)
python3 spotify_to_soulseek.py --cli

# Preview tracks without downloading
python3 spotify_to_soulseek.py --dry-run "https://open.spotify.com/playlist/<id>"

# Override output directory
python3 spotify_to_soulseek.py --out ~/Desktop/new-music "<url>"
```

### Where do my settings live?

| File | What's in it |
|---|---|
| `config.json` | Soulseek username/password + default download folder. Mode `0600`. Gitignored. Edit via the Settings screen. |
| `state.json` | Last playlist URL, fetched track list, and which tracks you selected — used to power *Resume last session*. Gitignored. Delete it to start fresh. |

## Where do the files go?

By default: `~/Music/Soulseek/`. Open Finder, press **Cmd+Shift+G**, type `~/Music/Soulseek`, hit Enter.

Each track is saved as `<Artist> - <Title>.<ext>`. Tracks that couldn't be found on Soulseek are skipped (you'll see them in `sldl`'s output) and logged in the `_index.sldl` file in the output folder.

## Troubleshooting

**`sldl not found on PATH`** — `sldl` isn't installed or isn't in a directory your shell searches. Verify with `which sldl`. If empty, redo the install steps above.

**`zsh: killed sldl`** — macOS Gatekeeper killed an unsigned binary. Fix:
```bash
sudo xattr -cr /usr/local/bin/sldl
sudo codesign --force --deep --sign - /usr/local/bin/sldl
```

**`Input error: No soulseek username or password provided for login.`** — your `config.json` has an empty password. Delete it and re-run:
```bash
rm config.json
python3 spotify_to_soulseek.py
```
Type the password carefully when prompted — `getpass` hides what you type, that's normal.

**`Spotify embed page did not contain __NEXT_DATA__`** — Spotify changed their page layout, or the playlist is private. Make sure the playlist is set to public on Spotify (right-click playlist → Share → "Anyone with link can view").

**Only 50 tracks found in a longer playlist** — known limitation. The public embed endpoint caps at ~50 tracks. For longer playlists you'd need to switch to the official Spotify Web API (open an issue if you want this).

**Many tracks fail to download** — normal on Soulseek. Tracks rely on other users having the file *and* being online. Re-run the script later (already-downloaded tracks are skipped) or pair with `--yt-dlp` to fall back to YouTube:
```bash
python3 spotify_to_soulseek.py --yt-dlp
```
(requires `yt-dlp` installed: `brew install yt-dlp`).

## How it works

1. Parses the playlist ID out of the URL.
2. Fetches `https://open.spotify.com/embed/playlist/<id>` (a public, no-auth endpoint) and extracts the embedded `__NEXT_DATA__` JSON.
3. Walks the JSON to find the `trackList` and reads each track's `title` (song) and `subtitle` (primary artist).
4. Writes a CSV with `Title,Artist` columns to a temp file.
5. Invokes `sldl <csvfile> --user … --pass … --path … --input-type csv` — `sldl` handles Soulseek search, peer selection, and downloads.

## Legal

Downloading copyrighted music from Soulseek without permission may be illegal in your jurisdiction. This project is for personal/educational use. Don't be a jerk.
