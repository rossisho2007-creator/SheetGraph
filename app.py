from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify, session
from flask_cors import CORS
from werkzeug.utils import secure_filename
import pandas as pd
import os, re, json, sqlite3
from datetime import datetime
from io import BytesIO
from functools import wraps

app = Flask(__name__, static_folder='static', static_url_path='/static')
CORS(app)
app.config['SECRET_KEY'] = 'autopro-2024'
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

USERS = {
    'hr': {'password': 'hr123', 'role': 'hr', 'name': 'HR Officer', 'branch': 'Head Office'},
    'manager': {'password': 'mgr123', 'role': 'manager', 'name': 'Branch Manager', 'branch': 'Jakarta'},
    'staff': {'password': 'staff123', 'role': 'staff', 'name': 'Staff', 'branch': 'Cabang'},
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

def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        npk TEXT, nama_lengkap TEXT, employee_type TEXT,
        jabatan_gol TEXT, departemen_cabang TEXT, cabang TEXT,
        type_motor TEXT, no_rangka TEXT, no_mesin TEXT, bpkb TEXT,
        loan_amount REAL, down_payment REAL, tenure_months INTEGER,
        interest_rate REAL, tanggal_mulai TEXT,
        principal REAL, total_interest REAL,
        monthly_installment REAL, outstanding_balance REAL,
        remarks TEXT, signature TEXT,
        status_approval TEXT DEFAULT 'Pending',
        submitted_by TEXT, document_source TEXT,
        submitted_date DATETIME DEFAULT CURRENT_TIMESTAMP,
        approved_date DATETIME
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS employee_loans (
        npk TEXT PRIMARY KEY, nama_lengkap TEXT,
        active_loan_count INTEGER DEFAULT 0,
        total_loans_ever INTEGER DEFAULT 0, last_loan_date DATETIME
    )''')
    conn.commit(); conn.close()

init_db()

KNOWN_DEPARTMENTS = ['AR Management', 'Finance', 'Sales', 'Marketing', 'Operation', 'HR Service', 'Collection', 'Credit', 'Service']
KNOWN_BRANCHES = ['Jakarta Pusat','Jakarta Selatan','Jakarta Utara','Bandung','Surabaya','Medan','Makassar','Denpasar','Palembang','Balikpapan','Batam','Yogyakarta','Semarang','Malang','Bekasi','Tangerang','Depok','Bogor','Padang','Pekanbaru','Samarinda','Banjarmasin','Manado','Lampung','Jambi','Bengkulu','Cirebon','Serang','Karawang','Kediri','Jember','Pontianak','Kendari','Palu','Banda Aceh','Duri','Kelapa Gading','BSD City','Tegal']

def do_ocr(path):
    try:
        from PIL import Image, ImageEnhance
        import pytesseract
        img = Image.open(path)
        if img.width > 2000: r = 2000/img.width; img = img.resize((2000, int(img.height*r)))
        img = img.convert('L')
        img = ImageEnhance.Contrast(img).enhance(3.0)
        img = ImageEnhance.Sharpness(img).enhance(2.0)
        return pytesseract.image_to_string(img, lang='eng+ind', config='--psm 6').strip()
    except: return ''

def smart_parse(text):
    if not text: return {}, {}, text
    result, conf = {}, {}
    tests = [
        ('npk', r'(?:NPK|NOPEK|No\.?\s*Pokok|NIK)\s*:?\s*(\d{3,})'),
        ('nama_lengkap', r'(?:Nama|Name)\s*:\s*([A-Za-z\s\.]{3,60})'),
        ('jabatan_gol', r'(?:Jabatan|Gol)\s*(?:/|\s)*:\s*([A-Za-z0-9\s\/\-\.]{2,30})'),
        ('departemen_cabang', r'(?:Departemen|Divisi|Bagian)\s*:\s*([A-Za-z0-9\s\/\-\.]{3,60})'),
        ('cabang', r'(?:Cabang|Kantor|Lokasi)\s*:\s*([A-Za-z\s\-]{3,40})'),
        ('no_rangka', r'(?:No\.?\s*Rangka|Rangka)\s*:\s*([A-Za-z0-9\-]{8,30})'),
        ('no_mesin', r'(?:No\.?\s*Mesin|Mesin)\s*:\s*([A-Za-z0-9\-]{8,30})'),
        ('bpkb', r'(?:BPKB)\s*:\s*([A-Za-z0-9\-]{5,30})'),
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
        rate = 0.05
        if loan <= 0 or tenor <= 0: return {}
        p = loan - dp; ti = p * rate * (tenor/12); m = (p + ti) / tenor
        return {'principal': round(p), 'total_interest': round(ti), 'monthly_installment': round(m), 'outstanding_balance': round(p+ti)}
    except: return {}

def generate_excel(export_type='full'):
    conn = get_db()
    if export_type == 'approved': df = pd.read_sql_query("SELECT * FROM submissions WHERE status_approval='Approved'", conn)
    elif export_type == 'pending': df = pd.read_sql_query("SELECT * FROM submissions WHERE status_approval='Pending'", conn)
    else: df = pd.read_sql_query("SELECT * FROM submissions", conn)
    conn.close()
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as w: df.to_excel(w, index=False)
    output.seek(0)
    return output

# ROUTES
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = request.form.get('username','').strip().lower()
        p = request.form.get('password','')
        if u in USERS and USERS[u]['password'] == p:
            session['user'] = u; session['role'] = USERS[u]['role']
            session['name'] = USERS[u]['name']
            session['branch'] = request.form.get('branch', USERS[u]['branch'])
            return redirect(url_for('dashboard'))
        role = request.form.get('role','staff')
        name = request.form.get('name','')
        branch = request.form.get('branch','Jakarta Pusat')
        if u and len(u) >= 2:
            session['user'] = u; session['role'] = role
            session['name'] = name if name else f'User-{u}'
            session['branch'] = branch
            return redirect(url_for('dashboard'))
        flash('Nama pengguna tidak valid', 'danger')
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
    if request.method == 'POST':
        file = request.files.get('document')
        if file and file.filename:
            path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(path)
            raw = do_ocr(path)
            try: os.remove(path)
            except: pass
            if raw:
                parsed, conf, raw = smart_parse(raw)
                if parsed: calc = calculate(parsed)
    return render_template('scan.html', parsed=parsed, confidence=conf, raw_text=raw, calculations=calc)

@app.route('/form')
@login_required
def online_form(): return render_template('cabang_form.html')

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
        conn = get_db()
        conn.execute('''INSERT INTO submissions 
            (npk,nama_lengkap,employee_type,jabatan_gol,departemen_cabang,cabang,
             type_motor,no_rangka,no_mesin,bpkb,
             loan_amount,down_payment,tenure_months,interest_rate,tanggal_mulai,
             principal,total_interest,monthly_installment,outstanding_balance,
             remarks,signature,submitted_by,document_source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (data.get('npk'),data.get('nama_lengkap'),data.get('employee_type','field'),
             data.get('jabatan_gol'),data.get('departemen_cabang'),data.get('cabang',session.get('branch')),
             data.get('type_motor'),data.get('no_rangka'),data.get('no_mesin'),data.get('bpkb'),
             data.get('loan_amount',16000000),data.get('down_payment',0),
             data.get('tenure_months',36),data.get('interest_rate',0.05),data.get('tanggal_mulai'),
             data.get('principal'),data.get('total_interest'),
             data.get('monthly_installment'),data.get('outstanding_balance'),
             data.get('remarks',''),data.get('signature',''),
             session.get('name'),data.get('document_source','Manual')))
        conn.execute('''INSERT OR REPLACE INTO employee_loans (npk, nama_lengkap, active_loan_count, total_loans_ever, last_loan_date)
                      VALUES (?,?,COALESCE((SELECT active_loan_count FROM employee_loans WHERE npk=?),0)+1,COALESCE((SELECT total_loans_ever FROM employee_loans WHERE npk=?),0)+1,?)''',
                   (data.get('npk'),data.get('nama_lengkap'),data.get('npk'),data.get('npk'),datetime.now()))
        conn.commit(); conn.close()
        return jsonify({'status':'success','calculations':calc})
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}),500

@app.route('/api/approve/<int:id>', methods=['POST'])
@login_required
def approve(id):
    if session.get('role') not in ['hr','manager']: return jsonify({'status':'error'}), 403
    conn = get_db()
    conn.execute("UPDATE submissions SET status_approval='Approved', approved_date=CURRENT_TIMESTAMP WHERE id=?",(id,))
    conn.commit(); conn.close()
    return jsonify({'status':'ok'})

@app.route('/api/reject/<int:id>', methods=['POST'])
@login_required
def reject(id):
    if session.get('role') not in ['hr','manager']: return jsonify({'status':'error'}), 403
    conn = get_db()
    conn.execute("UPDATE submissions SET status_approval='Rejected' WHERE id=?",(id,))
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
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, numbers
    from openpyxl.utils import get_column_letter
    
    wb = Workbook()
    ws = wb.active
    ws.title = "EDLIN Calculation"
    
    # Inputs
    otr = float(sub.get('loan_amount', 24389653))
    dp = float(sub.get('down_payment', 6000000))
    tenor = int(sub.get('tenure_months', 36))
    rate = 0.069  # COF + 1% = 6.9%
    monthly_rate = rate / 12
    insurance = otr * 0.053  # ~5.3% insurance
    
    sisa = otr - dp
    total_principal = sisa + insurance
    monthly_installment = round(-1 * (total_principal * monthly_rate * (1 + monthly_rate)**tenor) / ((1 + monthly_rate)**tenor - 1), 0)
    total_ar = monthly_installment * tenor
    total_interest = total_ar - total_principal
    
    # Style
    bold = Font(bold=True, size=11)
    title_font = Font(bold=True, size=14)
    header_fill = PatternFill(start_color='F2F2F2', end_color='F2F2F2', fill_type='solid')
    currency_fmt = '#,##0'
    
    # Title
    ws['A1'] = 'Skema Jurnal EDLIN'
    ws['A1'].font = title_font
    
    # Employee info
    ws['A2'] = 'Nama Karyawan'
    ws['B2'] = sub.get('nama_lengkap', '')
    ws['A3'] = 'NPK'
    ws['B3'] = sub.get('npk', '')
    
    # Financial inputs
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
    
    # Amortization header
    row = 29
    headers = ['Month', 'Principal', 'Interest', 'Installment', 'Balance']
    for i, h in enumerate(headers):
        cell = ws.cell(row=row, column=i+1, value=h)
        cell.font = bold
        cell.fill = header_fill
    
    # Amortization schedule
    balance = total_principal
    total_principal_paid = 0
    total_interest_paid = 0
    
    ws.cell(row=row+1, column=5, value=balance).number_format = currency_fmt  # Month 0
    
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
    
    # Sum row
    sum_row = row + 2 + tenor
    ws.cell(row=sum_row, column=2, value=round(total_principal_paid)).number_format = currency_fmt
    ws.cell(row=sum_row, column=3, value=round(total_interest_paid)).number_format = currency_fmt
    ws.cell(row=sum_row, column=2).font = bold
    ws.cell(row=sum_row, column=3).font = bold
    
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    return send_file(output, download_name=f'EDLIN_{sub.get("npk","")}_{sub.get("nama_lengkap","")}.xlsx',
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/')
def index():
    if 'user' in session: return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
