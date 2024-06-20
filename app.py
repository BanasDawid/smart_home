from flask import Flask, Response, render_template,  request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import RPi.GPIO as GPIO
import os
import threading
from picamera2 import Picamera2
import cv2
import time

app = Flask(__name__)
app.config.from_pyfile('config.cfg') #wywołanie config.cfg - konfiguracja bazy danych
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    first_name = db.Column(db.String(50))
    last_name = db.Column(db.String(50))

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

GPIO.setmode(GPIO.BCM)

# Ustawienie pinów GPIO dla diod LED
LED_PINS = {
    "living-room-light": 20,
    "kitchen-light": 16,
    "entery-light": 6,
    "bathroom-light": 12,
    "bedroom-light": 26,
    "office-light": 13,
    "childroom-light": 21,
    "vestibule-light": 19,
}
# Ustawienie pinów GPIO dla kontaktronów
CONTACT_PINS = {
    "kuchnia I": 23,
    "kuchnia II": 27,
    "łazienka": 18,
    "sypialnia": 22,
    "gabinet": 15,
    "pokój dziecięcy": 17,
}

# Adresy czujników DS18B20
SENSOR_ADDRESSES = {
    "salon": "28-00000012d3ab",
    "sypialnia": "28-0b239a7f8c8a",
    "łazienka": "28-0b239ac450f4",
    "gabinet": "28-0b235770ad6e",
    "pokój dziecięcy": "28-000000123315"
}

AC_SENSOR_ADDRESSES = {
    "salon": "28-00000012d3ab",
    "sypialnia": "28-0b239a7f8c8a",
}

# Ustawienie pinów GPIO dla diod LED ogrzewania
HEATING_PINS = {
    "salon": 7,
    "sypialnia": 0,
    "łazienka": 1,
    "gabinet": 8,
    "pokój dziecięcy": 5,
}

# Piny GPIO dla klimatyzacji
AC_PINS = {
    "salon": 10,
    "sypialnia": 24,
}

AC_LEDS ={
    "salon": 25,
    "sypialnia": 9,
}
# Pin dla alarmu dźwiękowego
ALARM_PIN = 14
GPIO.setup(ALARM_PIN, GPIO.OUT)

# Inicjalizacja zmiennych dla temperatur docelowych ogrzewania
target_temperatures = {
    "salon": 20,
    "sypialnia": 20,
    "łazienka": 20,
    "gabinet": 20,
    "pokój dziecięcy": 20
}

# Inicjalizacja zmiennych globalnych dla temperatur docelowych klimatyzacji
ac_target_temperatures = {
    "salon": 24,
    "sypialnia": 24,
}

# Ustaiwnie pinu zamka do drzwi
LOCK_PIN = 11 
lock_state = False  # False - zamknięty, True - otwarty
lock_timer = None

# Ustawienia GPIO
GPIO.setup(LOCK_PIN, GPIO.OUT)
GPIO.output(LOCK_PIN, GPIO.LOW)


# Ścieżka do katalogu, w którym znajdują się dane o temperaturze
SENSOR_DIRECTORY = '/sys/bus/w1/devices/'


def generate_frames():
    while True:
        frame = picam2.capture_array()
        ret, buffer = cv2.imencode('.jpg', frame)
        frame = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')



# Funkcja do zapalania diody LED
def turn_on_led(room, temperature):
    pin = HEATING_PINS.get(room)
    if pin is not None:
        if temperature < target_temperature[room]:
            GPIO.output(pin, GPIO.HIGH)  # Zapal diodę LED
        else:
            GPIO.output(pin, GPIO.LOW)  # Zgaś diodę LED


# Inicjalizacja pinów GPIO dla ogrzewania
for pin in HEATING_PINS.values():
    GPIO.setup(pin, GPIO.OUT)
    GPIO.output(pin, GPIO.LOW)
 

# Inicjalizacja pinów GPIO dla oswietlenia
for pin in LED_PINS.values():
    GPIO.setup(pin, GPIO.OUT)

#alarm
for pin in CONTACT_PINS.values():
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

alarm_armed = False
stop_thread = False


# Inicjalizacja pinów GPIO dla ogrzewania
for pin in HEATING_PINS.values():
    GPIO.setup(pin, GPIO.OUT)
    GPIO.output(pin, GPIO.LOW)


# Inicjalizacja pinów GPIO dla klimatyzacji
for pin in AC_PINS.values():
    GPIO.setup(pin, GPIO.OUT)
    GPIO.output(pin, GPIO.LOW)

for pin in AC_LEDS.values():
    GPIO.setup(pin, GPIO.OUT)
    GPIO.output(pin, GPIO.LOW)

# inicjalizacja kamery
picam2 = Picamera2()
picam2.configure(picam2.create_preview_configuration(main={"format": 'XRGB8888', "size": (640, 480)}))
picam2.start()

# sterowanie zamkiem
def toggle_lock():
    global lock_state, lock_timer
    if lock_state:
        GPIO.output(LOCK_PIN, GPIO.LOW)
        lock_state = False
        if lock_timer:
            lock_timer.cancel()
            lock_timer = None
    else:
        GPIO.output(LOCK_PIN, GPIO.HIGH)
        lock_state = True
        if lock_timer:
            lock_timer.cancel()
        lock_timer = threading.Timer(15, close_lock)
        lock_timer.start()

def close_lock():
    global lock_state
    GPIO.output(LOCK_PIN, GPIO.LOW)
    lock_state = False


# Funkcja do sterowania diodą LED oświetlenia
def toggle_led(pin, state):
    GPIO.output(pin, state)

# Funkcja do odczytu stanu kontaktronu
def read_contact(contact_pin):
    return GPIO.input(contact_pin)


# Funkcja monitorująca stan okien i uruchamiająca alarm
def monitor_windows():
    global stop_thread
    while not stop_thread:
        if alarm_armed:
            contacts_state = {room: read_contact(pin) for room, pin in CONTACT_PINS.items()}
            if any(state == 1 for state in contacts_state.values()):
                while any(read_contact(pin) == 0 for pin in CONTACT_PINS.values()) and alarm_armed:
                    GPIO.output(ALARM_PIN, GPIO.HIGH)
                    time.sleep(0.5)
                    GPIO.output(ALARM_PIN, GPIO.LOW)
                    time.sleep(0.5)
        time.sleep(1)  # Sprawdź stan okien co 1 sekundę

# Funkcja do odczytu temperatury z czujnika DS18B20
def read_temperature(sensor_address):
    try:
        sensor_file = os.path.join(SENSOR_DIRECTORY, sensor_address, 'w1_slave')
        with open(sensor_file, 'r') as file:
            lines = file.readlines()
            temperature_line = lines[1].split('t=')[1]
            temperature = float(temperature_line) / 1000.0
            return round(temperature, 1)
    except Exception as e:
        print(f"Błąd odczytu temperatury: {e}")
        return None
    
# Funkcja do kontroli ogrzewania
def control_heating():
    while True:
        for room, sensor_address in SENSOR_ADDRESSES.items():
            current_temperature = read_temperature(sensor_address)
            if current_temperature is not None:
                target_temperature = target_temperatures[room]
                if current_temperature < target_temperature - 0.5:
                    GPIO.output(HEATING_PINS[room], GPIO.HIGH)
                elif current_temperature >= target_temperature + 0.5:
                    GPIO.output(HEATING_PINS[room], GPIO.LOW)
        time.sleep(1)

# Uruchomienie wątku kontroli ogrzewania
threading.Thread(target=control_heating, daemon=True).start()

# Funkcja do kontroli klimatyzacji
def control_ac():
    while True:
        for room, sensor_address in AC_SENSOR_ADDRESSES.items():
            current_temperature = read_temperature(sensor_address)
            if current_temperature is not None:
                target_temperature = ac_target_temperatures[room]
                if current_temperature > target_temperature + 0.5:
                    GPIO.output(AC_PINS[room], GPIO.HIGH)
                    GPIO.output(AC_LEDS[room], GPIO.HIGH)
                elif current_temperature <= target_temperature - 0.5:
                    GPIO.output(AC_PINS[room], GPIO.LOW)
                    GPIO.output(AC_LEDS[room], GPIO.LOW)
        time.sleep(1) 

# Uruchomienie wątku kontroli klimatyzacji
threading.Thread(target=control_ac, daemon=True).start()

# Ścieżka główna aplikacji
@app.route('/index')
@login_required
def index():
    return render_template('index.html')


# Ścieżka do strony z oświetleniem
@app.route('/lights', methods=['GET', 'POST'])
@login_required
def lights():
    if request.method == 'POST':
        checkbox_id = request.form.get('checkbox_id')
        state = request.form.get('state')
        pin = LED_PINS.get(checkbox_id)
        if pin is not None:
            if state == 'on':
                toggle_led(pin, True)
            elif state == 'off':
                toggle_led(pin, False)
        return redirect(url_for('lights'))
    
    contacts_state = {room: GPIO.input(pin) for room, pin in LED_PINS.items()}
    return render_template('lights.html', contacts_state=contacts_state)

# Ścieżka do strony z bezpieczeństwem
@app.route('/safety')
@login_required
def safety():
    contacts_state = {}
    for room, contact_pin in CONTACT_PINS.items():
        contacts_state[room] = read_contact(contact_pin)
    return render_template('safety.html', contacts_state=contacts_state)


# Ścieżka do strony z ogrzewaniem
@app.route('/set_target_temperature', methods=['POST'])
def set_target_temperature():
    global target_temperatures
    data = request.form
    room = data['room']
    temperature = int(data['temperature'])
    
    # Sprawdź, czy temperatura docelowa dla ogrzewania nie jest wyższa niż temperatura docelowa klimatyzacji
    if room in ac_target_temperatures:
        ac_target_temperature = ac_target_temperatures.get(room)
        if ac_target_temperature is not None and temperature >= ac_target_temperature:
            flash('Nie można ustawić temperatury docelowej ogrzewania wyższej niż temperatura docelowa klimatyzacji. Zmień temperaturę klimatyzacji', "danger")
            return redirect('/heating')
            
    target_temperatures[room] = temperature
    return redirect('/heating')


@app.route('/heating')
@login_required
def heating():
    temperatures = {room: read_temperature(sensor_address) for room, sensor_address in SENSOR_ADDRESSES.items()}
    return render_template('heating.html', temperatures=temperatures, target_temperatures=target_temperatures)


@app.route('/set_ac_target_temperature', methods=['POST'])
def set_ac_target_temperature():
    global ac_target_temperatures
    room = request.form['room']
    temperature = int(request.form['temperature'])
     # Sprawdź, czy temperatura docelowa dla klimatyzacji nie jest niższa niż temperatura docelowa ogrzewania
    if room in target_temperatures:
        heating_target_temperature = target_temperatures.get(room)
        if heating_target_temperature is not None and temperature <= heating_target_temperature:
            flash('Nie można ustawić temperatury docelowej klimatyzacji niższej niż temperatura docelowa ogrzewania. Zmień temperaturę ogrzewania', "danger")
            return redirect('/ac')
    ac_target_temperatures[room] = temperature
    return redirect('/ac')

@app.route('/ac')
@login_required
def ac():
    temperatures = {room: read_temperature(sensor_address) for room, sensor_address in AC_SENSOR_ADDRESSES.items()}
    return render_template('ac.html', temperatures=temperatures, ac_target_temperatures=ac_target_temperatures)



# Ścieżka do pbslugi kamery

@app.route('/monitoring')
@login_required
def monitoring():
    return render_template('monitoring.html', lock_state=lock_state)


@app.route('/video_feed')
def video_feed():
    
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/toggle_lock')
def toggle_lock_route():
    toggle_lock()
    return redirect(url_for('monitoring'))


@app.route('/alarm', methods=['GET', 'POST'])
def alarm():
    global alarm_armed, stop_thread
    contacts_state = {room: read_contact(pin) for room, pin in CONTACT_PINS.items()}
    all_closed = all(state == 0 for state in contacts_state.values())

    if request.method == 'POST':
        pin = request.form.get('pin')
        if pin == "1234":  # Przykładowy PIN
            if alarm_armed:
                alarm_armed = False
                stop_thread = True
                flash("Alarm rozbrojony!", "success")
                GPIO.output(ALARM_PIN, GPIO.LOW)  # Wyłącz dźwięk alarmu
            else:
                if all_closed:
                    alarm_armed = True
                    stop_thread = False
                    threading.Thread(target=monitor_windows, daemon=True).start()
                    flash("Alarm uzbrojony!", "success")
                else:
                    open_windows = [room for room, state in contacts_state.items() if state == 1]
                    flash(f"Nie można uzbroić alarmu! Okno otwarte w: {', '.join(open_windows)}", "danger")
        else:
            flash("Nieprawidłowy PIN!", "danger")
        return redirect(url_for('alarm'))

    return render_template('alarm.html', contacts_state=contacts_state, alarm_armed=alarm_armed)




@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html')


@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('profile'))
        else:
            flash('Nieprawidłowa nazwa użytkownika lub hasło', "danger")
            return redirect(url_for('login'))
    return render_template('login.html')



@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        old_password = request.form['old_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        if not check_password_hash(current_user.password, old_password):
            flash('Aktualne hasło jest nieprawidłowe!', "danger")
            return redirect(url_for('change_password'))
        
        if new_password != confirm_password:
            flash('Hasła nie są takie same!', "danger")
            return redirect(url_for('change_password'))

        current_user.password = generate_password_hash(new_password, method='scrypt')
        db.session.commit()
        flash('Hasło zmienione.', "success")
        return redirect(url_for('profile'))
    
    return render_template('change_password.html')

@app.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    if current_user.username != 'admin':
        flash('BRAK DOSTĘPU.', "danger")
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        first_name = request.form['first_name']
        last_name = request.form['last_name']
        
        if User.query.filter_by(username=username).first():
            flash('Taki użytkownik już istnieje!', "danger")
            return redirect(url_for('register'))
        
        new_user = User(username=username, password=generate_password_hash(password, method='scrypt'), first_name=first_name, last_name=last_name)
        db.session.add(new_user)
        db.session.commit()
        flash('Użytkownik zarejestrowany.', "success")
        return redirect(url_for('index'))
    
    return render_template('register.html')

@app.route('/logout', methods=['GET', 'POST'])
@login_required
def logout():
    logout_user()
    flash('Użytkownik wylogowany.', "success")
    return redirect(url_for('login'))



if __name__ == '__main__':
    try:
        app.run(debug=False, host='0.0.0.0')
    finally:
        #po zamknieciu aplikacji
        GPIO.cleanup()
