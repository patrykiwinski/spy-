import threading, time, random
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

TIMER_SECONDS = 30  # 7:30
runda = 0
players = {}            # {sid: name}
game_started = False

stop_flag = False
timer_thread = None

CATEGORIES = {
    "Zwierzęta": ["Gekon", "Kot", "Pies", "Żaba", "Tygrys"],
    "Przedmioty": ["Krzesło", "Telefon", "Komputer", "Piłka"],
    "Osoby": ["Lewandowski", "Einstein", "Messi", "Obama"],
    "Państwa": ["Polska", "Niemcy", "Francja", "USA", "Hiszpania"],
    "Miasta": ["Warszawa", "Berlin", "Paryż", "Londyn", "Rzym"]
}

# ---------------- TIMER ----------------
def run_timer(seconds=TIMER_SECONDS):
    """Stop stary licznik (jeśli jest) i uruchom nowy."""
    global stop_flag, timer_thread

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
    return render_template("mobi.html")


# -------------- SOCKET HANDLERS --------------
@socketio.on("join")
def join_game(data):
    """Gracz dołącza z imieniem."""
    name = data.get("id", "Anonim")
    players[request.sid] = name
    print(f"[JOIN] {name} (SID={request.sid})  | graczy: {len(players)}")

    # tylko do tego gracza
    emit("joined", {"msg": f"Dołączyłeś jako {name}"}, to=request.sid)
    # do wszystkich (łącznie z nim): info
    socketio.emit("info", {"msg": f"{name} dołączył do gry. Graczy: {len(players)}"})


@socketio.on("disconnect")
def on_disconnect():
    """Czyść gracza przy wyjściu."""
    if request.sid in players:
        name = players.pop(request.sid)
        print(f"[LEFT] {name} left. | number of players: {len(players)}")
        socketio.emit("info", {"msg": f"{name} wyszedł. Graczy: {len(players)}"})


@socketio.on("start")
def start_game():
    """Start gry: losuj kategorię/hasło/szpieg, runda=1, start timer."""
    global game_started, runda
    if len(players) < 2:
        emit("error", {"msg": "Za mało graczy (min. 2)"}, to=request.sid)
        return

    game_started = True
    runda = 1

    category = random.choice(list(CATEGORIES.keys()))
    secret = random.choice(CATEGORIES[category])
    impostor_sid = random.choice(list(players.keys()))
    print(f"[GAME] Start gry → runda {runda} | kat: {category} | hasło: {secret}")

    for sid in players:
        if sid == impostor_sid:
            emit("role", {"category": category, "secret": None, "runda": runda}, to=sid)
        else:
            emit("role", {"category": category, "secret": secret, "runda": runda}, to=sid)

    socketio.emit("info", {"msg": f"Gra rozpoczęta! Runda {runda}"})
    run_timer()


@socketio.on("next_round")
def next_round():
    """Nowa runda: znów losowanie + reset timera, runda += 1."""
    global runda
    if len(players) < 2:
        emit("error", {"msg": "Za mało graczy (min. 2)"}, to=request.sid)
        return

    runda += 1

    category = random.choice(list(CATEGORIES.keys()))
    secret = random.choice(CATEGORIES[category])
    impostor_sid = random.choice(list(players.keys()))
    print(f"[GAME] Nowa runda → {runda} | kat: {category} | hasło: {secret}")

    for sid in players:
        if sid == impostor_sid:
            emit("role", {"category": category, "secret": None, "runda": runda}, to=sid)
        else:
            emit("role", {"category": category, "secret": secret, "runda": runda}, to=sid)

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
    global game_started, runda
    stop_timer()
    game_started = False
    runda = 0
    # NIE czyścimy players — jeśli chcesz czyścić, odkomentuj poniżej:
    # players.clear()
    print("[RESET] Gra zresetowana")
    socketio.emit("clear", {})
    socketio.emit("timer", {"time": ""})
    socketio.emit("info", {"msg": "Gra zresetowana"})


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
