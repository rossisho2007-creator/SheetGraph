from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify, session
from flask_cors import CORS
from werkzeug.utils import secure_filename
import pandas as pd
import os, re, json, sqlite3, uuid
from datetime import datetime, date
from io import BytesIO
from functools import wraps

app = Flask(__name__, static_folder='static', static_url_path='/static')
CORS(app)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-only-not-secure-change-me')
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

USERS = {
    'hr': {
        'password': os.environ.get('HR_PASSWORD', 'change-me-hr'),
        'role': 'hr', 'name': 'HR Officer', 'branch': 'Head Office'
    },
    'manager': {
        'password': os.environ.get('MANAGER_PASSWORD', 'change-me-manager'),
        'role': 'manager', 'name': 'Branch Manager', 'branch': 'Jakarta'
    },
    'staff': {
        'password': os.environ.get('STAFF_PASSWORD', 'change-me-staff'),
        'role': 'staff', 'name': 'Staff', 'branch': 'Cabang'
    },
}

def login_required(f):
    @wraps(f)
    def d(*a, **k):
        if 'user' not in session: return redirect(url_for('login'))
        return f(*a, **k)
    return d

def get_db():
    conn = sqlite3.connect('data.db')
    conn.row_factory = sqlite3.Row
    return conn

def get_setting(key, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else default

def get_setting_full(key):
    conn = get_db()
    row = conn.execute("SELECT value, updated_by, updated_at FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return dict(row) if row else None

def set_setting(key, value, updated_by):
    conn = get_db()
    conn.execute("""INSERT INTO settings (key, value, updated_by, updated_at) VALUES (?,?,?,?)
                     ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                     updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
                 (key, value, updated_by, datetime.now()))
    conn.commit()
    conn.close()

def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT,
        npk TEXT, nama_lengkap TEXT, employee_type TEXT,
        jabatan_gol TEXT, departemen_cabang TEXT, cabang TEXT,
        type_motor TEXT, no_rangka TEXT, no_mesin TEXT, bpkb TEXT,
        loan_amount REAL, down_payment REAL, tenure_months INTEGER,
        interest_rate REAL, tanggal_mulai TEXT,
        principal REAL, total_interest REAL,
        monthly_installment REAL, outstanding_balance REAL,
        remarks TEXT, signature TEXT,
        email TEXT, phone TEXT,
        status_approval TEXT DEFAULT 'Pending',
        rejection_reason TEXT,
        submitted_by TEXT, document_source TEXT,
        submitted_date DATETIME DEFAULT CURRENT_TIMESTAMP,
        approved_date DATETIME
    )''')
    for col, coltype in [('application_id', 'TEXT'), ('email', 'TEXT'),
                          ('phone', 'TEXT'), ('rejection_reason', 'TEXT')]:
        try:
            conn.execute(f'ALTER TABLE submissions ADD COLUMN {col} {coltype}')
        except sqlite3.OperationalError:
            pass
    conn.execute('''CREATE TABLE IF NOT EXISTS employee_loans (
        npk TEXT PRIMARY KEY, nama_lengkap TEXT,
        email TEXT, phone TEXT,
        active_loan_count INTEGER DEFAULT 0,
        total_loans_ever INTEGER DEFAULT 0, last_loan_date DATETIME
    )''')
    for col, coltype in [('email', 'TEXT'), ('phone', 'TEXT')]:
        try:
            conn.execute(f'ALTER TABLE employee_loans ADD COLUMN {col} {coltype}')
        except sqlite3.OperationalError:
            pass
    conn.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_by TEXT,
        updated_at DATETIME
    )''')
    try:
        conn.execute('ALTER TABLE submissions ADD COLUMN insurance_amount REAL')
    except sqlite3.OperationalError:
        pass
    conn.execute('''CREATE TABLE IF NOT EXISTS loan_schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL,
        month_number INTEGER NOT NULL,
        due_date TEXT NOT NULL,
        expected_amount REAL,
        balance_after REAL,
        is_paid INTEGER DEFAULT 0,
        paid_date DATETIME
    )''')
    conn.commit(); conn.close()

init_db()

KNOWN_DEPARTMENTS = ['AR Management', 'Finance', 'Sales', 'Marketing', 'Operation', 'HR Service', 'Collection', 'Credit', 'Service']
KNOWN_BRANCHES = ['Jakarta Pusat','Jakarta Selatan','Jakarta Utara','Bandung','Surabaya','Medan','Makassar','Denpasar','Palembang','Balikpapan','Batam','Yogyakarta','Semarang','Malang','Bekasi','Tangerang','Depok','Bogor','Padang','Pekanbaru','Samarinda','Banjarmasin','Manado','Lampung','Jambi','Bengkulu','Cirebon','Serang','Karawang','Kediri','Jember','Pontianak','Kendari','Palu','Banda Aceh','Duri','Kelapa Gading','BSD City','Tegal']

def add_months(d, months):
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    days_in_month = [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    day = min(d.day, days_in_month[month - 1])
    return date(year, month, day)

_ocr_reader = None

def get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None: 
        import easyocr
        _ocr_reader = easyocr.Reader(['id', 'en'])
    return _ocr_reader

def do_ocr(path):
    try:
        reader = get_ocr_reader()
        results = reader.readtext(path)
        return ' '.join([r[1] for r in results]).strip()
    except Exception as e:
        print(f"OCR failed: {e}")
        return ''

NEXT_LABEL = r'(?:No\.?\s*Pokok|NPK|NOPEK|NIK|Jabatan\s*/?\s*Gol|Departemen|Divisi|Cabang|Bagian|Tgl\s*Masuk|Tgl\s*Pengangkatan|Kantor|Lokasi|No\.?\s*Rangka|Rangka|No\.?\s*Mesin|Mesin|BPKB|$)'

def smart_parse(text):
    if not text: return {}, {}, text
    result, conf = {}, {}
    tests = [
        ('npk', r'(?:NPK|NOPEK|No\.?\s*Pokok(?:\s*Karyawan)?|NIK)\s*:?\s*(\d{3,})'),
        ('nama_lengkap', r'(?:Nama|Name)\s*:\s*([A-Za-z\s\.]{3,60}?)(?=\s*' + NEXT_LABEL + r')'),
        ('jabatan_gol', r'Jabatan\s*/?\s*Gol\s*:\s*([A-Za-z0-9\s\/\-\.]{2,30}?)(?=\s*' + NEXT_LABEL + r')'),
        ('departemen_cabang', r'Departemen\s*/?\s*Divisi\s*/?\s*Cabang\s*:\s*([A-Za-z0-9\s\/\-\.]{3,60}?)(?=\s*' + NEXT_LABEL + r')'),
        ('no_rangka', r'(?:No\.?\s*Rangka|Rangka)\s*:\s*([A-Za-z0-9\-]{5,30}?)(?=\s*' + NEXT_LABEL + r')'),
        ('no_mesin', r'(?:No\.?\s*Mesin|Mesin)\s*:\s*([A-Za-z0-9\-]{5,30}?)(?=\s*' + NEXT_LABEL + r')'),
        ('bpkb', r'(?:BPKB)\s*:\s*([A-Za-z0-9\-]{3,30}?)(?=\s*' + NEXT_LABEL + r')'),
    ]
    for field, pat in tests:
        try:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                v = m.group(1).strip()
                if field == 'npk':
                    v = re.sub(r'[^0-9]', '', v)
                    if len(v) < 3: continue
                if v:
                    result[field] = v; conf[field] = 90
        except: pass
    for known in KNOWN_BRANCHES:
        if known.lower() in text.lower():
            result['cabang'] = known; conf['cabang'] = 85; break
    for known in KNOWN_DEPARTMENTS:
        if known.lower() in text.lower():
            result['departemen_cabang'] = known; conf['departemen_cabang'] = 85; break
    if 'operasional' in text.lower() or 'surveyor' in text.lower() or 'fro' in text.lower():
        result['employee_type'] = 'field'
    return result, conf, text

def calculate(data):
    try:
        loan = float(data.get('loan_amount', 16000000))
        dp = float(data.get('down_payment', 0))
        tenor = int(data.get('tenure_months', 36))
        if data.get('employee_type') == 'office':
            rate = float(get_setting('office_interest_rate', '0.05'))
        else:
            rate = 0.05
        if loan <= 0 or tenor <= 0: return {}
        p = loan - dp; ti = p * rate * (tenor/12); m = (p + ti) / tenor
        return {'principal': round(p), 'total_interest': round(ti), 'monthly_installment': round(m), 'outstanding_balance': round(p+ti), 'interest_rate': rate}
    except: return {}

def generate_excel(export_type='full'):
    conn = get_db()
    if export_type == 'approved': df = pd.read_sql_query("SELECT * FROM submissions WHERE status_approval='Approved'", conn)
    elif export_type == 'pending': df = pd.read_sql_query("SELECT * FROM submissions WHERE status_approval='Pending'", conn)
    else: df = pd.read_sql_query("SELECT * FROM submissions", conn)
    conn.close()
    if 'signature' in df.columns:
        df = df.drop(columns=['signature'])
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as w: df.to_excel(w, index=False)
    output.seek(0)
    return output

@app.route('/export-excel/pending')
@login_required
def export_pending():
    if session.get('role') not in ['hr','manager']: return redirect(url_for('dashboard'))
    return send_file(generate_excel('pending'), download_name=f'Pending_{datetime.now().strftime("%Y%m%d")}.xlsx')


@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = request.form.get('username','').strip().lower()
        p = request.form.get('password','')

        # Path 1: privileged accounts (hr / manager / staff-demo) - real password required
        if u in USERS and p and USERS[u]['password'] == p:
            session['user'] = u
            session['role'] = USERS[u]['role']
            session['name'] = USERS[u]['name']
            session['branch'] = USERS[u]['branch']
            return redirect(url_for('dashboard'))

        # Path 2: regular employee NPK login - no password, role ALWAYS forced
        # to 'staff' server-side. Client-submitted role is never trusted.
        name = request.form.get('name','').strip()
        branch = request.form.get('branch','').strip()
        if u and len(u) >= 2 and not p and name and branch:
            session['user'] = u
            session['role'] = 'staff'
            session['name'] = name
            session['branch'] = branch
            return redirect(url_for('dashboard'))

        flash('Nama pengguna atau kata sandi salah', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    role = session.get('role'); name = session.get('name')
    if role in ['hr','manager']:
        stats = {
            'pending': conn.execute("SELECT COUNT(*) FROM submissions WHERE status_approval='Pending'").fetchone()[0],
            'approved': conn.execute("SELECT COUNT(*) FROM submissions WHERE status_approval='Approved'").fetchone()[0],
            'total': conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0],
            'active_loans': conn.execute("SELECT COUNT(*) FROM employee_loans WHERE active_loan_count > 0").fetchone()[0],
        }
        recent = [dict(r) for r in conn.execute("SELECT * FROM submissions ORDER BY submitted_date DESC LIMIT 50")]
    else:
        stats = {
            'pending': conn.execute("SELECT COUNT(*) FROM submissions WHERE status_approval='Pending' AND submitted_by=?",(name,)).fetchone()[0],
            'approved': conn.execute("SELECT COUNT(*) FROM submissions WHERE status_approval='Approved' AND submitted_by=?",(name,)).fetchone()[0],
        }
        recent = [dict(r) for r in conn.execute("SELECT * FROM submissions WHERE submitted_by=? ORDER BY submitted_date DESC LIMIT 50",(name,))]
    conn.close()
    return render_template('dashboard.html', stats=stats, recent=recent)

@app.route('/scan', methods=['GET','POST'])
@login_required
def scan():
    parsed, conf, raw, calc = None, None, None, None
    office_rate_percent = float(get_setting('office_interest_rate', '0.05')) * 100
    if request.method == 'POST':
        file = request.files.get('document')
        if file and file.filename:
            if not allowed_file(file.filename):
                flash('File harus berupa gambar (PNG, JPG, JPEG)', 'danger')
                return render_template('scan.html', parsed=parsed, confidence=conf, raw_text=raw, calculations=calc, office_rate_percent=office_rate_percent)
            path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(path)
            raw = do_ocr(path)
            try: os.remove(path)
            except: pass
            if raw:
                parsed, conf, raw = smart_parse(raw)
                if parsed: calc = calculate(parsed)
    return render_template('scan.html', parsed=parsed, confidence=conf, raw_text=raw, calculations=calc, office_rate_percent=office_rate_percent)

@app.route('/form')
@login_required
def online_form():
    office_rate_percent = float(get_setting('office_interest_rate', '0.05')) * 100
    return render_template('cabang_form.html', office_rate_percent=office_rate_percent)

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if session.get('role') != 'hr':
        flash('Hanya HR yang bisa mengakses halaman ini', 'danger')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        raw_pct = request.form.get('office_rate_percent', '').strip().replace(',', '.')
        try:
            pct = float(raw_pct)
            if pct <= 0 or pct > 100:
                flash('Persentase harus antara 0 dan 100', 'danger')
            else:
                set_setting('office_interest_rate', str(pct / 100), session.get('name'))
                flash(f'Suku bunga Non-Operasional diperbarui menjadi {pct}%', 'success')
        except ValueError:
            flash('Masukkan angka yang valid, cth. 8.5', 'danger')
        return redirect(url_for('settings'))

    current_percent = float(get_setting('office_interest_rate', '0.05')) * 100
    audit = get_setting_full('office_interest_rate')
    return render_template('settings.html', current_percent=current_percent, audit=audit)

@app.route('/calculator')
@login_required
def calculator(): return render_template('calculator.html')

@app.route('/api/submit', methods=['POST'])
@login_required
def submit():
    try:
        data = request.json
        calc = calculate(data)
        if not calc: return jsonify({'status':'error'}), 400
        data.update(calc)
        application_id = f"TAF-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
        conn = get_db()
        conn.execute('''INSERT INTO submissions 
            (application_id,npk,nama_lengkap,employee_type,jabatan_gol,departemen_cabang,cabang,
             type_motor,no_rangka,no_mesin,bpkb,
             loan_amount,down_payment,tenure_months,interest_rate,tanggal_mulai,
             principal,total_interest,monthly_installment,outstanding_balance,
             remarks,signature,email,phone,submitted_by,document_source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (application_id,data.get('npk'),data.get('nama_lengkap'),data.get('employee_type','field'),
             data.get('jabatan_gol'),data.get('departemen_cabang'),data.get('cabang',session.get('branch')),
             data.get('type_motor'),data.get('no_rangka'),data.get('no_mesin'),data.get('bpkb'),
             data.get('loan_amount',16000000),data.get('down_payment',0),
             data.get('tenure_months',36),data.get('interest_rate',0.05),data.get('tanggal_mulai'),
             data.get('principal'),data.get('total_interest'),
             data.get('monthly_installment'),data.get('outstanding_balance'),
             data.get('remarks',''),data.get('signature',''),
             data.get('email',''),data.get('phone',''),
             session.get('name'),data.get('document_source','Manual')))
        conn.execute('''INSERT OR REPLACE INTO employee_loans (npk, nama_lengkap, email, phone, active_loan_count, total_loans_ever, last_loan_date)
                      VALUES (?,?,?,?,COALESCE((SELECT active_loan_count FROM employee_loans WHERE npk=?),0)+1,COALESCE((SELECT total_loans_ever FROM employee_loans WHERE npk=?),0)+1,?)''',
                   (data.get('npk'),data.get('nama_lengkap'),data.get('email',''),data.get('phone',''),data.get('npk'),data.get('npk'),datetime.now()))
        conn.commit(); conn.close()
        return jsonify({'status':'success','application_id':application_id,'calculations':calc})
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}),500

@app.route('/api/approve/<int:id>', methods=['POST'])
@login_required
def approve(id):
    if session.get('role') not in ['hr','manager']: return jsonify({'status':'error'}), 403
    body = request.get_json(silent=True) or {}
    conn = get_db()
    row = conn.execute("SELECT * FROM submissions WHERE id=?", (id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'status':'error','message':'Not found'}), 404
    sub = dict(row)

    loan_amount = float(body.get('loan_amount') if body.get('loan_amount') not in (None,'') else (sub.get('loan_amount') or 16000000))
    down_payment = float(body.get('down_payment') if body.get('down_payment') not in (None,'') else (sub.get('down_payment') or 0))
    tenure_months = int(body.get('tenure_months') if body.get('tenure_months') not in (None,'') else (sub.get('tenure_months') or 36))
    insurance_raw = body.get('insurance_amount')
    if insurance_raw in (None, ''):
        insurance_amount = sub.get('insurance_amount')
        if insurance_amount is None:
            insurance_amount = round(loan_amount * 0.053, 3)
    else:
        insurance_amount = float(insurance_raw)

    calc = calculate({'loan_amount': loan_amount, 'down_payment': down_payment,
                       'tenure_months': tenure_months, 'employee_type': sub.get('employee_type')})
    if not calc:
        conn.close()
        return jsonify({'status':'error','message':'Angka pinjaman tidak valid'}), 400

    conn.execute("UPDATE submissions SET status_approval='Approved', approved_date=CURRENT_TIMESTAMP, "
                 "loan_amount=?, down_payment=?, tenure_months=?, insurance_amount=?, "
                 "principal=?, total_interest=?, monthly_installment=?, outstanding_balance=?, interest_rate=? "
                 "WHERE id=?",
                 (loan_amount, down_payment, tenure_months, insurance_amount,
                  calc['principal'], calc['total_interest'], calc['monthly_installment'],
                  calc['outstanding_balance'], calc['interest_rate'], id))

    conn.execute("DELETE FROM loan_schedule WHERE submission_id=?", (id,))
    start_raw = sub.get('tanggal_mulai') or ''
    try:
        start_date = datetime.strptime(start_raw[:10], '%Y-%m-%d').date()
    except ValueError:
        start_date = datetime.now().date()

    balance = calc['outstanding_balance']
    monthly = calc['monthly_installment']
    for month in range(1, tenure_months + 1):
        due = add_months(start_date, month)
        balance = round(balance - monthly, 2)
        conn.execute("INSERT INTO loan_schedule (submission_id, month_number, due_date, expected_amount, balance_after) "
                     "VALUES (?,?,?,?,?)", (id, month, due.isoformat(), monthly, max(balance, 0)))

    conn.commit(); conn.close()
    return jsonify({'status':'ok'})

@app.route('/api/schedule/mark-paid/<int:schedule_id>', methods=['POST'])
@login_required
def mark_paid(schedule_id):
    if session.get('role') not in ['hr','manager']: return jsonify({'status':'error'}), 403
    body = request.get_json(silent=True) or {}
    paid = bool(body.get('paid', True))
    conn = get_db()
    if paid:
        conn.execute("UPDATE loan_schedule SET is_paid=1, paid_date=CURRENT_TIMESTAMP WHERE id=?", (schedule_id,))
    else:
        conn.execute("UPDATE loan_schedule SET is_paid=0, paid_date=NULL WHERE id=?", (schedule_id,))
    conn.commit(); conn.close()
    return jsonify({'status':'ok'})

@app.route('/reports/schedule')
@login_required
def schedule_report():
    if session.get('role') not in ['hr','manager']:
        return redirect(url_for('dashboard'))
    conn = get_db()
    rows = conn.execute(
        "SELECT ls.*, s.nama_lengkap, s.npk, s.cabang, s.type_motor "
        "FROM loan_schedule ls JOIN submissions s ON s.id = ls.submission_id "
        "WHERE s.status_approval = 'Approved' "
        "ORDER BY s.nama_lengkap, ls.month_number"
    ).fetchall()
    conn.close()

    today = datetime.now().date()
    loans = {}
    for r in rows:
        r = dict(r)
        sid = r['submission_id']
        if sid not in loans:
            loans[sid] = {'nama_lengkap': r['nama_lengkap'], 'npk': r['npk'],
                          'cabang': r['cabang'], 'type_motor': r['type_motor'], 'rows': []}
        due = datetime.strptime(r['due_date'], '%Y-%m-%d').date()
        if r['is_paid']:
            r['live_status'] = 'Paid'
        elif due < today:
            r['live_status'] = 'Overdue'
        elif due.year == today.year and due.month == today.month:
            r['live_status'] = 'Due this month'
        else:
            r['live_status'] = 'Upcoming'
        r['due_date_display'] = due.strftime('%d %b %Y')
        loans[sid]['rows'].append(r)

    return render_template('schedule_report.html', loans=list(loans.values()), today=today.strftime('%d %B %Y'))

@app.route('/api/reject/<int:id>', methods=['POST'])
@login_required
def reject(id):
    if session.get('role') not in ['hr','manager']: return jsonify({'status':'error'}), 403
    body = request.get_json(silent=True) or {}
    reason = body.get('reason', '')
    conn = get_db()
    conn.execute("UPDATE submissions SET status_approval='Rejected', rejection_reason=? WHERE id=?",(reason, id))
    conn.commit(); conn.close()
    return jsonify({'status':'ok'})

@app.route('/export-excel')
@login_required
def export():
    if session.get('role') not in ['hr','manager']: return redirect(url_for('dashboard'))
    return send_file(generate_excel('full'), download_name=f'Report_{datetime.now().strftime("%Y%m%d")}.xlsx')

@app.route('/export-excel/approved')
@login_required
def export_approved():
    if session.get('role') not in ['hr','manager']: return redirect(url_for('dashboard'))
    return send_file(generate_excel('approved'), download_name=f'Approved_{datetime.now().strftime("%Y%m%d")}.xlsx')

SHEET_NAME_FORBIDDEN = [':', '\\', '/', '?', '*', '[', ']']

def sanitize_sheet_name(name):
    name = str(name)
    for ch in SHEET_NAME_FORBIDDEN:
        name = name.replace(ch, '-')
    name = name.strip() or 'Sheet'
    return name[:31]

def unique_sheet_name(base, used):
    name = sanitize_sheet_name(base)
    if name not in used:
        return name
    n = 2
    while True:
        suffix = f' ({n})'
        candidate = sanitize_sheet_name(base)[:31 - len(suffix)] + suffix
        if candidate not in used:
            return candidate
        n += 1

def build_edlin_sheet(ws, sub):
    """Fill one worksheet with the EDLIN-formatted amortization schedule for a submission"""
    from openpyxl.styles import Font, PatternFill

    otr = float(sub.get('loan_amount', 24389653))
    dp = float(sub.get('down_payment', 6000000))
    tenor = int(sub.get('tenure_months', 36))
    rate = 0.069
    monthly_rate = rate / 12
    insurance = otr * 0.053

    sisa = otr - dp
    total_principal = sisa + insurance
    monthly_installment = round(-1 * (total_principal * monthly_rate * (1 + monthly_rate)**tenor) / ((1 + monthly_rate)**tenor - 1), 0)
    total_ar = monthly_installment * tenor
    total_interest = total_ar - total_principal

    bold = Font(bold=True, size=11)
    title_font = Font(bold=True, size=14)
    header_fill = PatternFill(start_color='F2F2F2', end_color='F2F2F2', fill_type='solid')
    currency_fmt = '#,##0'

    ws['A1'] = 'Skema Jurnal EDLIN'
    ws['A1'].font = title_font
    ws['A2'] = 'Nama Karyawan'
    ws['B2'] = sub.get('nama_lengkap', '')
    ws['A3'] = 'NPK'
    ws['B3'] = sub.get('npk', '')

    row = 5
    data = [
        ('OTR', otr), ('DP (min 10%)', dp), ('Sisa', sisa),
        ('Insurance', insurance), ('Total Principal', total_principal),
        ('Interest', total_interest), ('Total AR', total_ar),
        ('Rate', rate), ('Tenor', tenor),
        ('Bunga per bulan', monthly_rate), ('Angsuran total anuitas', monthly_installment)
    ]
    for label, value in data:
        ws[f'A{row}'] = label
        ws[f'B{row}'] = value
        ws[f'B{row}'].number_format = currency_fmt
        row += 2

    row = 29
    headers = ['Month', 'Principal', 'Interest', 'Installment', 'Balance']
    for i, h in enumerate(headers):
        cell = ws.cell(row=row, column=i+1, value=h)
        cell.font = bold
        cell.fill = header_fill

    balance = total_principal
    total_principal_paid = 0
    total_interest_paid = 0

    ws.cell(row=row+1, column=5, value=balance).number_format = currency_fmt

    for month in range(1, tenor + 1):
        r = row + 1 + month
        interest_payment = balance * monthly_rate
        principal_payment = monthly_installment - interest_payment
        balance = round(balance - principal_payment, 0)

        ws.cell(row=r, column=1, value=month)
        ws.cell(row=r, column=2, value=round(principal_payment)).number_format = currency_fmt
        ws.cell(row=r, column=3, value=round(interest_payment)).number_format = currency_fmt
        ws.cell(row=r, column=4, value=monthly_installment).number_format = currency_fmt
        ws.cell(row=r, column=5, value=max(0, balance)).number_format = currency_fmt

        total_principal_paid += principal_payment
        total_interest_paid += interest_payment

    sum_row = row + 2 + tenor
    ws.cell(row=sum_row, column=2, value=round(total_principal_paid)).number_format = currency_fmt
    ws.cell(row=sum_row, column=3, value=round(total_interest_paid)).number_format = currency_fmt
    ws.cell(row=sum_row, column=2).font = bold
    ws.cell(row=sum_row, column=3).font = bold

@app.route('/export-edlin/<int:id>')
@login_required
def export_edlin(id):
    """Generate EDLIN-formatted Excel matching their exact template"""
    if session.get('role') not in ['hr','manager']:
        return redirect(url_for('dashboard'))

    conn = get_db()
    sub = dict(conn.execute("SELECT * FROM submissions WHERE id=?", (id,)).fetchone())
    conn.close()

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "EDLIN Calculation"
    build_edlin_sheet(ws, sub)

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(output, download_name=f'EDLIN_{sub.get("npk","")}_{sub.get("nama_lengkap","")}.xlsx',
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/export-edlin-employee/<npk>')
@login_required
def export_edlin_employee(npk):
    """Generate one EDLIN-formatted workbook with a sheet per submission for this NPK's full loan history"""
    if session.get('role') not in ['hr','manager']:
        return redirect(url_for('dashboard'))

    conn = get_db()
    subs = [dict(r) for r in conn.execute(
        "SELECT * FROM submissions WHERE npk=? ORDER BY submitted_date ASC", (npk,)).fetchall()]
    conn.close()

    if not subs:
        flash('Tidak ada pengajuan untuk NPK ini', 'danger')
        return redirect(url_for('dashboard'))

    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    used_names = set()
    for i, sub in enumerate(subs, 1):
        name = unique_sheet_name(f"{i}. {sub.get('application_id') or sub.get('id')}", used_names)
        used_names.add(name)
        ws = wb.create_sheet(title=name)
        build_edlin_sheet(ws, sub)

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    nama = subs[-1].get('nama_lengkap', '')
    return send_file(output, download_name=f'EDLIN_{npk}_{nama}_History.xlsx',
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/')
def index():
    if 'user' in session: return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug_mode, host='0.0.0.0', port=5000)
