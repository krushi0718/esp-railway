from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
import qrcode
import os
import sqlite3
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'railway-dev-secret')

ESP32_STREAM_URL = os.environ.get('ESP32_STREAM_URL', 'http://10.42.0.41/stream')
BASE_DIR = os.path.dirname(__file__)
QR_DIR = os.path.join(BASE_DIR, 'static', 'qrs')
DB_PATH = os.environ.get('DB_PATH', os.path.join(BASE_DIR, 'railway.db'))

# --- SIMULATED IRCTC DATABASE ---
TRAINS = [
    {"id": "12601", "name": "Mangaluru Mail", "from": "Chennai (MAS)", "to": "Mangaluru (MAQ)", "time": "20:10", "type": "Superfast"},
    {"id": "22625", "name": "Double Decker", "from": "Chennai (MAS)", "to": "Bengaluru (SBC)", "time": "07:25", "type": "AC Express"},
    {"id": "12007", "name": "Shatabdi Exp", "from": "Chennai (MAS)", "to": "Mysuru (MYS)", "time": "06:00", "type": "Shatabdi"},
]

# Coach Data: S1 to S40
# Stores: {"booked": bool, "onboard": bool, "name": str, "age": int, "gender": str, "owner": str}
coach_data = {
    f"S{i+1}": {"booked": False, "onboard": False, "name": "", "age": 0, "gender": "", "owner": ""}
    for i in range(40)
}

# Ensure directories exist
os.makedirs(QR_DIR, exist_ok=True)


def get_db_connection():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def sync_coach_from_db():
    for sid in coach_data:
        coach_data[sid].update({"booked": False, "onboard": False, "name": "", "age": 0, "gender": "", "owner": ""})

    conn = get_db_connection()
    rows = conn.execute(
        "SELECT seat_id, name, age, gender, owner, booked, onboard FROM bookings"
    ).fetchall()
    conn.close()

    for row in rows:
        sid = row['seat_id']
        if sid in coach_data:
            coach_data[sid].update({
                "booked": bool(row['booked']),
                "onboard": bool(row['onboard']),
                "name": row['name'] or "",
                "age": int(row['age'] or 0),
                "gender": row['gender'] or "",
                "owner": row['owner'] or "",
            })


def init_db():
    conn = get_db_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'tte'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bookings (
            seat_id TEXT PRIMARY KEY,
            name TEXT,
            age INTEGER,
            gender TEXT,
            owner TEXT NOT NULL,
            booked INTEGER NOT NULL DEFAULT 1,
            onboard INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(owner) REFERENCES users(username)
        )
        """
    )

    conn.execute("INSERT OR IGNORE INTO users(username, password, role) VALUES(?, ?, ?)", ("arun", "rail123", "user"))
    conn.execute("INSERT OR IGNORE INTO users(username, password, role) VALUES(?, ?, ?)", ("meena", "rail123", "user"))
    conn.execute("INSERT OR IGNORE INTO users(username, password, role) VALUES(?, ?, ?)", ("tte", "admin123", "tte"))

    conn.commit()
    conn.close()
    sync_coach_from_db()


def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if 'username' not in session:
                return redirect(url_for('login'))
            if role and session.get('role') != role:
                flash('You do not have permission to access this page.', 'danger')
                return redirect(url_for('index'))
            return f(*args, **kwargs)

        return wrapped

    return decorator


def _ticket_list_for_user(username):
    return [
        {"id": sid, "data": data}
        for sid, data in coach_data.items()
        if data.get('booked') and data.get('owner') == username
    ]


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')

        conn = get_db_connection()
        user = conn.execute(
            "SELECT username, password, role FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        conn.close()

        if user and user['password'] == password:
            session['username'] = username
            session['role'] = user['role']
            flash('Login successful.', 'success')

            if user['role'] == 'tte':
                return redirect(url_for('tte_dashboard'))
            return redirect(url_for('user_dashboard'))

        flash('Invalid username or password.', 'danger')

    return render_template('login.html')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if len(username) < 3:
            flash('Username must be at least 3 characters.', 'danger')
            return render_template('signup.html')

        if len(password) < 4:
            flash('Password must be at least 4 characters.', 'danger')
            return render_template('signup.html')

        if password != confirm_password:
            flash('Password and confirm password do not match.', 'danger')
            return render_template('signup.html')

        conn = get_db_connection()
        exists = conn.execute("SELECT username FROM users WHERE username = ?", (username,)).fetchone()
        if exists:
            conn.close()
            flash('Username already exists. Please choose another.', 'warning')
            return render_template('signup.html')

        conn.execute(
            "INSERT INTO users(username, password, role) VALUES(?, ?, 'user')",
            (username, password),
        )
        conn.commit()
        conn.close()
        flash('Signup successful. Please login.', 'success')
        return redirect(url_for('login'))

    return render_template('signup.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/')
def index():
    return render_template('index.html', trains=TRAINS)


@app.route('/user/dashboard')
@login_required(role='user')
def user_dashboard():
    username = session.get('username')
    tickets = _ticket_list_for_user(username)
    return render_template('user_dashboard.html', tickets=tickets)


@app.route('/tte/dashboard')
@login_required(role='tte')
def tte_dashboard():
    status_filter = request.args.get('status', 'all')
    total = len(coach_data)
    booked = sum(1 for x in coach_data.values() if x['booked'])
    onboard = sum(1 for x in coach_data.values() if x['onboard'])
    available = total - booked
    seat_rows_all = sorted(coach_data.items(), key=lambda pair: int(pair[0][1:]))

    if status_filter == 'booked':
        seat_rows = [row for row in seat_rows_all if row[1]['booked']]
    elif status_filter == 'onboard':
        seat_rows = [row for row in seat_rows_all if row[1]['onboard']]
    elif status_filter == 'not_boarded':
        seat_rows = [row for row in seat_rows_all if row[1]['booked'] and not row[1]['onboard']]
    elif status_filter == 'available':
        seat_rows = [row for row in seat_rows_all if not row[1]['booked']]
    else:
        status_filter = 'all'
        seat_rows = seat_rows_all

    return render_template(
        'dashboard.html',
        total=total,
        booked=booked,
        onboard=onboard,
        available=available,
        seat_rows=seat_rows,
        status_filter=status_filter,
        esp32_stream_url=ESP32_STREAM_URL,
    )


@app.route('/tte/export.csv')
@login_required(role='tte')
def tte_export_csv():
    status_filter = request.args.get('status', 'all')
    seat_rows = sorted(coach_data.items(), key=lambda pair: int(pair[0][1:]))

    if status_filter == 'booked':
        seat_rows = [row for row in seat_rows if row[1]['booked']]
    elif status_filter == 'onboard':
        seat_rows = [row for row in seat_rows if row[1]['onboard']]
    elif status_filter == 'not_boarded':
        seat_rows = [row for row in seat_rows if row[1]['booked'] and not row[1]['onboard']]
    elif status_filter == 'available':
        seat_rows = [row for row in seat_rows if not row[1]['booked']]

    lines = ["seat_id,booked,onboard,name,age,gender,owner"]
    for seat_id, seat in seat_rows:
        lines.append(
            f"{seat_id},{int(bool(seat['booked']))},{int(bool(seat['onboard']))},{seat['name']},{seat['age']},{seat['gender']},{seat['owner']}"
        )

    content = "\n".join(lines)
    return app.response_class(
        content,
        mimetype='text/csv',
        headers={"Content-Disposition": "attachment; filename=tte_dashboard.csv"},
    )

@app.route('/api/seats')
def get_seats():
    return jsonify(coach_data)

@app.route('/select_seat/<train_id>')
@login_required(role='user')
def select_seat(train_id):
    train = next((t for t in TRAINS if t['id'] == train_id), None)
    return render_template('seat_map.html', train=train, coach=coach_data)

@app.route('/book', methods=['POST'])
def book():
    if session.get('role') != 'user':
        return jsonify({"success": False, "message": "Please login as a passenger to book seats."}), 401

    data = request.json
    passengers = data.get('passengers', [])

    conn = get_db_connection()
    booked_ids = []
    for p in passengers:
        s_id = p.get('seat_id')
        if s_id in coach_data and not coach_data[s_id]['booked']:
            owner = session.get('username', '')
            name = p.get('name')
            age = p.get('age')
            gender = p.get('gender')

            coach_data[s_id].update({
                "booked": True,
                "name": name,
                "age": age,
                "gender": gender,
                "owner": owner
            })

            conn.execute(
                """
                INSERT OR REPLACE INTO bookings(seat_id, name, age, gender, owner, booked, onboard)
                VALUES(?, ?, ?, ?, ?, 1, COALESCE((SELECT onboard FROM bookings WHERE seat_id = ?), 0))
                """,
                (s_id, name, age, gender, owner, s_id),
            )

            # Generate QR Code
            qrcode.make(s_id).save(os.path.join(QR_DIR, f"{s_id}.png"))
            booked_ids.append(s_id)

    conn.commit()
    conn.close()
            
    if booked_ids:
        return jsonify({"success": True, "seats": ",".join(booked_ids)})
    return jsonify({"success": False, "message": "Booking failed"})

@app.route('/tickets/<seat_ids>')
@login_required()
def view_tickets(seat_ids):
    is_tte = session.get('role') == 'tte'
    current_user = session.get('username')
    ids = seat_ids.split(',')
    ticket_list = []
    for sid in ids:
        if sid not in coach_data:
            continue
        if is_tte or coach_data[sid].get('owner') == current_user:
            ticket_list.append({"id": sid, "data": coach_data[sid]})

    if not ticket_list:
        flash('No tickets found for your account.', 'warning')
        if is_tte:
            return redirect(url_for('tte_dashboard'))
        return redirect(url_for('user_dashboard'))

    return render_template('multi_ticket.html', tickets=ticket_list)


@app.route('/my_tickets')
@login_required(role='user')
def my_tickets():
    tickets = _ticket_list_for_user(session.get('username'))
    if not tickets:
        flash('No booked tickets yet. Book seats to generate QR tickets.', 'info')
        return redirect(url_for('index'))
    seat_ids = ','.join([t['id'] for t in tickets])
    return redirect(url_for('view_tickets', seat_ids=seat_ids))

@app.route('/validate')
def validate():
    t_id = request.args.get('ticket_id')
    
    # Logic check
    if t_id in coach_data and coach_data[t_id]['booked']:
        # Allow repeated scans for valid booked tickets.
        coach_data[t_id]['onboard'] = True
        conn = get_db_connection()
        conn.execute("UPDATE bookings SET onboard = 1 WHERE seat_id = ?", (t_id,))
        conn.commit()
        conn.close()
        return jsonify({"status": "AUTHORIZED"}), 200
        
    return jsonify({"status": "DENIED"}), 403

init_db()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '5000'))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)