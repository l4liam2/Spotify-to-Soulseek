#!/usr/bin/env python3
"""
spotify_to_soulseek.py — Textual TUI for downloading a Spotify playlist via sldl.

Run:
    python3 spotify_to_soulseek.py        # launches TUI (default)
    python3 spotify_to_soulseek.py --cli  # legacy non-interactive CLI
    python3 spotify_to_soulseek.py --dry-run <url>

Requires `sldl` on PATH and the `textual` package:
    pip install -r requirements.txt
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import csv
import getpass
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import urllib.request
import weakref
from dataclasses import dataclass, field, asdict
from pathlib import Path


# Track every sldl subprocess so we can guarantee they die when we do.
_LIVE_PROCS: "weakref.WeakSet[subprocess.Popen]" = weakref.WeakSet()


def _kill_all_children(_signum: int | None = None, _frame=None) -> None:
    """Terminate all tracked sldl subprocesses. Idempotent."""
    for proc in list(_LIVE_PROCS):
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
    # Give them a moment, then SIGKILL anything still alive.
    for proc in list(_LIVE_PROCS):
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    if _signum is not None:
        # Re-raise the signal so the process exits with the conventional code.
        signal.signal(_signum, signal.SIG_DFL)
        os.kill(os.getpid(), _signum)


atexit.register(_kill_all_children)
for _sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
    try:
        signal.signal(_sig, _kill_all_children)
    except (ValueError, OSError):
        pass  # Some signals can't be installed in non-main threads / Windows


# ---------------------------------------------------------------------------
# Core helpers (shared between CLI and TUI)
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
STATE_PATH = SCRIPT_DIR / "state.json"

PLAYLIST_ID_RE = re.compile(r"playlist[/:]([A-Za-z0-9]+)")
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', re.DOTALL
)


@dataclass
class Track:
    title: str
    artist: str


@dataclass
class State:
    """Persisted between sessions for resume support."""
    url: str = ""
    playlist_id: str = ""
    tracks: list[dict] = field(default_factory=list)
    selected: list[int] = field(default_factory=list)  # indices that were last selected
    out_dir: str = ""

    @classmethod
    def load(cls) -> "State | None":
        if not STATE_PATH.exists():
            return None
        try:
            return cls(**json.loads(STATE_PATH.read_text()))
        except Exception:
            return None

    def save(self) -> None:
        STATE_PATH.write_text(json.dumps(asdict(self), indent=2))


def extract_playlist_id(url: str) -> str:
    m = PLAYLIST_ID_RE.search(url)
    if not m:
        raise ValueError(f"Could not find a playlist id in: {url}")
    return m.group(1)


def fetch_tracks(playlist_id: str) -> list[Track]:
    url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    m = NEXT_DATA_RE.search(html)
    if not m:
        raise RuntimeError("Spotify embed page didn't contain __NEXT_DATA__.")
    data = json.loads(m.group(1))

    track_list = _find_key(data, "trackList")
    if not track_list:
        raise RuntimeError("No trackList found — is the playlist public?")

    tracks: list[Track] = []
    for t in track_list:
        title = t.get("title")
        artist = t.get("subtitle")
        if not title or not artist:
            continue
        tracks.append(Track(title=title, artist=artist.split(",")[0].strip()))
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


def load_or_create_config(interactive: bool = True) -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    if not interactive:
        raise RuntimeError("Missing config.json and not in interactive mode.")
    print(f"No config found at {CONFIG_PATH}. Let's set one up.")
    user = input("Soulseek username: ").strip()
    password = getpass.getpass("Soulseek password: ")
    out_dir = input("Download directory [~/Music/Soulseek]: ").strip() or "~/Music/Soulseek"
    cfg = {"user": user, "password": password, "out": out_dir}
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)
    print(f"Saved to {CONFIG_PATH} (mode 0600).")
    return cfg


def write_csv(tracks: list[Track], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Title", "Artist"])
        for t in tracks:
            w.writerow([t.title, t.artist])


def build_sldl_cmd(csv_path: Path, cfg: dict, out_dir: Path, extra: list[str]) -> list[str]:
    if not shutil.which("sldl"):
        raise RuntimeError("sldl not found on PATH. See README for install steps.")
    return [
        "sldl", str(csv_path),
        "--user", cfg["user"],
        "--pass", cfg["password"],
        "--path", str(out_dir),
        "--input-type", "csv",
        *extra,
    ]


# ---------------------------------------------------------------------------
# Legacy CLI path
# ---------------------------------------------------------------------------

def run_cli(args) -> int:
    url = args.url or input("Spotify playlist URL: ").strip()
    if not url:
        print("No URL provided.")
        return 1
    pid = extract_playlist_id(url)
    print(f"Playlist id: {pid}")
    tracks = fetch_tracks(pid)
    print(f"Found {len(tracks)} tracks:")
    for t in tracks:
        print(f"  {t.artist} — {t.title}")
    if args.dry_run:
        return 0
    cfg = load_or_create_config(interactive=True)
    out_dir = Path(args.out or cfg.get("out", "~/Music/Soulseek")).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tmp:
        csv_path = Path(tmp.name)
    write_csv(tracks, csv_path)
    cmd = build_sldl_cmd(csv_path, cfg, out_dir, [])
    return subprocess.call(cmd)


# ---------------------------------------------------------------------------
# Textual TUI
# ---------------------------------------------------------------------------

def run_tui() -> int:
    try:
        from textual.app import App, ComposeResult
        from textual.binding import Binding
        from textual.containers import Horizontal, Vertical, VerticalScroll
        from textual.screen import Screen
        from textual.widgets import (
            Button, Checkbox, Footer, Header, Input, Label, ProgressBar,
            RichLog, Static,
        )
        from textual import work
    except ImportError:
        print("Textual not installed. Run: pip install -r requirements.txt")
        return 1

    APP_CSS = """
    Screen { background: $surface; }
    #title { content-align: center middle; text-style: bold; color: $accent; padding: 1; }
    #subtitle { content-align: center middle; color: $text-muted; padding-bottom: 1; }
    .panel { border: round $primary; padding: 1 2; margin: 1 2; }
    .row { height: auto; }
    Button { margin: 0 1; }
    #url_input { margin: 1 0; }
    #track_list { height: 1fr; border: round $primary; padding: 1; margin: 1 2; }
    Checkbox { padding: 0 1; }
    #log { height: 1fr; border: round $primary; margin: 1 2; padding: 1; }
    .status { color: $text-muted; padding: 0 2; }
    .ok { color: $success; }
    .err { color: $error; }
    #progress_box { height: auto; border: round $primary; margin: 1 2; padding: 0 1; }
    #progress { width: 100%; padding: 0 1; }
    #counters { content-align: center middle; padding: 0 1; }
    #active { content-align: left middle; color: $text-muted; padding: 0 1; }
    """

    # ---- URL screen ------------------------------------------------------
    class URLScreen(Screen):
        BINDINGS = [
            Binding("enter", "fetch", "Fetch playlist"),
            Binding("q", "app.quit", "Quit"),
        ]

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield Static("Spotify → Soulseek", id="title")
            yield Static("Paste a public Spotify playlist URL to begin", id="subtitle")
            with Vertical(classes="panel"):
                yield Label("Playlist URL")
                yield Input(
                    placeholder="https://open.spotify.com/playlist/...",
                    id="url_input",
                )
                with Horizontal(classes="row"):
                    yield Button("Fetch tracks", id="fetch", variant="primary")
                    if STATE_PATH.exists():
                        yield Button("Resume last session", id="resume")
                    yield Button("Settings", id="settings")
                    yield Button("Quit", id="quit", variant="error")
                yield Static("", id="msg", classes="status")
            yield Footer()

        def on_mount(self) -> None:
            self.query_one("#url_input", Input).focus()
            state = State.load()
            if state and state.url:
                self.query_one("#url_input", Input).value = state.url

        def action_fetch(self) -> None:
            self._fetch()

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "fetch":
                self._fetch()
            elif event.button.id == "resume":
                state = State.load()
                if state and state.tracks:
                    self.app.push_screen(TrackScreen(
                        url=state.url,
                        tracks=[Track(**t) for t in state.tracks],
                        preselect=set(state.selected),
                    ))
            elif event.button.id == "settings":
                self.app.push_screen(SettingsScreen())
            elif event.button.id == "quit":
                self.app.exit()

        @work(exclusive=True, thread=True)
        def _fetch(self) -> None:
            url = self.query_one("#url_input", Input).value.strip()
            msg = self.query_one("#msg", Static)
            if not url:
                self.app.call_from_thread(msg.update, "[err]Please paste a URL.[/]")
                return
            self.app.call_from_thread(msg.update, "Fetching…")
            try:
                pid = extract_playlist_id(url)
                tracks = fetch_tracks(pid)
            except Exception as exc:
                self.app.call_from_thread(msg.update, f"[err]Error: {exc}[/]")
                return

            # Screen must be constructed on the UI thread (Python 3.9 needs a
            # running event loop to allocate the asyncio.Lock inside Widget).
            def _push() -> None:
                self.app.push_screen(TrackScreen(url=url, tracks=tracks))
                msg.update(f"Found {len(tracks)} tracks.")

            self.app.call_from_thread(_push)

    # ---- Track selection screen ------------------------------------------
    class TrackScreen(Screen):
        BINDINGS = [
            Binding("a", "select_all", "Select all"),
            Binding("n", "select_none", "Select none"),
            Binding("d", "download", "Download"),
            Binding("escape", "app.pop_screen", "Back"),
        ]

        def __init__(self, url: str, tracks: list[Track], preselect: set[int] | None = None):
            super().__init__()
            self.url = url
            self.tracks = tracks
            self.preselect = preselect  # None → all selected

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield Static(f"Select tracks to download ({len(self.tracks)} found)", id="title")
            yield Static("Press A=all  N=none  D=download  Esc=back", id="subtitle")
            with VerticalScroll(id="track_list"):
                for i, t in enumerate(self.tracks):
                    checked = (self.preselect is None) or (i in self.preselect)
                    yield Checkbox(f"{t.artist} — {t.title}", value=checked, id=f"t{i}")
            with Horizontal(classes="panel"):
                yield Button("Select all", id="all")
                yield Button("Select none", id="none")
                yield Button("Download selected", id="go", variant="success")
                yield Button("Back", id="back")
            yield Static("", id="msg", classes="status")
            yield Footer()

        def _selected(self) -> list[int]:
            return [i for i, _ in enumerate(self.tracks)
                    if self.query_one(f"#t{i}", Checkbox).value]

        def _set_all(self, value: bool) -> None:
            for i in range(len(self.tracks)):
                self.query_one(f"#t{i}", Checkbox).value = value

        def action_select_all(self) -> None: self._set_all(True)
        def action_select_none(self) -> None: self._set_all(False)
        def action_download(self) -> None: self._go()

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "all":   self._set_all(True)
            elif event.button.id == "none": self._set_all(False)
            elif event.button.id == "back": self.app.pop_screen()
            elif event.button.id == "go":   self._go()

        def _go(self) -> None:
            sel = self._selected()
            if not sel:
                self.query_one("#msg", Static).update("[err]No tracks selected.[/]")
                return
            picked = [self.tracks[i] for i in sel]
            try:
                cfg = load_or_create_config(interactive=False)
            except Exception:
                self.query_one("#msg", Static).update(
                    "[err]No config.json — open Settings first.[/]")
                return
            # Persist for resume
            state = State(
                url=self.url,
                playlist_id=extract_playlist_id(self.url),
                tracks=[asdict(t) for t in self.tracks],
                selected=sel,
                out_dir=cfg.get("out", "~/Music/Soulseek"),
            )
            state.save()
            self.app.push_screen(DownloadScreen(picked, cfg))

    # ---- Download screen --------------------------------------------------
    class DownloadScreen(Screen):
        BINDINGS = [
            Binding("escape", "back", "Back"),
            Binding("r", "retry", "Retry"),
            Binding("y", "retry_yt", "Retry + YouTube"),
            Binding("c", "cancel", "Cancel download"),
        ]

        def __init__(self, tracks: list[Track], cfg: dict, extra: list[str] | None = None):
            super().__init__()
            self.tracks = tracks
            self.cfg = cfg
            self.extra = extra or []
            self.return_code: int | None = None
            self.csv_path: Path | None = None
            self.proc: subprocess.Popen | None = None
            self.cancelled: bool = False
            # Progress state
            self.total = len(tracks)
            self.done = 0
            self.failed = 0
            self.skipped = 0

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield Static(f"Downloading {len(self.tracks)} tracks…", id="title")
            yield Static("Press C to cancel. Already-downloaded tracks are skipped.", id="subtitle")
            with Vertical(id="progress_box"):
                yield ProgressBar(total=self.total, id="progress", show_eta=False)
                yield Static(self._counters_text(), id="counters")
                yield Static("", id="active")
            yield RichLog(id="log", highlight=True, markup=True, wrap=True)
            with Horizontal(classes="panel"):
                yield Button("Cancel (kill sldl)", id="cancel", variant="error")
                yield Button("Retry (skips done)", id="retry", disabled=True)
                yield Button("Retry + YouTube fallback", id="retry_yt", disabled=True)
                yield Button("Back to start", id="home", disabled=True)
            yield Static("", id="status", classes="status")
            yield Footer()

        # --- progress helpers -------------------------------------------
        def _counters_text(self) -> str:
            processed = self.done + self.failed + self.skipped
            return (
                f"[b]{processed}/{self.total}[/]   "
                f"[ok]✓ {self.done}[/]  "
                f"[err]✕ {self.failed}[/]  "
                f"[dim]↻ {self.skipped} skipped[/]"
            )

        def _tick(self, kind: str) -> None:
            """Called on the main thread when a track reaches a final state."""
            if kind == "done":
                self.done += 1
            elif kind == "failed":
                self.failed += 1
            elif kind == "skipped":
                self.skipped += 1
            processed = self.done + self.failed + self.skipped
            try:
                self.query_one("#progress", ProgressBar).update(progress=processed)
                self.query_one("#counters", Static).update(self._counters_text())
            except Exception:
                pass  # widget may have been removed

        def _set_active(self, label: str) -> None:
            try:
                self.query_one("#active", Static).update(f"▸ {label}")
            except Exception:
                pass

        @staticmethod
        def _classify_line(line: str) -> str | None:
            """Return 'done' | 'failed' | 'skipped' | None for a sldl output line."""
            low = line.lower()
            # Success: 'succeeded:', 'downloaded', or a saved-path arrow
            if (
                "succeeded:" in low
                or "✓" in line
                or " downloaded:" in low
                or "→ saved" in low
                or low.startswith("downloaded ")
            ):
                return "done"
            # Skipped already-existing tracks
            if (
                "already exists" in low
                or low.startswith("skipped")
                or "skipping " in low
            ):
                return "skipped"
            # Failures and not-found
            if (
                "failed:" in low
                or "no results" in low
                or "no users have" in low
                or "not found" in low
                or "couldn't find" in low
            ):
                return "failed"
            return None

        def on_mount(self) -> None:
            self._start()

        @work(exclusive=True, thread=True)
        def _start(self) -> None:
            log = self.query_one("#log", RichLog)
            status = self.query_one("#status", Static)

            out_dir = Path(self.cfg.get("out", "~/Music/Soulseek")).expanduser()
            out_dir.mkdir(parents=True, exist_ok=True)

            with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tmp:
                self.csv_path = Path(tmp.name)
            write_csv(self.tracks, self.csv_path)

            try:
                cmd = build_sldl_cmd(self.csv_path, self.cfg, out_dir, self.extra)
            except RuntimeError as exc:
                self.app.call_from_thread(log.write, f"[err]{exc}[/]")
                self.app.call_from_thread(self._finish, 1)
                return

            self.app.call_from_thread(
                log.write,
                f"[dim]sldl {self.csv_path}  →  {out_dir}[/]",
            )
            self.app.call_from_thread(status.update, "Running sldl…")

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except FileNotFoundError:
                self.app.call_from_thread(log.write, "[err]sldl not found[/]")
                self.app.call_from_thread(self._finish, 127)
                return

            self.proc = proc
            _LIVE_PROCS.add(proc)

            assert proc.stdout is not None
            active_re = re.compile(
                r"^(?:searching|downloading|trying|now downloading)[: ]+(.+)$",
                re.IGNORECASE,
            )
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue

                # Update progress counters when a track reaches a terminal state.
                kind = self._classify_line(line)
                if kind:
                    self.app.call_from_thread(self._tick, kind)

                # Track the currently-active item (best-effort).
                m = active_re.match(line)
                if m:
                    self.app.call_from_thread(self._set_active, m.group(1).strip())

                # Style the log line.
                styled = line
                if kind == "done":
                    styled = f"[ok]{line}[/]"
                elif kind == "failed":
                    styled = f"[err]{line}[/]"
                elif kind == "skipped":
                    styled = f"[dim]{line}[/]"
                self.app.call_from_thread(log.write, styled)
            proc.wait()
            self.app.call_from_thread(self._finish, proc.returncode)

        def _finish(self, rc: int) -> None:
            self.return_code = rc
            status = self.query_one("#status", Static)
            if self.cancelled:
                status.update("[err]✕ Cancelled.[/]")
            elif rc == 0:
                status.update("[ok]✓ Done.[/]")
            else:
                status.update(f"[err]sldl exited with code {rc}.[/] Press R to retry.")
            self.query_one("#cancel", Button).disabled = True
            for btn_id in ("retry", "retry_yt", "home"):
                self.query_one(f"#{btn_id}", Button).disabled = False

        def action_retry(self) -> None: self._retry([])
        def action_retry_yt(self) -> None: self._retry(["--yt-dlp"])

        def action_cancel(self) -> None:
            self._cancel()

        def action_back(self) -> None:
            self._cancel()
            self.app.pop_screen()

        def _cancel(self) -> None:
            """Hard-stop the sldl subprocess. Safe to call repeatedly."""
            if self.cancelled:
                return
            self.cancelled = True
            proc = self.proc
            log = self.query_one("#log", RichLog)
            if proc is None or proc.poll() is not None:
                log.write("[err]Nothing to cancel.[/]")
                return
            log.write("[err]Cancelling — sending SIGTERM to sldl…[/]")
            try:
                proc.terminate()
            except Exception as exc:
                log.write(f"[err]terminate() failed: {exc}[/]")
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                log.write("[err]sldl ignored SIGTERM — sending SIGKILL.[/]")
                try:
                    proc.kill()
                except Exception as exc:
                    log.write(f"[err]kill() failed: {exc}[/]")

        def _retry(self, extra: list[str]) -> None:
            self.app.pop_screen()
            self.app.push_screen(DownloadScreen(self.tracks, self.cfg, self.extra + extra))

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "retry": self._retry([])
            elif event.button.id == "retry_yt": self._retry(["--yt-dlp"])
            elif event.button.id == "cancel": self._cancel()
            elif event.button.id == "home":
                self._cancel()
                while len(self.app.screen_stack) > 1:
                    self.app.pop_screen()

    # ---- Settings screen --------------------------------------------------
    class SettingsScreen(Screen):
        BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

        def compose(self) -> ComposeResult:
            cfg = {}
            if CONFIG_PATH.exists():
                cfg = json.loads(CONFIG_PATH.read_text())
            yield Header(show_clock=False)
            yield Static("Settings", id="title")
            yield Static("Stored at config.json (mode 0600, gitignored)", id="subtitle")
            with Vertical(classes="panel"):
                yield Label("Soulseek username")
                yield Input(value=cfg.get("user", ""), id="user")
                yield Label("Soulseek password")
                yield Input(value=cfg.get("password", ""), password=True, id="password")
                yield Label("Download directory")
                yield Input(value=cfg.get("out", "~/Music/Soulseek"), id="out")
                with Horizontal(classes="row"):
                    yield Button("Save", id="save", variant="primary")
                    yield Button("Cancel", id="cancel")
                yield Static("", id="msg", classes="status")
            yield Footer()

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "save":
                cfg = {
                    "user": self.query_one("#user", Input).value.strip(),
                    "password": self.query_one("#password", Input).value,
                    "out": self.query_one("#out", Input).value.strip() or "~/Music/Soulseek",
                }
                if not cfg["user"] or not cfg["password"]:
                    self.query_one("#msg", Static).update(
                        "[err]Username and password are required.[/]")
                    return
                CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
                os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)
                self.query_one("#msg", Static).update("[ok]Saved.[/]")
            elif event.button.id == "cancel":
                self.app.pop_screen()

    # ---- App --------------------------------------------------------------
    class S2SApp(App):
        CSS = APP_CSS
        TITLE = "Spotify → Soulseek"
        BINDINGS = [Binding("ctrl+q", "quit", "Quit")]

        def on_mount(self) -> None:
            # First run? Force settings before anything else.
            if not CONFIG_PATH.exists():
                self.push_screen(URLScreen())
                self.push_screen(SettingsScreen())
            else:
                self.push_screen(URLScreen())

    S2SApp().run()
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url", nargs="?", help="Playlist URL (CLI mode only)")
    p.add_argument("--cli", action="store_true", help="Use the original non-interactive CLI")
    p.add_argument("--dry-run", action="store_true", help="Print track list and exit (CLI)")
    p.add_argument("--out", help="Override output directory")
    args, _ = p.parse_known_args()

    if args.cli or args.dry_run or args.url:
        sys.exit(run_cli(args))
    sys.exit(run_tui())


if __name__ == "__main__":
    main()
