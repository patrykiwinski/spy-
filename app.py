import os
import time
import random
import json
import logging
from pathlib import Path
from typing import Optional, Tuple, List, Dict
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configure SocketIO async_mode
# On Render with gunicorn+eventlet, 'threading' mode works fine
# Can be overridden via SOCKETIO_ASYNC_MODE env var if needed
async_mode = os.environ.get('SOCKETIO_ASYNC_MODE', 'threading')
socketio = SocketIO(app, async_mode=async_mode, cors_allowed_origins="*")
logger.info(f"SocketIO initialized with async_mode: {async_mode}")

# Constants
DEFAULT_TIMER_SECONDS = 30
MIN_TIMER_SECONDS = 5
MAX_TIMER_SECONDS = 900
MIN_PLAYERS = 2
timer_version = 0

# Game state
TIMER_SECONDS = DEFAULT_TIMER_SECONDS
runda = 0
players: Dict[str, str] = {}  # {sid: name}
game_started = False
host_sid: Optional[str] = None
host_name = ""
stop_flag = False
timer_task = None  # Background task for timer

BASE = Path(__file__).resolve().parent

# Name gender exceptions (for Polish grammar)
MALE_NAMES_ENDING_A = {"kuba", "kosma", "barnaba", "bonawentura", "ezra", "saba", "misha", "sasha"}
FEMALE_NAMES_NOT_ENDING_A = {"miriam", "beatrycze", "ingrid", "ester", "noemi", "rachel", "ruth"}


def get_forma_dolaczyl(name: str) -> str:
    """Determine Polish verb form based on name (dołączył/dołączyła)."""
    name_lower = name.lower()
    if (name_lower.endswith("a") and name_lower not in MALE_NAMES_ENDING_A) or name_lower in FEMALE_NAMES_NOT_ENDING_A:
        return "dołączyła"
    return "dołączył"


def get_forma_zatrzymal(name: str) -> str:
    """Determine Polish verb form based on name (zatrzymał/zatrzymała)."""
    name_lower = name.lower()
    if (name_lower.endswith("a") and name_lower not in MALE_NAMES_ENDING_A) or name_lower in FEMALE_NAMES_NOT_ENDING_A:
        return "zatrzymała"
    return "zatrzymał"



def load_index_and_data() -> Tuple[List[str], Dict[str, List[str]]]:
    """Load categories index and data from JSON files."""
    try:
        idx = json.loads((BASE / "categories_index.json").read_text(encoding="utf-8"))
        data = json.loads((BASE / "categories_data.json").read_text(encoding="utf-8"))
        names = [c for c in idx.get("categories", []) if c in data]
        return names, data
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        logger.error(f"Error loading categories: {e}")
        return [], {}

CATEGORIES_LIST, CATEGORIES_DATA = load_index_and_data()
selected_categories: List[str] = []


# ---------------- TIMER ----------------
def run_timer(seconds: Optional[int] = None) -> None:
    global stop_flag, timer_task, timer_version

    if seconds is None:
        seconds = TIMER_SECONDS

    stop_timer()
    stop_flag = False

    timer_version += 1
    local_version = timer_version

    # NATYCHMIAST reset UI
    socketio.emit("timer", {
        "time": seconds,
        "v": local_version
    })

    def countdown():
        global stop_flag
        for i in range(seconds - 1, -1, -1):
            if stop_flag or local_version != timer_version:
                return
            socketio.emit("timer", {
                "time": i,
                "v": local_version
            })
            time.sleep(1)

        if not stop_flag and local_version == timer_version:
            socketio.emit("end", {"color": "#ffffff"})

    timer_task = socketio.start_background_task(countdown)
    logger.info(f"Timer started: {seconds}s")



def stop_timer() -> None:
    """Stop current timer (if running)."""
    global stop_flag, timer_task
    
    stop_flag = True
    timer_task = None
    logger.info("Timer stopped")


# ---------------- ROUTES ----------------
@app.route("/")
@app.route("/mobi")
def mobi():
    # zwracaj mobilny widok także pod "/"
    return render_template("mobi.html",categories=CATEGORIES_LIST)

@app.route("/preview")
def preview():
    # Preview page for responsive testing
    return render_template("preview.html") if Path("templates/preview.html").exists() else "Preview not available"


# -------------- SOCKET HANDLERS --------------

def pick_category_and_secret() -> Tuple[str, str]:
    """Pick random category and secret word from available categories."""
    global selected_categories, CATEGORIES_LIST, CATEGORIES_DATA

    # Use selected categories if available, otherwise all categories
    pool = selected_categories if selected_categories else CATEGORIES_LIST

    # Filter out non-existent categories
    pool = [c for c in pool if c in CATEGORIES_DATA]
    if not pool:
        pool = CATEGORIES_LIST
        if not pool:
            raise ValueError("No categories available")

    cat = random.choice(pool)
    secrets = CATEGORIES_DATA[cat]
    if not secrets:
        raise ValueError(f"Category '{cat}' has no secrets")
    
    secret = random.choice(secrets)
    return cat, secret


@socketio.on("set_category")
def set_category(data: Dict) -> Dict:
    """Set selected categories (host only, before game starts)."""
    global selected_categories, host_sid, game_started

    # Only host can set categories
    if request.sid != host_sid:
        emit("error_msg", {"msg": "Tylko host może ustawiać kategorie."}, to=request.sid)
        return {}
    
    if game_started:
        emit("error_msg", {"msg": "Nie można zmieniać kategorii w trakcie gry."}, to=request.sid)
        return {}

    cats = (data or {}).get("categories", [])
    if not isinstance(cats, list):
        cats = [cats] if cats else []

    selected_categories = [c for c in cats if c in CATEGORIES_LIST]
    label = ", ".join(selected_categories) if selected_categories else "Losowa"

    logger.info(f"[SET_CATEGORY] {request.sid} -> {selected_categories}")
    socketio.emit("category_update", {"selected": selected_categories, "label": label})
    socketio.emit("info", {"msg": f"Kategoria: {label}"})

    return {"ok": True, "label": label, "selected": selected_categories}

@socketio.on("connect")
def on_connect() -> None:
    """Handle client connection - send current game state."""
    label = ", ".join(selected_categories) if selected_categories else "Losowa"
    emit("category_update", {"selected": selected_categories, "label": label})
    emit("timer_update", {"seconds": TIMER_SECONDS})
    logger.info(f"Client connected: {request.sid}")




    
@socketio.on("set_timer")
def set_timer(data: Dict) -> None:
    """Set timer duration (host only)."""
    global TIMER_SECONDS

    # Only host can change timer
    if request.sid != host_sid:
        emit("error_msg", {"msg": "Tylko host może zmienić czas rundy."}, to=request.sid)
        return

    try:
        secs = int((data or {}).get("seconds", TIMER_SECONDS))
    except (TypeError, ValueError):
        emit("error_msg", {"msg": "Nieprawidłowa liczba sekund."}, to=request.sid)
        return

    secs = max(MIN_TIMER_SECONDS, min(secs, MAX_TIMER_SECONDS))
    TIMER_SECONDS = secs

    logger.info(f"[SET_TIMER] {request.sid} set timer to {TIMER_SECONDS}s")
    socketio.emit("timer_update", {"seconds": TIMER_SECONDS})
    socketio.emit("info", {"msg": f"Czas rundy ustawiony na {TIMER_SECONDS} s"})


@socketio.on("join")
def join_game(data: Dict) -> None:
    """Handle player joining the game."""
    global host_sid, host_name
    
    name = (data.get("id", "Anonim") or "Anonim").strip()
    if not name:
        name = "Anonim"
    
    players[request.sid] = name
    forma_dolaczyl = get_forma_dolaczyl(name)
    
    if host_sid is None:
        host_sid = request.sid
        host_name = name
        socketio.emit("info", {"msg": f"{host_name} jest hostem i ustawia kategorie."})
        socketio.emit("info2", {"host": host_name})
    else:
        # Show current host to new player
        emit("info2", {"host": host_name}, to=request.sid)

    emit("joined", {"msg": f"Dołączyłeś jako {name}"}, to=request.sid)
    socketio.emit("info", {"msg": f"Gracz {name} {forma_dolaczyl} do gry. Graczy: {len(players)}"})
    logger.info(f"Player joined: {name} (sid: {request.sid}), total players: {len(players)}")

@socketio.on("disconnect")
def on_disconnect() -> None:
    """Handle player disconnection."""
    global host_sid, host_name
    
    if request.sid in players:
        name = players.pop(request.sid)
        socketio.emit("info", {"msg": f"{name} wyszedł. Graczy: {len(players)}"})
        logger.info(f"Player disconnected: {name} (sid: {request.sid}), remaining players: {len(players)}")

    if request.sid == host_sid:
        if players:
            new_sid = next(iter(players.keys()))
            host_sid = new_sid
            host_name = players[new_sid]
            socketio.emit("info", {"msg": f"{host_name} został nowym hostem."})
            socketio.emit("info2", {"host": host_name})
            logger.info(f"New host assigned: {host_name} (sid: {new_sid})")
        else:
            host_sid, host_name = None, ""
            socketio.emit("info2", {"host": "—"})
            logger.info("No players remaining, host cleared")



@socketio.on("start")
def start_game() -> None:
    """Start game: only host can start."""
    global game_started, runda

    # Only host can start
    if request.sid != host_sid:
        emit("error_msg", {"msg": "Tylko host może rozpocząć grę."}, to=request.sid)
        return

    if len(players) < MIN_PLAYERS:
        emit("error_msg", {"msg": f"Za mało graczy (min. {MIN_PLAYERS})."}, to=request.sid)
        return

    game_started = True
    runda = 1

    try:
        category, secret = pick_category_and_secret()
    except (ValueError, KeyError) as e:
        logger.error(f"Error picking category/secret: {e}")
        emit("error_msg", {"msg": "Błąd podczas losowania kategorii."}, to=request.sid)
        game_started = False
        return

    impostor_sid = random.choice(list(players.keys()))
    logger.info(f"[GAME] Start → runda {runda} | kat: {category} | hasło: {secret} | impostor: {players[impostor_sid]}")

    # Send roles to players
    for sid in players:
        if sid == impostor_sid:
            emit("role", {"category": category, "secret": None, "runda": runda}, to=sid)
        else:
            emit("role", {"category": category, "secret": secret, "runda": runda}, to=sid)

    socketio.emit("info", {"msg": f"Gra rozpoczęta! Runda {runda}"})
    run_timer(TIMER_SECONDS)  # Start timer once





@socketio.on("next_round")
def next_round() -> None:
    """Start next round: pick new category/secret, reset timer, increment round."""
    global runda

    if len(players) < MIN_PLAYERS:
        emit("error_msg", {"msg": f"Za mało graczy (min. {MIN_PLAYERS})"}, to=request.sid)
        return

    runda += 1

    try:
        category, secret = pick_category_and_secret()
    except (ValueError, KeyError) as e:
        logger.error(f"Error picking category/secret: {e}")
        emit("error_msg", {"msg": "Błąd podczas losowania kategorii."}, to=request.sid)
        runda -= 1  # Rollback round increment
        return

    impostor_sid = random.choice(list(players.keys()))
    logger.info(f"[GAME] Nowa runda → {runda} | kat: {category} | hasło: {secret} | impostor: {players[impostor_sid]}")

    # Send roles to players
    for sid in players:
        emit(
            "role",
            {"category": category, "secret": None if sid == impostor_sid else secret, "runda": runda},
            to=sid
        )

    socketio.emit("info", {"msg": f"Nowa runda! Runda {runda}"})
    run_timer(TIMER_SECONDS)  # Start new timer


@socketio.on("pause")
def pause_game(data: Dict) -> None:
    """Pause the game timer."""
    try:
        who = (data.get("id", "") or "").strip()
        if not who:
            emit("error_msg", {"msg": "Brak identyfikatora gracza."}, to=request.sid)
            return
        
        stop_timer()
        forma = get_forma_zatrzymal(who)
        
        socketio.emit("paused", {"msg": f"{who} {forma} zegar"})
        socketio.emit("boom", {"color": "#ff4444"})
        logger.info(f"Game paused by {who}")
    except (KeyError, AttributeError) as e:
        logger.error(f"Error in pause_game: {e}")
        emit("error_msg", {"msg": "Błąd podczas pauzowania gry."}, to=request.sid)


@socketio.on("restart")
def restart_game() -> None:
    """Reset game — clear players/round, stop timer, clear UI."""
    global game_started, runda, selected_categories, host_sid, host_name
    
    stop_timer()
    game_started = False
    runda = 0
    players.clear()
    selected_categories = []
    host_sid = None
    host_name = ""
    
    logger.info("[RESET] Game reset")
    socketio.emit("clear", {})
    socketio.emit("timer", {"time": ""})
    socketio.emit("info", {"msg": "Gra zresetowana"})
    socketio.emit("info2", {"host": "—"})



if __name__ == "__main__":
    logger.info("Starting server on 0.0.0.0:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)








