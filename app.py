import threading, time, random, json
from pathlib import Path 
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

TIMER_SECONDS = 30  
runda = 0
players = {}            # {sid: name}
game_started = False
host_sid = None
host_name = ""
stop_flag = False
timer_thread = None

BASE = Path(__file__).resolve().parent

def load_index_and_data():
    idx  = json.loads((BASE / "categories_index.json").read_text(encoding="utf-8"))
    data = json.loads((BASE / "categories_data.json").read_text(encoding="utf-8"))
    names = [c for c in idx["categories"] if c in data]  # tylko te, dla których mamy hasła
    return names, data

CATEGORIES_LIST, CATEGORIES_DATA = load_index_and_data()
selected_categories = []


# ---------------- TIMER ----------------
def run_timer(seconds=None):
    """Stop stary licznik (jeśli jest) i uruchom nowy."""
    global stop_flag, timer_thread
    if seconds is None:
        seconds = TIMER_SECONDS


    # Zatrzymaj poprzedni licznik
    if timer_thread and timer_thread.is_alive():
        stop_flag = True
        timer_thread.join()

    stop_flag = False

    def countdown():
        global stop_flag
        for i in range(seconds, -1, -1):
            if stop_flag:
                return  # zakończ, jeśli zatrzymano
            socketio.emit("timer", {"time": i})
            time.sleep(1)
        socketio.emit("end", {"color": "#ffffff"})

    timer_thread = threading.Thread(target=countdown, daemon=True)
    timer_thread.start()



def stop_timer():
    """Zatrzymaj aktualny licznik (jeśli działa)."""
    global stop_flag, timer_thread
    stop_flag = True
    if timer_thread and timer_thread.is_alive():
        timer_thread.join()
    timer_thread = None


# ---------------- ROUTES ----------------
@app.route("/")
@app.route("/mobi")
def mobi():
    # zwracaj mobilny widok także pod "/"
    return render_template("mobi.html",categories=CATEGORIES_LIST)


# -------------- SOCKET HANDLERS --------------

def pick_category_and_secret():
    # jeśli chcesz „hot reload” JSON-ów w devie, możesz tu odświeżać:
    # global CATEGORIES_LIST, CATEGORIES_DATA
    # CATEGORIES_LIST, CATEGORIES_DATA = load_index_and_data()

    global selected_categories, CATEGORIES_LIST, CATEGORIES_DATA

    # koszyk do losowania: wybrane kategorie, a jeśli brak – wszystkie
    pool = selected_categories if selected_categories else CATEGORIES_LIST

    # (na wszelki wypadek) odfiltruj ewentualne nieistniejące wpisy
    pool = [c for c in pool if c in CATEGORIES_DATA]
    if not pool:  # gdyby ktoś wysłał coś dziwnego
        pool = CATEGORIES_LIST

    cat = random.choice(pool)
    secret = random.choice(CATEGORIES_DATA[cat])
    return cat, secret


@socketio.on("set_category")
def set_category(data):
    global selected_categories, host_sid, game_started

    # ⛔ tylko host i tylko przed startem
    if request.sid != host_sid:
        emit("error_msg", {"msg": "Tylko host może ustawiać kategorie."}, to=request.sid)
        return
    if game_started:
        emit("error_msg", {"msg": "Nie można zmieniać kategorii w trakcie gry."}, to=request.sid)
        return

    cats = (data or {}).get("categories", [])
    if not isinstance(cats, list):
        cats = [cats] if cats else []

    selected_categories = [c for c in cats if c in CATEGORIES_LIST]
    label = ", ".join(selected_categories) if selected_categories else "Losowa"

    print("[SET_CATEGORY]", request.sid, "->", selected_categories)
    socketio.emit("category_update", {"selected": selected_categories, "label": label})
    socketio.emit("info", {"msg": f"Kategoria: {label}"})

    return {"ok": True, "label": label, "selected": selected_categories}

@socketio.on("connect")
def on_connect():
    label = ", ".join(selected_categories) if selected_categories else "Losowa"
    emit("category_update", {"selected": selected_categories, "label": label})
    emit("timer_update", {"seconds": TIMER_SECONDS})




    
@socketio.on("set_timer")
def set_timer(data):
    global TIMER_SECONDS

    # tylko host może zmieniać czas
    if request.sid != host_sid:
        emit("error_msg", {"msg": "Tylko host może zmienić czas rundy."}, to=request.sid)
        return

    try:
        secs = int((data or {}).get("seconds", TIMER_SECONDS))
    except (TypeError, ValueError):
        emit("error_msg", {"msg": "Nieprawidłowa liczba sekund."}, to=request.sid)
        return

    secs = max(5, min(secs, 900))  # 5s–900s (15 minut)
    TIMER_SECONDS = secs  # ✅ zapisz globalnie!

    print(f"[SET_TIMER] {request.sid} ustawił czas na {TIMER_SECONDS}s")

    socketio.emit("timer_update", {"seconds": TIMER_SECONDS})
    socketio.emit("info", {"msg": f"Czas rundy ustawiony na {TIMER_SECONDS} s"})


@socketio.on("join")
def join_game(data):
    global host_sid, host_name
    name = data.get("id", "Anonim")
    players[request.sid] = name

    if host_sid is None:
        host_sid = request.sid
        host_name = name
        socketio.emit("info", {"msg": f"{host_name} jest hostem i ustawia kategorie."})
        socketio.emit("info2", {"host": host_name})  # 👑 wysyłamy do wszystkich
    else:
        # pokaż obecnego hosta nowemu graczowi
        emit("info2", {"host": host_name}, to=request.sid)

    emit("joined", {"msg": f"Dołączyłeś jako {name}"}, to=request.sid)
    socketio.emit("info", {"msg": f"{name} dołączył do gry. Graczy: {len(players)}"})

@socketio.on("disconnect")
def on_disconnect():
    global host_sid, host_name   # <-- DODAJ
    if request.sid in players:
        name = players.pop(request.sid)
        socketio.emit("info", {"msg": f"{name} wyszedł. Graczy: {len(players)}"})

    if request.sid == host_sid:
        if players:
            new_sid = next(iter(players.keys()))
            host_sid = new_sid
            host_name = players[new_sid]
            socketio.emit("info", {"msg": f"{host_name} został nowym hostem."})
            socketio.emit("info2", {"host": host_name})
        else:
            host_sid, host_name = None, ""
            socketio.emit("info2", {"host": "—"})



@socketio.on("start")
def start_game():
    """Start gry: tylko host może rozpocząć."""
    global game_started, runda

    # tylko host może kliknąć start
    if request.sid != host_sid:
        emit("error_msg", {"msg": "Tylko host może rozpocząć grę."}, to=request.sid)
        return

    if len(players) < 2:
        emit("error_msg", {"msg": "Za mało graczy (min. 2)."}, to=request.sid)
        return

    game_started = True
    runda = 1

    pool = selected_categories if selected_categories else CATEGORIES_LIST
    category = random.choice(pool)
    secret = random.choice(CATEGORIES_DATA[category])
    impostor_sid = random.choice(list(players.keys()))
    run_timer(TIMER_SECONDS)

    print(f"[GAME] Start gry → runda {runda} | kat: {category} | hasło: {secret}")

    for sid in players:
        if sid == impostor_sid:
            emit("role", {"category": category, "secret": None, "runda": runda}, to=sid)
        else:
            emit("role", {"category": category, "secret": secret, "runda": runda}, to=sid)

    socketio.emit("info", {"msg": f"Gra rozpoczęta! Runda {runda}"})
    run_timer(TIMER_SECONDS)





@socketio.on("next_round")
def next_round():
    """Nowa runda: losowanie (z wybranej lub losowej kategorii), reset timera, runda += 1."""
    global runda

    if len(players) < 2:
        emit("error_msg", {"msg": "Za mało graczy (min. 2)"}, to=request.sid)
        return

    # zatrzymaj poprzedni licznik, żeby nie dublował się z nowym
    run_timer(TIMER_SECONDS)


    runda += 1

    # użyj helpera z 2-plikowego rozwiązania
    # (names,data = get_categories() lub CATEGORIES_LIST/CATEGORIES_DATA + selected_category)
    pool = selected_categories if selected_categories else CATEGORIES_LIST
    category = random.choice(pool)
    secret = random.choice(CATEGORIES_DATA[category])

    impostor_sid = random.choice(list(players.keys()))
    print(f"[GAME] Nowa runda → {runda} | kat: {category} | hasło: {secret}")

    for sid in players:
        emit(
            "role",
            {"category": category, "secret": None if sid == impostor_sid else secret, "runda": runda},
            to=sid
        )

    socketio.emit("info", {"msg": f"Nowa runda! Runda {runda}"})
    run_timer()


@socketio.on("pause")
def pause_game(data):
    stop_timer()
    who = data["id"].strip()
    who_lower = who.lower()

    # wyjątki męskie na -a
    meskie_na_a = {"kuba", "kosma", "barnaba", "bonawentura", "ezra", "saba", "misha", "sasha"}
    # wyjątki żeńskie bez -a
    zenskie_bez_a = {"miriam", "beatrycze", "ingrid", "ester", "noemi", "rachel", "ruth"}

    if (who_lower.endswith("a") and who_lower not in meskie_na_a) or who_lower in zenskie_bez_a:
        forma = "zatrzymała"
    else:
        forma = "zatrzymał"

    socketio.emit("paused", {"msg": f"{who} {forma} zegar"})
    socketio.emit("boom", {"color": "#ff4444"})  # możesz podać kolor akcentu


@socketio.on("restart")
def restart_game():
    """Reset gry — czyść graczy/rundę, stop timer, czyść UI."""
    global game_started, runda, selected_categories, host_sid, host_name
    stop_timer()
    game_started = False
    runda = 0
    # NIE czyścimy players — jeśli chcesz czyścić, odkomentuj poniżej:
    players.clear()
    print("[RESET] Gra zresetowana")
    socketio.emit("clear", {})
    selected_categories = []
    host_sid = None
    host_name = ""
    socketio.emit("timer", {"time": ""})
    socketio.emit("info", {"msg": "Gra zresetowana"})
    socketio.emit("info2", {"host": "—"})



if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)


