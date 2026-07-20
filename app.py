"""
AutoInput Pro - Complete Dual Role System
🔵 Cabang: Scan/Form upload only
🔴 HR: Dashboard, approve/reject, scan, Excel import/export
"""

from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify, session
from flask_cors import CORS
from werkzeug.utils import secure_filename
import pandas as pd
import os, re, json, sqlite3
from datetime import datetime
from io import BytesIO
from functools import wraps

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = 'autopro-2024'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB for Excel files
ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv', 'pdf', 'png', 'jpg', 'jpeg'}
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ═══════════════════════════
# USERS
# ═══════════════════════════
USERS = {
    'hr': {'password': 'hr123', 'role': 'hr', 'name': 'HR Officer'},
    'cabang1': {'password': 'cabang123', 'role': 'cabang', 'name': 'Jakarta South'},
    'cabang2': {'password': 'cabang123', 'role': 'cabang', 'name': 'Bandung'},
}

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def hr_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'hr': flash('HR only', 'danger'); return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def cabang_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'cabang': flash('Cabang only', 'danger'); return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ═══════════════════════════
# DATABASE
# ═══════════════════════════
def get_db():
    conn = sqlite3.connect('data.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        npk TEXT, kpm_id TEXT, nama_lengkap TEXT,
        loan_amount REAL, down_payment REAL, total_ar REAL,
        tanggal_mulai TEXT, tenure_months INTEGER,
        loan_type TEXT, interest_rate REAL, cabang TEXT,
        principal REAL, total_interest REAL,
        monthly_installment REAL, outstanding_balance REAL,
        status_approval TEXT DEFAULT 'Pending',
        submitted_by TEXT, approved_by TEXT,
        document_source TEXT, ocr_confidence REAL,
        submitted_date DATETIME DEFAULT CURRENT_TIMESTAMP,
        approved_date DATETIME
    )''')
    conn.commit()
    conn.close()

init_db()

# ═══════════════════════════
# OCR ENGINE
# ═══════════════════════════
def ocr_scan_image(image_path):
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(image_path).convert('L')
        return pytesseract.image_to_string(img, lang='eng+ind').strip()
    except:
        return None

def parse_document(text):
    if not text: return {}, {}
    
    patterns = {
        'npk': r'(?:NPK|NOPEK)[:\s]*([A-Za-z0-9\-]{2,20})',
        'kpm_id': r'(?:KPM)[:\s\-]*(\d{10,})',
        'nama_lengkap': r'(?:Nama|Customer|Debitur)[:\s]*([A-Za-z\s\.]{3,60})',
        'loan_amount': r'(?:Loan|Pinjaman|AF|Plafon)[:\s]*[Rp\.\s]*([\d,\.]{5,})',
        'down_payment': r'(?:DP|Down\s*Payment|Uang\s*Muka)[:\s]*[Rp\.\s]*([\d,\.]{4,})',
        'total_ar': r'(?:Total\s*AR|Piutang)[:\s]*[Rp\.\s]*([\d,\.]{4,})',
        'tanggal_mulai': r'(?:Tanggal|Tgl)[:\s]*(\d{1,2}[\s/\-\.]\d{1,2}[\s/\-\.]\d{2,4})',
        'tenure_months': r'(?:Tenure|Tenor|Jangka)[:\s]*(\d{1,3})',
        'loan_type': r'(?:Type|Jenis|Produk)[:\s]*(Regular|Fleet|Siap\s*Dana|KINTO)',
        'interest_rate': r'(?:Interest|Bunga|Rate)[:\s]*([\d.,]{1,5})\s*%?',
        'cabang': r'(?:Cabang|Branch|Kantor)[:\s]*([A-Za-z\s\-]{3,40})',
    }
    
    result, conf = {}, {}
    for field, pattern in patterns.items():
        m = re.search(pattern, text, re.IGNORECASE|re.MULTILINE)
        if m:
            v = m.group(1).strip()
            if not v: continue
            try:
                if field in ['loan_amount','down_payment','total_ar']:
                    v = float(re.sub(r'[^\d]','',v))
                elif field == 'tenure_months':
                    v = int(re.sub(r'[^\d]','',v))
                elif field == 'interest_rate':
                    v = float(v.replace(',','.'))
                    if v > 1: v /= 100
                result[field] = v; conf[field] = 85
            except: pass
    return result, conf

def calculate(data):
    try:
        loan = float(data.get('loan_amount',0))
        dp = float(data.get('down_payment',0))
        tenor = int(data.get('tenure_months',12))
        rate = float(data.get('interest_rate',0.05))
        if loan <= 0 or tenor <= 0: return {}
        principal = loan - dp
        total_int = principal * rate * (tenor/12)
        monthly = (principal + total_int) / tenor
        return {'principal':round(principal),'total_interest':round(total_int),
                'monthly_installment':round(monthly),'outstanding_balance':round(principal+total_int)}
    except: return {}

# ═══════════════════════════
# EXCEL PROCESSOR (for existing files)
# ═══════════════════════════
def process_excel_file(filepath):
    """Read and process existing Excel files"""
    try:
        if filepath.endswith('.csv'):
            df = pd.read_csv(filepath)
        else:
            xl = pd.ExcelFile(filepath)
            df = pd.read_excel(filepath, sheet_name=xl.sheet_names[0])
        
        # Clean column names
        df.columns = [str(c).strip() for c in df.columns]
        
        # Try to map columns
        column_map = {}
        for col in df.columns:
            cl = col.lower()
            if 'npk' in cl: column_map['npk'] = col
            elif 'kpm' in cl: column_map['kpm_id'] = col
            elif 'nama' in cl or 'name' in cl: column_map['nama_lengkap'] = col
            elif 'loan' in cl or 'pinjaman' in cl or 'af' in cl: column_map['loan_amount'] = col
            elif 'dp' in cl or 'down' in cl or 'muka' in cl: column_map['down_payment'] = col
            elif 'ar' in cl or 'piutang' in cl: column_map['total_ar'] = col
            elif 'tanggal' in cl or 'date' in cl or 'tgl' in cl: column_map['tanggal_mulai'] = col
            elif 'tenor' in cl or 'tenure' in cl: column_map['tenure_months'] = col
            elif 'type' in cl or 'jenis' in cl: column_map['loan_type'] = col
            elif 'bunga' in cl or 'interest' in cl or 'rate' in cl: column_map['interest_rate'] = col
            elif 'cabang' in cl or 'branch' in cl: column_map['cabang'] = col
        
        processed = []
        for _, row in df.iterrows():
            data = {}
            for key, col in column_map.items():
                val = row[col]
                if pd.isna(val): val = 0 if key in ['loan_amount','down_payment','total_ar','tenure_months','interest_rate'] else ''
                data[key] = val
            
            calc = calculate(data)
            data.update(calc)
            processed.append(data)
        
        return processed, list(column_map.keys())
    except Exception as e:
        return [], str(e)

def save_to_db(data_list, source='Excel Import'):
    conn = get_db()
    count = 0
    for data in data_list:
        try:
            conn.execute('''INSERT OR IGNORE INTO submissions 
                (npk, kpm_id, nama_lengkap, loan_amount, down_payment, total_ar,
                 tanggal_mulai, tenure_months, loan_type, interest_rate, cabang,
                 principal, total_interest, monthly_installment, outstanding_balance,
                 submitted_by, document_source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (str(data.get('npk','')), str(data.get('kpm_id','')),
                 str(data.get('nama_lengkap','')),
                 float(data.get('loan_amount',0)), float(data.get('down_payment',0)),
                 float(data.get('total_ar',0)), str(data.get('tanggal_mulai','')),
                 int(data.get('tenure_months',12)), str(data.get('loan_type','Regular')),
                 float(data.get('interest_rate',0.05)), str(data.get('cabang','')),
                 float(data.get('principal',0)), float(data.get('total_interest',0)),
                 float(data.get('monthly_installment',0)), float(data.get('outstanding_balance',0)),
                 session.get('name','System'), source))
            count += 1
        except: pass
    conn.commit()
    conn.close()
    return count

# ═══════════════════════════
# ROUTES
# ═══════════════════════════

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = request.form.get('username','').lower().strip()
        p = request.form.get('password','')
        if u in USERS and USERS[u]['password'] == p:
            session['user'] = u; session['role'] = USERS[u]['role']; session['name'] = USERS[u]['name']
            flash(f'Welcome, {USERS[u]["name"]}!', 'success')
            return redirect(url_for('hr_dashboard') if USERS[u]['role']=='hr' else url_for('cabang_upload'))
        flash('Invalid credentials', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('login'))

# 🔴 HR ROUTES
@app.route('/hr')
@hr_required
def hr_dashboard():
    conn = get_db()
    stats = {
        'pending': conn.execute("SELECT COUNT(*) FROM submissions WHERE status_approval='Pending'").fetchone()[0],
        'approved': conn.execute("SELECT COUNT(*) FROM submissions WHERE status_approval='Approved'").fetchone()[0],
        'total': conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0],
        'amount': conn.execute("SELECT COALESCE(SUM(loan_amount),0) FROM submissions").fetchone()[0],
    }
    recent = [dict(r) for r in conn.execute(
        "SELECT * FROM submissions ORDER BY submitted_date DESC LIMIT 100").fetchall()]
    conn.close()
    return render_template('hr_dashboard.html', stats=stats, recent=recent)

@app.route('/hr/scan', methods=['GET','POST'])
@hr_required
def hr_scan():
    """HR can also scan documents"""
    parsed, confidence, raw_text, calculations = None, None, None, None
    
    if request.method == 'POST':
        file = request.files.get('document')
        if file and file.filename:
            path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(path)
            raw_text = ocr_scan_image(path)
            if raw_text:
                parsed, confidence = parse_document(raw_text)
                if parsed:
                    calculations = calculate(parsed)
                    flash(f'✅ Found {len(parsed)} fields!', 'success')
                else:
                    flash('⚠️ No data detected. Try clearer image.', 'warning')
            else:
                flash('⚠️ OCR failed.', 'warning')
    
    return render_template('hr_scan.html', parsed=parsed, confidence=confidence,
                         raw_text=raw_text, calculations=calculations)

@app.route('/hr/import-excel', methods=['GET','POST'])
@hr_required
def hr_import_excel():
    """HR can import existing Excel files"""
    results = None
    
    if request.method == 'POST':
        file = request.files.get('excel_file')
        if file and file.filename:
            path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(path)
            
            if file.filename.endswith(('.xlsx','.xls','.csv')):
                processed, columns = process_excel_file(path)
                if processed:
                    count = save_to_db(processed, f'Excel Import: {file.filename}')
                    results = {'count': count, 'columns': columns, 'sample': processed[:3]}
                    flash(f'✅ Imported {count} records from {file.filename}!', 'success')
                else:
                    flash(f'⚠️ Could not process file: {columns}', 'warning')
            else:
                flash('⚠️ Please upload .xlsx, .xls, or .csv file', 'warning')
    
    return render_template('hr_import.html', results=results)

@app.route('/hr/export-excel')
@hr_required
def hr_export():
    """Export all data to Excel"""
    conn = get_db()
    df = pd.read_sql_query("SELECT * FROM submissions ORDER BY submitted_date DESC", conn)
    conn.close()
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='All Submissions', index=False)
    output.seek(0)
    
    return send_file(output, 
                    download_name=f'HR_Report_{datetime.now().strftime("%Y%m%d_%H%M")}.xlsx',
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/hr/export-approved')
@hr_required
def hr_export_approved():
    """Export only approved submissions"""
    conn = get_db()
    df = pd.read_sql_query(
        "SELECT * FROM submissions WHERE status_approval='Approved' ORDER BY approved_date DESC", conn)
    conn.close()
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Approved', index=False)
    output.seek(0)
    
    return send_file(output,
                    download_name=f'HR_Approved_{datetime.now().strftime("%Y%m%d")}.xlsx',
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/api/approve/<int:id>', methods=['POST'])
@hr_required
def approve(id):
    conn = get_db()
    conn.execute("UPDATE submissions SET status_approval='Approved', approved_by=?, approved_date=CURRENT_TIMESTAMP WHERE id=?",
                 (session.get('name','HR'), id))
    conn.commit(); conn.close()
    return jsonify({'status':'ok'})

@app.route('/api/reject/<int:id>', methods=['POST'])
@hr_required
def reject(id):
    conn = get_db()
    conn.execute("UPDATE submissions SET status_approval='Rejected' WHERE id=?", (id,))
    conn.commit(); conn.close()
    return jsonify({'status':'ok'})

@app.route('/api/hr-submit', methods=['POST'])
@hr_required
def hr_submit():
    """HR can also submit directly"""
    try:
        data = request.json
        calc = calculate(data)
        if not calc: return jsonify({'status':'error','message':'Invalid data'}), 400
        data.update(calc)
        
        conn = get_db()
        conn.execute('''INSERT OR IGNORE INTO submissions 
            (npk, kpm_id, nama_lengkap, loan_amount, down_payment, total_ar,
             tanggal_mulai, tenure_months, loan_type, interest_rate, cabang,
             principal, total_interest, monthly_installment, outstanding_balance,
             submitted_by, document_source, status_approval)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (data.get('npk'), data.get('kpm_id'), data.get('nama_lengkap'),
             data.get('loan_amount'), data.get('down_payment'), data.get('total_ar'),
             data.get('tanggal_mulai'), data.get('tenure_months'), data.get('loan_type'),
             data.get('interest_rate'), data.get('cabang'),
             data.get('principal'), data.get('total_interest'),
             data.get('monthly_installment'), data.get('outstanding_balance'),
             session.get('name','HR'), data.get('document_source','HR Manual'),
             'Approved'))
        conn.commit(); conn.close()
        return jsonify({'status':'success','calculations':calc})
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}), 500

# 🔵 CABANG ROUTES
@app.route('/cabang')
@cabang_required
def cabang_upload():
    return render_template('cabang_upload.html')

@app.route('/cabang/scan', methods=['GET','POST'])
@cabang_required
def cabang_scan():
    parsed, confidence, raw_text, calculations = None, None, None, None
    
    if request.method == 'POST':
        file = request.files.get('document')
        if file and file.filename:
            path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(path)
            raw_text = ocr_scan_image(path)
            if raw_text:
                parsed, confidence = parse_document(raw_text)
                if parsed:
                    calculations = calculate(parsed)
                    if calculations:
                        flash(f'✅ Found {len(parsed)} fields!', 'success')
    
    return render_template('cabang_scan.html', parsed=parsed, confidence=confidence,
                         raw_text=raw_text, calculations=calculations)

@app.route('/cabang/form')
@cabang_required
def cabang_form():
    return render_template('cabang_form.html')

@app.route('/api/submit', methods=['POST'])
@cabang_required
def submit():
    try:
        data = request.json
        calc = calculate(data)
        if not calc: return jsonify({'status':'error','message':'Invalid data'}), 400
        data.update(calc)
        
        conn = get_db()
        conn.execute('''INSERT OR IGNORE INTO submissions 
            (npk, kpm_id, nama_lengkap, loan_amount, down_payment, total_ar,
             tanggal_mulai, tenure_months, loan_type, interest_rate, cabang,
             principal, total_interest, monthly_installment, outstanding_balance,
             submitted_by, document_source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (data.get('npk'), data.get('kpm_id'), data.get('nama_lengkap'),
             data.get('loan_amount'), data.get('down_payment'), data.get('total_ar'),
             data.get('tanggal_mulai'), data.get('tenure_months'), data.get('loan_type'),
             data.get('interest_rate'), data.get('cabang', session.get('name','')),
             data.get('principal'), data.get('total_interest'),
             data.get('monthly_installment'), data.get('outstanding_balance'),
             session.get('name','Cabang'), data.get('document_source','Cabang')))
        conn.commit(); conn.close()
        return jsonify({'status':'success','calculations':calc})
    except Exception as e:
        return jsonify({'status':'error','message':str(e)}), 500

@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for('hr_dashboard') if session.get('role')=='hr' else url_for('cabang_upload'))
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
