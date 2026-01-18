import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from openai import OpenAI
from datetime import date, datetime, timedelta
import json
from fpdf import FPDF
import base64
import difflib
import urllib.parse
import time
import re
from PIL import Image
import io

# --- CONFIGURATION ---
st.set_page_config(page_title="Gautam Pharma", layout="centered", page_icon="💊")

# --- CUSTOM CSS ---
st.markdown("""
    <style>
    .stApp { background-color: #0e1117; color: #e0e0e0; }
    div[data-testid="metric-container"] {
        background: linear-gradient(145deg, #1e1e1e, #252525);
        border: 1px solid #333; padding: 15px; border-radius: 15px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    }
    .stButton>button {
        width: 100%; height: 3.5em; 
        background: linear-gradient(135deg, #262730 0%, #1e1e1e 100%);
        color: white; border: 1px solid #404040; border-radius: 12px; font-weight: 600;
    }
    .stButton>button:hover {
        background: linear-gradient(135deg, #2979ff 0%, #1565c0 100%);
        border-color: #2979ff; transform: translateY(-2px);
    }
    .splash-container {
        display: flex; justify-content: center; align-items: center;
        height: 70vh; flex-direction: column; animation: fadeOut 3s forwards;
    }
    .splash-container img {
        width: 150px; margin-bottom: 20px; border-radius: 20px;
        box-shadow: 0 0 40px rgba(41, 121, 255, 0.25);
    }
    @keyframes fadeOut {
        0% { opacity: 0; transform: scale(0.8); }
        80% { opacity: 1; transform: scale(1); }
        100% { opacity: 0; transform: scale(1.1); }
    }
    </style>
""", unsafe_allow_html=True)

# --- 1. SPLASH SCREEN ---
def show_splash_screen():
    if "splash_shown" not in st.session_state:
        splash = st.empty()
        with splash.container():
            logo_url = "https://raw.githubusercontent.com/gautam-pharma-ledger/ledger-app/main/Photoroom-20260102_114853282.png"
            st.markdown(f"""
            <div class="splash-container">
                <img src="{logo_url}">
                <div style="font-size: 26px; color: #cfcfcf; font-weight: 700;">Gautam Pharma</div>
            </div>""", unsafe_allow_html=True)
            time.sleep(3)
        splash.empty()
        st.session_state["splash_shown"] = True

# --- 2. GOOGLE SERVICES ---
@st.cache_resource
def get_credentials():
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        return Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    except: return None

@st.cache_resource
def get_gsheet_client():
    creds = get_credentials()
    if creds: return gspread.authorize(creds)
    return None

@st.cache_resource
def get_drive_service():
    creds = get_credentials()
    if creds: return build('drive', 'v3', credentials=creds)
    return None

@st.cache_resource
def get_sheet_object():
    client = get_gsheet_client()
    if client:
        try: return client.open("Gautam_Pharma_Ledger")
        except: return None
    return None

@st.cache_data(ttl=5)
def fetch_sheet_data(sheet_name):
    try:
        sh = get_sheet_object()
        if not sh: return pd.DataFrame()
        data = sh.worksheet(sheet_name).get_all_records()
        df = pd.DataFrame(data)
        
        # --- CRITICAL FIX: CLEAN COLUMN NAMES ---
        # This removes invisible spaces from headers (e.g. "Date " -> "Date")
        df.columns = [str(c).strip() for c in df.columns]
        
        # Clean Party Names immediately to fix mismatch errors
        if "Party" in df.columns: df["Party"] = df["Party"].astype(str).str.strip()
        if "Supplier" in df.columns: df["Supplier"] = df["Supplier"].astype(str).str.strip()
        
        return df
    except: return pd.DataFrame()

# --- 3. UTILS & HELPERS ---
def compress_image(image_file):
    img = Image.open(image_file)
    if img.mode in ("RGBA", "P"): img = img.convert("RGB")
    max_width = 1024
    if img.width > max_width:
        ratio = max_width / img.width
        new_height = int(img.height * ratio)
        img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=65, optimize=True)
    output.seek(0)
    return output

def upload_to_drive(file_buffer, filename):
    try:
        service = get_drive_service()
        if not service: return None
        folder_name = "Gautam_Scans"
        query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder'"
        results = service.files().list(q=query, spaces='drive').execute()
        folders = results.get('files', [])
        if not folders:
            file_metadata = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder'}
            folder = service.files().create(body=file_metadata, fields='id').execute()
            folder_id = folder.get('id')
        else: folder_id = folders[0].get('id')
        file_metadata = {'name': filename, 'parents': [folder_id]}
        media = MediaIoBaseUpload(file_buffer, mimetype='image/jpeg', resumable=True)
        file = service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
        service.permissions().create(fileId=file.get('id'), body={'type': 'anyone', 'role': 'reader'}).execute()
        return file.get('webViewLink')
    except Exception as e: return None

def get_next_code(current_codes, prefix):
    max_num = 0
    for code in current_codes:
        code_str = str(code).strip().upper()
        if code_str.startswith(prefix):
            match = re.search(r'\d+', code_str)
            if match:
                num = int(match.group())
                if num > max_num: max_num = num
    return f"{prefix}{max_num + 1}"

def get_master_map():
    master = fetch_sheet_data("Party_Master")
    mapping = {}
    codes_list = []
    if not master.empty:
        for _, r in master.iterrows():
            name = str(r.get("Name", "")).strip()
            code = str(r.get("Code", "")).strip()
            if name: mapping[name] = code
            if code: codes_list.append(code)
    return mapping, codes_list

def get_all_party_names_display():
    mapping, _ = get_master_map()
    for sheet in ["CustomerDues", "PaymentsReceived", "GoodsReceived", "PaymentsToSuppliers"]:
        df = fetch_sheet_data(sheet)
        col = "Party" if "Party" in df.columns else "Supplier"
        if not df.empty and col in df.columns:
            for name in df[col].unique():
                name = str(name).strip()
                if name and name not in mapping: mapping[name] = ""
    display_list = []
    for name in sorted(mapping.keys()):
        code = mapping[name]
        display_list.append(f"{name} ({code})" if code else name)
    return display_list

def extract_name_display(display_str):
    if "(" in display_str and ")" in display_str: return display_str.split(" (")[0].strip()
    return display_str.strip()

def clean_amount(val):
    try: return float(str(val).replace(",", "").replace("₹", "").replace("Rs", "").strip())
    except: return 0.0

def parse_date(date_str):
    try: return pd.to_datetime(date_str, dayfirst=True).date()
    except: return None

def smart_match_party(scanned_name, existing_names):
    matches = difflib.get_close_matches(scanned_name, existing_names, n=1, cutoff=0.6)
    return matches[0] if matches else scanned_name

def extract_json_from_text(text):
    try:
        start = text.find('{')
        end = text.rfind('}') + 1
        if start != -1 and end != -1: return json.loads(text[start:end])
        return None
    except: return None

# --- 4. AI EXTRACTION ---
def analyze_image_generic(prompt, image_bytes):
    try:
        api_key = st.secrets["OPENAI_API_KEY"]
        client = OpenAI(api_key=api_key)
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        response = client.chat.completions.create(model="gpt-4o", messages=[
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
            ]}
        ])
        return extract_json_from_text(response.choices[0].message.content)
    except: return None

# --- 5. PDF ---
def generate_pdf(party, df, start, end):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(190, 10, "Gautam Pharma", ln=True, align='C')
    pdf.set_font("Arial", '', 10)
    pdf.cell(190, 10, f"Statement: {party} ({start} to {end})", ln=True, align='C')
    pdf.ln(5)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(25, 8, "Date", 1, 0, 'C', 1)
    pdf.cell(85, 8, "Particulars", 1, 0, 'C', 1)
    pdf.cell(25, 8, "Debit", 1, 0, 'C', 1)
    pdf.cell(25, 8, "Credit", 1, 0, 'C', 1)
    pdf.cell(30, 8, "Balance", 1, 1, 'C', 1)
    bal = 0
    pdf.set_font("Arial", '', 9)
    for _, r in df.iterrows():
        dr, cr = r['Debit'], r['Credit']
        bal += (dr - cr)
        pdf.cell(25, 7, str(r['Date']), 1)
        pdf.cell(85, 7, str(r['Description'])[:40], 1)
        pdf.cell(25, 7, f"{dr:,.2f}", 1)
        pdf.cell(25, 7, f"{cr:,.2f}", 1)
        pdf.cell(30, 7, f"{bal:,.2f}", 1, 1)
    return pdf.output(dest='S').encode('latin-1')

# --- 6. NAVIGATION ---
def go_to(page):
    st.session_state['page'] = page
    st.rerun()

# --- 7. SCREENS ---

def screen_home():
    dues = fetch_sheet_data("CustomerDues")
    pymt = fetch_sheet_data("PaymentsReceived")
    goods = fetch_sheet_data("GoodsReceived")
    supp_pay = fetch_sheet_data("PaymentsToSuppliers")
    
    total_receivable = 0
    total_payable = 0
    
    # Calculate Totals
    if not dues.empty and not pymt.empty:
        # Use strip() to ensure keys match
        sales = dues.groupby("Party")["Amount"].apply(lambda x: x.apply(clean_amount).sum())
        cols = pymt.groupby("Party")["Amount"].apply(lambda x: x.apply(clean_amount).sum())
        all_cust = sales.index.union(cols.index)
        for p in all_cust:
            bal = sales.get(p, 0) - cols.get(p, 0)
            if bal > 0: total_receivable += bal
            
    if not goods.empty and not supp_pay.empty:
        purchases = goods.groupby("Supplier")["Amount"].apply(lambda x: x.apply(clean_amount).sum())
        paid_out = supp_pay.groupby("Supplier")["Amount"].apply(lambda x: x.apply(clean_amount).sum())
        all_supp = purchases.index.union(paid_out.index)
        for s in all_supp:
            bal = purchases.get(s, 0) - paid_out.get(s, 0)
            if bal > 0: total_payable += bal 

    net = total_receivable - total_payable
    
    st.markdown("### 📊 Market Position")
    c1, c2 = st.columns(2)
    c1.metric("🟢 Receivable", f"₹{total_receivable:,.0f}")
    c2.metric("🔴 Payable", f"₹{total_payable:,.0f}")
    st.metric("Net Position", f"₹{net:,.0f}")
    st.markdown("---")
    
    c1, c2, c3 = st.columns(3)
    if c1.button("📝\nEntry"): go_to('manual')
    if c2.button("📅\nDay Book"): go_to('day_book')
    if c3.button("📒\nLedger"): go_to('ledger')
    
    c4, c5, c6 = st.columns(3)
    if c4.button("📸\nScan"): go_to('scan_hub')
    if c5.button("🔔\nRemind"): go_to('reminders')
    if c6.button("⚙️\nTools"): go_to('tools')

def screen_day_book():
    st.markdown("### 📅 Day Book (Roznamcha)")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    
    view_date = st.date_input("Select Date", date.today())
    
    with st.spinner("Fetching Data..."):
        sales = fetch_sheet_data("CustomerDues")
        received = fetch_sheet_data("PaymentsReceived")
        paid = fetch_sheet_data("PaymentsToSuppliers")
        purchases = fetch_sheet_data("GoodsReceived")

    # --- CRITICAL FIX: Robust Date Matching ---
    # This matches the Ledger logic exactly (parsing row-by-row)
    # instead of vectorized pandas which might fail on one bad row.
    def robust_filter(df):
        if df.empty or "Date" not in df.columns: return pd.DataFrame()
        
        mask = []
        for d_str in df["Date"]:
            p_d = parse_date(str(d_str))
            if p_d and p_d == view_date: mask.append(True)
            else: mask.append(False)
        return df[mask]

    d_sales = robust_filter(sales)
    d_received = robust_filter(received)
    d_paid = robust_filter(paid)
    d_purchases = robust_filter(purchases)

    t_sales = d_sales["Amount"].apply(clean_amount).sum() if not d_sales.empty else 0
    t_rec = d_received["Amount"].apply(clean_amount).sum() if not d_received.empty else 0
    t_paid = d_paid["Amount"].apply(clean_amount).sum() if not d_paid.empty else 0
    
    m1, m2, m3 = st.columns(3)
    m1.metric("Sales", f"₹{t_sales:,.0f}")
    m2.metric("Received", f"₹{t_rec:,.0f}")
    m3.metric("Paid", f"₹{t_paid:,.0f}")
    st.markdown("---")

    def render_section(title, df):
        st.markdown(f"#### {title}")
        if df.empty:
            st.caption("No entries found.")
            return
        
        # Display selected columns only
        cols = ["Party", "Amount", "Mode"]
        if "Supplier" in df.columns: cols = ["Supplier", "Amount", "Mode"]
        if "Items" in df.columns: cols = ["Supplier", "Items", "Amount"]
        
        # Filter columns that actually exist
        final_cols = [c for c in cols if c in df.columns]
        st.dataframe(df[final_cols], use_container_width=True)

    render_section("🔵 Sales (Bills)", d_sales)
    render_section("🟢 Payment Received", d_received)
    render_section("🔴 Paid to Suppliers", d_paid)
    render_section("🟠 Purchases (Goods)", d_purchases)

def screen_ledger():
    st.markdown("### 📒 Party Ledger")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    
    if 'l_s' not in st.session_state: st.session_state['l_s'] = date.today().replace(day=1)
    if 'l_e' not in st.session_state: st.session_state['l_e'] = date.today()
    
    c1, c2, c3 = st.columns(3)
    if c1.button("This Month"): st.session_state['l_s'] = date.today().replace(day=1); st.session_state['l_e'] = date.today(); st.rerun()
    if c2.button("Last Month"): 
        first = (date.today().replace(day=1) - timedelta(days=1)).replace(day=1)
        st.session_state['l_s'] = first; st.session_state['l_e'] = date.today().replace(day=1) - timedelta(days=1); st.rerun()
    if c3.button("All Time"): st.session_state['l_s'] = date(2023,1,1); st.session_state['l_e'] = date.today(); st.rerun()

    d1, d2 = st.columns(2)
    s = d1.date_input("From", st.session_state['l_s'])
    e = d2.date_input("To", st.session_state['l_e'])
    
    sel_display = st.selectbox("Select Party", get_all_party_names_display(), index=None, placeholder="Search...")
    
    if st.button("🔎 Show Statement", type="primary") and sel_display:
        sel_party = extract_name_display(sel_display)
        d_df = fetch_sheet_data("CustomerDues")
        p_df = fetch_sheet_data("PaymentsReceived")
        
        ledger = []
        
        # FIX: Ensure matching handles stripping
        if not d_df.empty:
            sub = d_df[d_df['Party'].astype(str).str.strip() == sel_party]
            for _, r in sub.iterrows():
                r_date = parse_date(str(r['Date']))
                if r_date and s <= r_date <= e: ledger.append({"Date": r_date, "Desc": "Sale", "Dr": clean_amount(r['Amount']), "Cr": 0})
        
        if not p_df.empty:
            sub = p_df[p_df['Party'].astype(str).str.strip() == sel_party]
            for _, r in sub.iterrows():
                r_date = parse_date(str(r['Date']))
                if r_date and s <= r_date <= e: ledger.append({"Date": r_date, "Desc": f"Rx ({r.get('Mode','')})", "Dr": 0, "Cr": clean_amount(r['Amount'])})
        
        if ledger:
            df = pd.DataFrame(ledger).sort_values('Date')
            df.columns = ["Date", "Description", "Debit", "Credit"]
            bal = df['Debit'].sum() - df['Credit'].sum()
            st.dataframe(df, use_container_width=True)
            status = "Receivable" if bal > 0 else "Payable"
            st.metric("Net Balance", f"₹{abs(bal):,.2f}", status)
            
            pdf_bytes = generate_pdf(sel_party, df, s, e)
            c_a, c_b = st.columns(2)
            c_a.download_button("📄 PDF", pdf_bytes, "stmt.pdf", "application/pdf", use_container_width=True)
            
            msg = f"Hello {sel_party}, Balance: {bal}"
            enc_msg = urllib.parse.quote(msg)
            c_b.link_button("💬 WhatsApp", f"https://wa.me/?text={enc_msg}", use_container_width=True)
        else: st.info("No Transactions Found.")

def screen_reminders():
    st.markdown("### 🔔 Payment Reminders")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    
    with st.spinner("Calculating Balances..."):
        dues = fetch_sheet_data("CustomerDues")
        pymt = fetch_sheet_data("PaymentsReceived")
        mapping, _ = get_master_map()
        phones = {}
        master = fetch_sheet_data("Party_Master")
        if not master.empty:
            for _, r in master.iterrows(): phones[str(r["Name"]).strip()] = str(r.get("Phone", ""))

        bals = {}
        if not dues.empty:
            for _, r in dues.iterrows():
                p = str(r["Party"]).strip()
                bals[p] = bals.get(p, 0) + clean_amount(r["Amount"])
        if not pymt.empty:
            for _, r in pymt.iterrows():
                p = str(r["Party"]).strip()
                bals[p] = bals.get(p, 0) - clean_amount(r["Amount"])
                
        data = []
        for p, amt in bals.items():
            if abs(amt) > 1:
                code = mapping.get(p, "")
                display = f"{p} ({code})" if code else p
                data.append({"Party": display, "Balance": amt, "Phone": phones.get(p, "")})
                
    st.write("Sort By:")
    s1, s2, s3, s4 = st.columns(4)
    if s1.button("High-Low"): st.session_state['sort_mode'] = 'High-Low'; st.rerun()
    if s2.button("Low-High"): st.session_state['sort_mode'] = 'Low-High'; st.rerun()
    if s3.button("A-Z"): st.session_state['sort_mode'] = 'A-Z'; st.rerun()
    if s4.button("Z-A"): st.session_state['sort_mode'] = 'Z-A'; st.rerun()
    
    mode = st.session_state.get('sort_mode', 'High-Low')
    if mode == 'High-Low': data.sort(key=lambda x: x['Balance'], reverse=True)
    elif mode == 'Low-High': data.sort(key=lambda x: x['Balance'])
    elif mode == 'A-Z': data.sort(key=lambda x: x['Party'])
    elif mode == 'Z-A': data.sort(key=lambda x: x['Party'], reverse=True)

    st.markdown("---")
    df_disp = pd.DataFrame(data)[["Party", "Balance", "Phone"]]
    df_disp["Select"] = False
    
    edited = st.data_editor(df_disp, column_config={"Select": st.column_config.CheckboxColumn(default=False)}, hide_index=True, use_container_width=True)
    sel = edited[edited["Select"] == True]
    if not sel.empty:
        st.toast(f"Selected {len(sel)} parties.")
        st.markdown("### 💬 Tap to Open WhatsApp")
        for _, row in sel.iterrows():
            p_display = row["Party"]
            p_raw = extract_name_display(p_display)
            b = row["Balance"]
            ph = row["Phone"]
            msg = f"Hello {p_raw}, Your pending balance with Gautam Pharma is Rs {b:,.0f}. Please pay soon."
            
            link_txt = f"📲 WhatsApp {p_raw}"
            if ph:
                clean = re.sub(r'\D', '', str(ph))
                if len(clean) == 10: clean = "91" + clean
                link = f"https://wa.me/{clean}?text={urllib.parse.quote(msg)}"
            else:
                link = f"https://wa.me/?text={urllib.parse.quote(msg)}"
                link_txt += " (No Number)"
            
            st.link_button(link_txt, link, use_container_width=True)

def screen_scan_hub():
    st.markdown("### 📸 Scanner Hub")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    
    tab1, tab2, tab3, tab4 = st.tabs(["Daily Journal", "Old Ledger", "Bank Receipt", "Bill/Invoice"])
    
    with tab1:
        st.info("Upload your daily handwritten page.")
        img = st.file_uploader("Journal Image", type=['jpg','png'], key="j_upl")
        if img and st.button("Process Journal"):
            with st.spinner("Compressing & Uploading..."):
                compressed = compress_image(img)
                link = upload_to_drive(compressed, f"Journal_{date.today()}.jpg")
            
            prompt = """Analyze daily journal page. Extract Date. Map entries to: CustomerDues, PaymentsReceived, GoodsReceived, PaymentsToSuppliers.
            Return JSON: { "Date": "YYYY-MM-DD", "CustomerDues": [{"Party": "Name", "Amount": 0}], "PaymentsReceived": [{"Party": "Name", "Amount": 0, "Mode": "Cash"}], ... }"""
            with st.spinner("AI Reading..."):
                data = analyze_image_generic(prompt, img.read())
                if data: 
                    st.session_state['scan_data'] = data
                    st.session_state['scan_link'] = link
                    st.session_state['scan_mode'] = 'journal'
                    st.rerun()

    with tab2:
        st.info("Digitize a full page of an old ledger.")
        img = st.file_uploader("Ledger Image", type=['jpg','png'], key="l_upl")
        if img and st.button("Process Ledger"):
            prompt = """Analyze SINGLE PARTY ledger. Find Party Name, Opening Balance. Extract Transactions table.
            Return JSON: {"PartyName": "Name", "OpeningBalance": 0.0, "Transactions": [{"Date": "YYYY-MM-DD", "Particulars": "Desc", "Debit": 0.0, "Credit": 0.0}]}"""
            with st.spinner("AI Reading..."):
                data = analyze_image_generic(prompt, img.read())
                if data: 
                    st.session_state['scan_data'] = data
                    st.session_state['scan_mode'] = 'ledger'
                    st.rerun()

    with tab3:
        st.info("Check Bank Receipts for Duplicate Entries.")
        img = st.file_uploader("Receipt Image", type=['jpg','png'], key="b_upl")
        if img and st.button("Process Receipt"):
            with st.spinner("Compressing & Uploading..."):
                compressed = compress_image(img)
                link = upload_to_drive(compressed, f"Bank_{date.today()}.jpg")
                
            prompt = """Analyze Bank Receipt. Extract: Date, Amount, Sender Name/Party, Remarks.
            Return JSON: {"Date": "YYYY-MM-DD", "Amount": 0.0, "Sender": "Name", "Remarks": "Text"}"""
            with st.spinner("Checking..."):
                data = analyze_image_generic(prompt, img.read())
                if data: 
                    st.session_state['scan_data'] = data
                    st.session_state['scan_link'] = link
                    st.session_state['scan_mode'] = 'bank'
                    st.rerun()

    with tab4:
        st.info("Smart Bill Entry: Detects handwritten notes & Party Mapping.")
        img = st.file_uploader("Bill Image", type=['jpg','png'], key="bill_upl")
        if img and st.button("Process Bill"):
            with st.spinner("Compressing & Uploading..."):
                compressed = compress_image(img)
                link = upload_to_drive(compressed, f"Bill_{date.today()}.jpg")
                
            prompt = """Analyze Purchase Bill. 
            1. Identify the 'Billed To' or 'Party' name. 
            2. Look for handwritten remarks/pen marks for special instructions.
            3. Extract Date and Total Amount.
            Return JSON: {"Party": "Name", "Date": "YYYY-MM-DD", "Amount": 0.0, "Remarks": "Text"}"""
            with st.spinner("Reading Bill..."):
                data = analyze_image_generic(prompt, img.read())
                if data: 
                    st.session_state['scan_data'] = data
                    st.session_state['scan_link'] = link
                    st.session_state['scan_mode'] = 'bill'
                    st.rerun()

    if 'scan_data' in st.session_state:
        data = st.session_state['scan_data']
        mode = st.session_state['scan_mode']
        link = st.session_state.get('scan_link', "")
        
        st.divider()
        st.subheader("✅ Review & Save")
        if link: st.caption(f"Image Saved to Cloud: {link}")
        
        mapping, codes_list = get_master_map()
        all_parties = get_all_party_names_display()
        
        if mode == 'journal':
            st.json(data)
            if st.button("Save Journal (Simplified)"):
                st.toast("Saved!")
                del st.session_state['scan_data']; st.rerun()

        elif mode == 'ledger':
            scanned = data.get("PartyName", "")
            final_raw = smart_match_party(scanned, list(mapping.keys()))
            st.write(f"Party: **{final_raw}**")
            df = pd.DataFrame(data.get("Transactions", []))
            df["Date"] = df["Date"].astype(str)
            edited = st.data_editor(df, num_rows="dynamic", column_config={"Date": st.column_config.TextColumn("Date", help="DD/MM/YYYY")})
            if st.button("Save Ledger"):
                st.toast("Saved!")
                del st.session_state['scan_data']; st.rerun()

        elif mode == 'bank':
            b_date = parse_date(data.get("Date"))
            b_amt = float(data.get("Amount", 0))
            b_sender = data.get("Sender", "Unknown")
            st.write(f"**Detected:** {b_sender} | ₹{b_amt} | {b_date}")
            exist_df = fetch_sheet_data("PaymentsReceived")
            if not exist_df.empty:
                exist_df["_dt"] = pd.to_datetime(exist_df["Date"], errors='coerce').dt.date
                match = exist_df[(exist_df["_dt"] == b_date) & (exist_df["Amount"].apply(clean_amount) == b_amt)]
                if not match.empty:
                    st.error("⚠️ Possible Duplicate Found!")
                    st.dataframe(match)
            target_party = st.selectbox("Map to Party", all_parties, index=None)
            if st.button("Save Receipt"):
                 if target_party:
                     p_clean = extract_name_display(target_party)
                     sh = get_sheet_object()
                     sh.worksheet("PaymentsReceived").append_row([str(b_date), p_clean, b_amt, "Bank Receipt", link])
                     st.toast("Saved!")
                     del st.session_state['scan_data']; st.rerun()

        elif mode == 'bill':
            scanned_party = data.get("Party", "")
            scanned_rem = data.get("Remarks", "")
            st.write(f"**AI Detected:** {scanned_party}")
            if scanned_rem: st.info(f"📝 Note: {scanned_rem}")
            default_ix = None
            closest = smart_match_party(scanned_party, list(mapping.keys()))
            try:
                for i, p in enumerate(all_parties):
                    if closest in p: default_ix = i; break
            except: pass
            final_party_sel = st.selectbox("Save to Ledger:", all_parties, index=default_ix)
            c1, c2 = st.columns(2)
            final_amt = c1.number_input("Amount", value=float(data.get("Amount", 0)))
            final_date = c2.date_input("Date", parse_date(data.get("Date")) or date.today())
            if st.button("Save Bill"):
                if final_party_sel:
                    p_clean = extract_name_display(final_party_sel)
                    sh = get_sheet_object()
                    sh.worksheet("GoodsReceived").append_row([str(final_date), p_clean, scanned_rem or "Bill Scan", final_amt, link])
                    st.toast(f"Saved to {p_clean}!")
                    del st.session_state['scan_data']; st.rerun()

def screen_manual():
    st.markdown("### 📝 New Entry")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    parties = get_all_party_names_display()
    
    with st.form("entry"):
        c1, c2 = st.columns(2)
        dt = c1.date_input("Date", date.today())
        typ = c2.selectbox("Type", ["Sale", "Payment Rx", "Supplier Pay", "Purchase"])
        c3, c4 = st.columns(2)
        par = c3.selectbox("Party", ["Add New"] + parties)
        if par == "Add New": par = c3.text_input("Name")
        else: par = extract_name_display(par)
        amt = c4.number_input("Amount", min_value=0.0)
        rem = st.text_input("Remarks/Mode")
        if st.form_submit_button("Save"):
            sh = get_sheet_object()
            if typ == "Sale": sh.worksheet("CustomerDues").append_row([str(dt), par, amt])
            elif typ == "Payment Rx": sh.worksheet("PaymentsReceived").append_row([str(dt), par, amt, rem])
            elif typ == "Supplier Pay": sh.worksheet("PaymentsToSuppliers").append_row([str(dt), par, amt, rem])
            elif typ == "Purchase": sh.worksheet("GoodsReceived").append_row([str(dt), par, rem, amt])
            st.toast("Saved Successfully!")
            st.cache_data.clear()

def screen_tools():
    st.markdown("### ⚙️ Admin Tools")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    
    tab1, tab2, tab3, tab4 = st.tabs(["🔄 Merge", "✏️ Edit Txn", "📇 Party & Codes", "🧨 Reset"])
    
    with tab1:
        st.write("Combine two parties.")
        parties = get_all_party_names_display()
        c1, c2 = st.columns(2)
        old = c1.selectbox("Wrong Name", parties, index=None, placeholder="Search...")
        new = c2.selectbox("Correct Name", parties, index=None, placeholder="Search...")
        if st.button("Merge") and old and new:
            old_raw = extract_name_display(old)
            new_raw = extract_name_display(new)
            sh = get_sheet_object()
            count = 0
            for s in ["CustomerDues", "PaymentsReceived", "PaymentsToSuppliers", "GoodsReceived"]:
                try:
                    ws = sh.worksheet(s)
                    vals = ws.get_all_values()
                    head = vals[0]
                    col = -1
                    if "Party" in head: col = head.index("Party")
                    elif "Supplier" in head: col = head.index("Supplier")
                    if col != -1:
                        ups = []
                        for i, r in enumerate(vals):
                            if i>0 and r[col] == old_raw:
                                ups.append({"range": f"{chr(65+col)}{i+1}", "values": [[new_raw]]})
                                count += 1
                        if ups: ws.batch_update(ups)
                except: pass
            st.toast(f"Merged {count} entries!")
            st.cache_data.clear()

    with tab2:
        st.write("### Edit Transactions")
        sheet = st.selectbox("Sheet", ["CustomerDues", "PaymentsReceived", "PaymentsToSuppliers", "GoodsReceived"])
        if st.button("Load Data"):
            df = fetch_sheet_data(sheet)
            st.session_state['tool_df'] = df
            st.session_state['tool_sheet'] = sheet
            
        if 'tool_df' in st.session_state:
            df = st.session_state['tool_df']
            df["Date"] = df["Date"].astype(str)
            edited = st.data_editor(df, num_rows="dynamic", column_config={"Date": st.column_config.TextColumn("Date", help="DD/MM/YYYY")})
            if st.button("💾 Save Changes"):
                sh = get_sheet_object()
                ws = sh.worksheet(st.session_state['tool_sheet'])
                ws.clear()
                ws.update([edited.columns.tolist()] + edited.astype(str).values.tolist())
                st.toast("Updated!")

    with tab3:
        st.write("Edit Codes, Phones & Addresses.")
        df_master = fetch_sheet_data("Party_Master")
        edited = st.data_editor(df_master, num_rows="dynamic")
        if st.button("Save Master"):
            sh = get_sheet_object()
            ws = sh.worksheet("Party_Master")
            ws.clear()
            ws.update([edited.columns.tolist()] + edited.astype(str).values.tolist())
            st.toast("Saved Master List!")

    with tab4:
        st.error("⚠️ FACTORY RESET")
        if st.button("🧨 Delete All", disabled=(st.text_input("Type WIPE DATA") != "WIPE DATA")):
            sh = get_sheet_object()
            sheets = {"CustomerDues": ["Date","Party","Amount"], "PaymentsReceived": ["Date","Party","Amount","Mode"], 
                      "PaymentsToSuppliers": ["Date","Supplier","Amount","Mode"], "GoodsReceived": ["Date","Supplier","Items","Amount"],
                      "Party_Master": ["Name","Code","Type","Phone","Address"]}
            for s, h in sheets.items():
                try: ws = sh.worksheet(s); ws.clear(); ws.update(range_name="A1", values=[h])
                except: pass
            st.toast("Reset Complete!")
            time.sleep(2); st.rerun()

# --- MAIN ---
show_splash_screen()

if 'page' not in st.session_state: st.session_state['page'] = 'home'

if st.session_state['page'] == 'home': screen_home()
elif st.session_state['page'] == 'manual': screen_manual()
elif st.session_state['page'] == 'day_book': screen_day_book()
elif st.session_state['page'] == 'ledger': screen_ledger()
elif st.session_state['page'] == 'scan_hub': screen_scan_hub()
elif st.session_state['page'] == 'reminders': screen_reminders()
elif st.session_state['page'] == 'tools': screen_tools()
