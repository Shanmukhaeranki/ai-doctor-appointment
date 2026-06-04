from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
from groq import Groq
import jwt
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

app = Flask(__name__)
CORS(app)

# Config
groq_client = Groq(api_key=os.getenv('GROQ_API_KEY'))
JWT_SECRET = os.getenv('JWT_SECRET', 'mysecretkey123')

# Configure Groq
groq_client = Groq(api_key=os.getenv('GROQ_API_KEY'))

def init_db():
    conn = sqlite3.connect('hospital.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, email TEXT UNIQUE,
        password TEXT, role TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS doctors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, specialization TEXT,
        experience INTEGER, phone TEXT,
        available_days TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS appointments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER, doctor_id INTEGER,
        symptoms TEXT, ai_suggestion TEXT,
        appointment_date TEXT, appointment_time TEXT,
        status TEXT DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (patient_id) REFERENCES users(id),
        FOREIGN KEY (doctor_id) REFERENCES doctors(id)
    )''')

    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO users (name, email, password, role) VALUES (?,?,?,?)",
                  ('Admin', 'admin@hospital.com', 'admin123', 'admin'))
        
        doctors = [
            ('Dr. Rajesh Kumar', 'rajesh@hospital.com', 'Cardiologist', 10, 'Monday,Wednesday,Friday'),
            ('Dr. Priya Sharma', 'priya@hospital.com', 'Dermatologist', 7, 'Tuesday,Thursday,Saturday'),
            ('Dr. Anil Verma', 'anil@hospital.com', 'Neurologist', 12, 'Monday,Tuesday,Wednesday'),
            ('Dr. Sunita Reddy', 'sunita@hospital.com', 'Orthopedic', 8, 'Thursday,Friday,Saturday'),
        ]
        
        for name, email, spec, exp, days in doctors:
            c.execute("INSERT INTO users (name, email, password, role) VALUES (?,?,?,?)",
                      (name, email, 'admin123', 'doctor'))
            user_id = c.lastrowid
            c.execute("INSERT INTO doctors (user_id, specialization, experience, phone, available_days) VALUES (?,?,?,?,?)",
                      (user_id, spec, exp, '9876543210', days))
    
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect('hospital.db')
    conn.row_factory = sqlite3.Row
    return conn

def decode_token(request):
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    return jwt.decode(token, JWT_SECRET, algorithms=['HS256'])

# AUTH ROUTES

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    try:
        conn = get_db()
        conn.execute("INSERT INTO users (name, email, password, role) VALUES (?,?,?,?)",
                     (data['name'], data['email'], data['password'], 'patient'))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Registered successfully'})
    except Exception as e:
        print("Register error:", e)
        return jsonify({'error': str(e)}), 400

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data['email'].strip()
    password = data['password'].strip()
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=? AND password=?",
                        (email, password)).fetchone()
    conn.close()
    if user:
        token = jwt.encode({
            'id': user['id'], 'role': user['role'],
            'name': user['name'],
            'exp': datetime.utcnow() + timedelta(days=7)
        }, JWT_SECRET)
        return jsonify({'token': token, 'role': user['role'], 'name': user['name']})
    return jsonify({'error': 'Invalid credentials'}), 401

# GEMINI SYMPTOM CHECKER

@app.route('/api/symptom-check', methods=['POST'])
def symptom_check():
    data = request.get_json()
    symptoms = data['symptoms']
    
    prompt = f"""
    A patient has the following symptoms: {symptoms}
    
    Based on these symptoms, suggest:
    1. The most likely medical condition
    2. The type of specialist doctor they should consult (one of: Cardiologist, Dermatologist, Neurologist, Orthopedic, General Physician)
    3. Urgency level (Low/Medium/High)
    4. Basic advice
    
    Reply in this exact format:
    CONDITION: [condition name]
    SPECIALIST: [specialist type]
    URGENCY: [Low/Medium/High]
    ADVICE: [2-3 sentences of advice]
    """
    
    try:
        response = groq_client.chat.completions.create(
            model='llama-3.3-70b-versatile',
            messages=[{'role': 'user', 'content': prompt}]
        )
        result = response.choices[0].message.content
    except Exception as e:
        print("Groq error:", str(e))
        return jsonify({'error': str(e)}), 500
    
    specialist = 'General Physician'
    for line in result.split('\n'):
        if 'SPECIALIST:' in line:
            specialist = line.replace('SPECIALIST:', '').strip()
            break
    
    conn = get_db()
    doctors = conn.execute("""
        SELECT d.id, u.name, d.specialization, d.experience, d.available_days
        FROM doctors d JOIN users u ON d.user_id = u.id
        WHERE d.specialization LIKE ?
    """, (f'%{specialist}%',)).fetchall()
    conn.close()
    
    return jsonify({
        'ai_response': result,
        'specialist': specialist,
        'doctors': [dict(d) for d in doctors]
    })

# APPOINTMENT ROUTES

@app.route('/api/book-appointment', methods=['POST'])
def book_appointment():
    data = request.get_json()
    try:
        user = decode_token(request)
        conn = get_db()
        conn.execute("""
            INSERT INTO appointments 
            (patient_id, doctor_id, symptoms, ai_suggestion, appointment_date, appointment_time)
            VALUES (?,?,?,?,?,?)
        """, (user['id'], data['doctor_id'], data['symptoms'],
              data['ai_suggestion'], data['date'], data['time']))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Appointment booked successfully!'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/my-appointments', methods=['GET'])
def my_appointments():
    user = decode_token(request)
    conn = get_db()
    appointments = conn.execute("""
        SELECT a.*, u.name as doctor_name, d.specialization
        FROM appointments a
        JOIN doctors d ON a.doctor_id = d.id
        JOIN users u ON d.user_id = u.id
        WHERE a.patient_id = ?
        ORDER BY a.created_at DESC
    """, (user['id'],)).fetchall()
    conn.close()
    return jsonify([dict(a) for a in appointments])

@app.route('/api/doctor-appointments', methods=['GET'])
def doctor_appointments():
    user = decode_token(request)
    conn = get_db()
    doctor = conn.execute("SELECT id FROM doctors WHERE user_id=?", (user['id'],)).fetchone()
    appointments = conn.execute("""
        SELECT a.*, u.name as patient_name
        FROM appointments a
        JOIN users u ON a.patient_id = u.id
        WHERE a.doctor_id = ?
        ORDER BY a.appointment_date ASC
    """, (doctor['id'],)).fetchall()
    conn.close()
    return jsonify([dict(a) for a in appointments])
@app.route('/api/confirm-appointment/<int:id>', methods=['PUT'])
def confirm_appointment(id):
    conn = get_db()
    conn.execute("UPDATE appointments SET status='confirmed' WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Confirmed!'})

@app.route('/api/admin/appointments', methods=['GET'])
def admin_appointments():
    conn = get_db()
    appointments = conn.execute("""
        SELECT a.*, p.name as patient_name, doc.name as doctor_name, d.specialization
        FROM appointments a
        JOIN users p ON a.patient_id = p.id
        JOIN doctors d ON a.doctor_id = d.id
        JOIN users doc ON d.user_id = doc.id
        ORDER BY a.created_at DESC
    """).fetchall()
    conn.close()
    return jsonify([dict(a) for a in appointments])

@app.route('/api/doctors', methods=['GET'])
def get_doctors():
    conn = get_db()
    doctors = conn.execute("""
        SELECT d.id, u.name, d.specialization, d.experience, d.available_days
        FROM doctors d JOIN users u ON d.user_id = u.id
    """).fetchall()
    conn.close()
    return jsonify([dict(d) for d in doctors])

@app.route('/')
def home():
    with open('templates/index.html', 'r', encoding='utf-8') as f:
        content = f.read()
    return content

if __name__ == '__main__':
    app.run(debug=True)