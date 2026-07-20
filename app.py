from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from werkzeug.utils import secure_filename
import pandas as pd
import plotly.express as px
import plotly.io as pio
import os
from datetime import datetime
from io import BytesIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sheetgraph-dev-key-2024'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv'}
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

data_store = {}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def home():
    stats = {
        'total_files': len(data_store),
        'total_sheets': sum(len(v) for v in data_store.values()),
        'total_records': sum(sum(len(df) for df in v.values()) for v in data_store.values())
    }
    return render_template('index.html', stats=stats)

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected', 'error')
            return redirect(url_for('upload'))
        
        file = request.files['file']
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('upload'))
        
        if not allowed_file(file.filename):
            flash('Invalid file type. Use .xlsx, .xls, or .csv', 'error')
            return redirect(url_for('upload'))
        
        try:
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            excel_data = {}
            if filename.endswith('.csv'):
                df = pd.read_csv(filepath)
                excel_data['Sheet1'] = df
            else:
                xl_file = pd.ExcelFile(filepath)
                for sheet_name in xl_file.sheet_names:
                    excel_data[sheet_name] = pd.read_excel(filepath, sheet_name=sheet_name)
            
            data_store[filename] = excel_data
            
            preview = {}
            for sheet_name, df in excel_data.items():
                preview[sheet_name] = {
                    'columns': df.columns.tolist(),
                    'rows': len(df),
                    'numeric_cols': len(df.select_dtypes(include=['number']).columns),
                    'head': df.head(5).to_dict('records')
                }
            
            flash(f'Successfully uploaded {filename}!', 'success')
            return render_template('upload.html', filename=filename, preview=preview, files=list(data_store.keys()))
        
        except Exception as e:
            flash(f'Error: {str(e)}', 'error')
            return redirect(url_for('upload'))
    
    return render_template('upload.html', files=list(data_store.keys()))

@app.route('/view/<filename>')
def view_data(filename):
    if filename not in data_store:
        flash('File not found', 'error')
        return redirect(url_for('upload'))
    
    excel_data = data_store[filename]
    sheet_name = request.args.get('sheet')
    if not sheet_name or sheet_name not in excel_data:
        sheet_name = list(excel_data.keys())[0]
    
    df = excel_data[sheet_name]
    page = int(request.args.get('page', 1))
    per_page = 50
    total_rows = len(df)
    total_pages = max(1, (total_rows + per_page - 1) // per_page)
    
    start_idx = (page - 1) * per_page
    end_idx = min(start_idx + per_page, total_rows)
    page_data = df.iloc[start_idx:end_idx].to_dict('records')
    
    return render_template('view.html',
                         filename=filename,
                         sheets=list(excel_data.keys()),
                         current_sheet=sheet_name,
                         columns=df.columns.tolist(),
                         data=page_data,
                         page=page,
                         total_pages=total_pages,
                         total_rows=total_rows)

@app.route('/graphs/<filename>')
def show_graphs(filename):
    if filename not in data_store:
        flash('File not found', 'error')
        return redirect(url_for('upload'))
    
    excel_data = data_store[filename]
    sheet_name = request.args.get('sheet')
    if not sheet_name or sheet_name not in excel_data:
        sheet_name = list(excel_data.keys())[0]
    
    df = excel_data[sheet_name]
    graphs_html = {}
    
    numeric_cols = df.select_dtypes(include=['number']).columns
    categorical_cols = df.select_dtypes(include=['object']).columns
    
    if len(categorical_cols) > 0 and len(numeric_cols) > 0:
        top_cats = df[categorical_cols[0]].value_counts().head(10).index
        df_filtered = df[df[categorical_cols[0]].isin(top_cats)]
        agg_data = df_filtered.groupby(categorical_cols[0])[numeric_cols[0]].sum().reset_index()
        fig = px.bar(agg_data, x=numeric_cols[0], y=categorical_cols[0], 
                    title=f'{numeric_cols[0]} by {categorical_cols[0]}')
        fig.update_layout(template='plotly_white', height=400)
        graphs_html['bar_chart'] = pio.to_html(fig, full_html=False)
    
    if len(numeric_cols) > 0:
        fig = px.histogram(df, x=numeric_cols[0], title=f'Distribution of {numeric_cols[0]}')
        fig.update_layout(template='plotly_white', height=400)
        graphs_html['histogram'] = pio.to_html(fig, full_html=False)
    
    if len(numeric_cols) >= 2:
        fig = px.scatter(df, x=numeric_cols[0], y=numeric_cols[1], 
                        title=f'{numeric_cols[1]} vs {numeric_cols[0]}', trendline='ols')
        fig.update_layout(template='plotly_white', height=400)
        graphs_html['scatter_plot'] = pio.to_html(fig, full_html=False)
    
    stats = {
        'total_rows': len(df),
        'total_columns': len(df.columns),
        'numeric_columns': len(numeric_cols),
        'categorical_columns': len(categorical_cols),
        'missing_values': int(df.isnull().sum().sum())
    }
    
    return render_template('graphs.html',
                         filename=filename,
                         sheets=list(excel_data.keys()),
                         current_sheet=sheet_name,
                         graphs=graphs_html,
                         stats=stats)

@app.route('/generate-report/<filename>')
def generate_report(filename):
    if filename not in data_store:
        flash('File not found', 'error')
        return redirect(url_for('upload'))
    
    try:
        excel_data = data_store[filename]
        
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
        
        wb = Workbook()
        if 'Sheet' in wb.sheetnames:
            wb.remove(wb['Sheet'])
        
        # Create formatted sheets
        for sheet_name, df in excel_data.items():
            ws = wb.create_sheet(str(sheet_name)[:31])
            
            # Headers
            header_fill = PatternFill(start_color='1B4332', end_color='1B4332', fill_type='solid')
            header_font = Font(name='Calibri', size=12, bold=True, color='FFFFFF')
            
            for col_idx, column in enumerate(df.columns, 1):
                cell = ws.cell(row=1, column=col_idx, value=column)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal='center')
            
            # Data
            for row_idx, (_, row_data) in enumerate(df.iterrows(), 2):
                for col_idx, value in enumerate(row_data, 1):
                    ws.cell(row=row_idx, column=col_idx, value=value)
            
            # Auto-fit columns
            for col_idx, column in enumerate(df.columns, 1):
                max_length = max(df[column].astype(str).str.len().max(), len(str(column)))
                ws.column_dimensions[get_column_letter(col_idx)].width = min(max_length + 3, 50)
            
            ws.auto_filter.ref = ws.dimensions
            ws.freeze_panes = 'A2'
        
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        
        return send_file(output,
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        as_attachment=True,
                        download_name=f'SheetGraph_Report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx')
    
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('upload'))

@app.route('/delete/<filename>', methods=['POST'])
def delete_file(filename):
    if filename in data_store:
        del data_store[filename]
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(filepath):
            os.remove(filepath)
        flash(f'{filename} deleted', 'success')
    return redirect(url_for('upload'))

@app.route('/download-template')
def download_template():
    template_data = {
        'Sales': pd.DataFrame({
            'Date': pd.date_range('2024-01-01', periods=50, freq='D'),
            'Product': ['Product A', 'Product B', 'Product C', 'Product D'] * 12 + ['Product A', 'Product B'],
            'Quantity': [10, 25, 15, 30] * 12 + [10, 25],
            'Price': [100, 200, 150, 300] * 12 + [100, 200],
            'Region': ['North', 'South', 'East', 'West'] * 12 + ['North', 'South'],
            'Salesperson': ['John', 'Jane', 'Bob', 'Alice'] * 12 + ['John', 'Jane']
        }),
        'Inventory': pd.DataFrame({
            'Item_Code': [f'ITM{i:03d}' for i in range(1, 21)],
            'Item_Name': [f'Product {i}' for i in range(1, 21)],
            'Stock_Level': [100, 150, 75, 200, 50, 125, 175, 90, 110, 80,
                          130, 160, 140, 95, 105, 180, 70, 120, 155, 85],
            'Unit_Price': [10.99, 24.99, 15.50, 30.00, 8.75, 12.50, 18.99, 22.00,
                         9.99, 11.50, 14.75, 27.50, 16.25, 13.99, 19.50, 33.00,
                         7.99, 21.50, 26.00, 17.75]
        })
    }
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        for sheet_name, df in template_data.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    
    output.seek(0)
    return send_file(output,
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    as_attachment=True,
                    download_name='SheetGraph_Template.xlsx')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
