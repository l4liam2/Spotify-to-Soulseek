# Spotify-to-Soulseek

Paste a Spotify playlist URL, get the songs downloaded from Soulseek.

A small Python wrapper around [`sldl` (slsk-batchdl)](https://github.com/fiso64/slsk-batchdl). It pulls the track list from a public Spotify playlist (no Spotify API key needed) and hands the list to `sldl` to download from the Soulseek network.

## Requirements

- macOS or Linux
- Python 3.9+ (stdlib only, no `pip install` needed)
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

2. (Optional) Create a Soulseek account — there is no signup form. If you log in with a username that doesn't exist on the network, it's auto-created. So just pick any username/password on first run.

## Usage

### Quick start

```bash
python3 spotify_to_soulseek.py
```

You'll be prompted for:

| Prompt | What to enter |
|---|---|
| Spotify playlist URL | A public playlist link, e.g. `https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M` |
| Soulseek username | Pick anything (e.g. your name + a number). If new, the account is created automatically. |
| Soulseek password | Pick anything. Save it somewhere — there is no password reset on Soulseek. |
| Download directory | Press Enter for the default (`~/Music/Soulseek`) |

After the first run, your Soulseek credentials and download path are saved to `config.json` next to the script (mode `0600`). You won't be asked again. `config.json` is `.gitignore`d.

### Subsequent runs

Just run it again — you'll only be asked for the playlist URL:

```bash
python3 spotify_to_soulseek.py
```

### Pass the URL inline

Skip the prompt by passing the URL as an argument:

```bash
python3 spotify_to_soulseek.py "https://open.spotify.com/playlist/<id>"
```

### Preview without downloading

`--dry-run` prints the parsed track list and exits without calling `sldl`. Useful for checking the playlist parses correctly:

```bash
python3 spotify_to_soulseek.py --dry-run
```

### Custom output directory

Override the saved default for one run:

```bash
python3 spotify_to_soulseek.py --out ~/Desktop/new-music
```

### Pass extra flags through to `sldl`

Any unrecognized arguments are forwarded to `sldl`. Useful examples:

```bash
# Prefer FLAC, fall back to MP3
python3 spotify_to_soulseek.py --pref-format flac,mp3

# Minimum 320 kbps bitrate
python3 spotify_to_soulseek.py --min-bitrate 320

# Run 4 downloads in parallel instead of 2
python3 spotify_to_soulseek.py --concurrent-downloads 4

# Verbose sldl logging
python3 spotify_to_soulseek.py -v
```

Run `sldl --help` to see every available flag.

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
