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
import traceback

# --- SAFETY IMPORT FOR VOICE ---
try:
    from streamlit_mic_recorder import mic_recorder
    VOICE_AVAILABLE = True
except ImportError:
    VOICE_AVAILABLE = False

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
        transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
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

# --- 1. CRITICAL FUNCTIONS ---
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
            time.sleep(2.5)
        splash.empty()
        st.session_state["splash_shown"] = True

# --- 2. CONNECTORS & UTILS ---
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
        
        data = sh.worksheet(sheet_name).get_all_values()
        if not data: return pd.DataFrame()
        
        headers = data.pop(0)
        df = pd.DataFrame(data, columns=headers)
        
        # 1. CLEAN EMPTY ROWS
        df.replace("", pd.NA, inplace=True)
        df.dropna(how='all', inplace=True)
        df.fillna("", inplace=True)
        
        # 2. CLEAN HEADERS
        df.columns = [str(c).strip() for c in df.columns]
        
        # 3. FIX COLUMN NAMES
        if sheet_name in ["PaymentsToSuppliers", "GoodsReceived"]:
            if "Party" in df.columns: 
                df.rename(columns={"Party": "Supplier"}, inplace=True)
        
        # 4. CLEAN DATA
        for col in ["Party", "Supplier"]:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()
                
        return df
    except Exception:
        return pd.DataFrame()

def clean_amount(val):
    try:
        if isinstance(val, (int, float)): return float(val)
        val = str(val).replace(",", "").replace("₹", "").replace("Rs", "").strip()
        return float(val) if val else 0.0
    except: return 0.0

def parse_date(date_str):
    if not date_str: return None
    try: return pd.to_datetime(date_str, dayfirst=True).date()
    except: 
        try: return pd.to_datetime(date_str).date()
        except: return None

def extract_name_display(display_str):
    if "(" in display_str and ")" in display_str: return display_str.split(" (")[0].strip()
    return display_str.strip()

def get_all_party_names_display():
    df = fetch_sheet_data("Party_Master")
    names = []
    if not df.empty:
        for _, r in df.iterrows():
            n = str(r.get("Name", "")).strip()
            c = str(r.get("Code", "")).strip()
            if n: names.append(f"{n} ({c})" if c else n)
    return sorted(list(set(names)))

def get_master_map():
    df = fetch_sheet_data("Party_Master")
    mapping = {}
    codes = []
    if not df.empty:
        for _, r in df.iterrows():
            mapping[str(r.get("Name","")).strip()] = str(r.get("Code","")).strip()
            codes.append(str(r.get("Code","")).strip())
    return mapping, codes

# --- 3. IMAGES & AI ---
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
            meta = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder'}
            folder = service.files().create(body=meta, fields='id').execute()
            fid = folder.get('id')
        else: fid = folders[0].get('id')
        meta = {'name': filename, 'parents': [fid]}
        media = MediaIoBaseUpload(file_buffer, mimetype='image/jpeg', resumable=True)
        f = service.files().create(body=meta, media_body=media, fields='id, webViewLink').execute()
        service.permissions().create(fileId=f.get('id'), body={'type': 'anyone', 'role': 'reader'}).execute()
        return f.get('webViewLink')
    except: return None

def extract_json(text):
    try:
        s = text.find('{')
        e = text.rfind('}') + 1
        return json.loads(text[s:e]) if s != -1 else None
    except: return None

def analyze_image_generic(prompt, image_bytes):
    try:
        api_key = st.secrets["OPENAI_API_KEY"]
        client = OpenAI(api_key=api_key)
        b64 = base64.b64encode(image_bytes).decode('utf-8')
        resp = client.chat.completions.create(model="gpt-4o", messages=[
            {"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}
        ])
        return extract_json(resp.choices[0].message.content)
    except: return None

# --- 4. PDF ---
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
    # FIX: Uses 'Debit', 'Credit', 'Particulars' keys correctly
    for _, r in df.iterrows():
        dr = r.get('Debit', 0)
        cr = r.get('Credit', 0)
        bal += (dr - cr)
        pdf.cell(25, 7, str(r['Date']), 1)
        pdf.cell(85, 7, str(r.get('Particulars', ''))[:40], 1)
        pdf.cell(25, 7, f"{dr:,.2f}", 1)
        pdf.cell(25, 7, f"{cr:,.2f}", 1)
        pdf.cell(30, 7, f"{bal:,.2f}", 1, 1)
    return pdf.output(dest='S').encode('latin-1')

# --- 5. SCREENS ---

def screen_home():
    dues = fetch_sheet_data("CustomerDues")
    pymt = fetch_sheet_data("PaymentsReceived")
    goods = fetch_sheet_data("GoodsReceived")
    supp = fetch_sheet_data("PaymentsToSuppliers")
    
    tr, tp = 0, 0
    if not dues.empty and not pymt.empty:
        s_tot = dues.groupby("Party")["Amount"].apply(lambda x: x.apply(clean_amount).sum())
        r_tot = pymt.groupby("Party")["Amount"].apply(lambda x: x.apply(clean_amount).sum())
        for p in s_tot.index.union(r_tot.index):
            bal = s_tot.get(p, 0) - r_tot.get(p, 0)
            if bal > 0: tr += bal
    if not goods.empty and not supp.empty:
        p_tot = goods.groupby("Supplier")["Amount"].apply(lambda x: x.apply(clean_amount).sum())
        paid_tot = supp.groupby("Supplier")["Amount"].apply(lambda x: x.apply(clean_amount).sum())
        for s in p_tot.index.union(paid_tot.index):
            bal = p_tot.get(s, 0) - paid_tot.get(s, 0)
            if bal > 0: tp += bal
    net = tr - tp
    
    st.markdown("### 📊 Market Position")
    c1, c2 = st.columns(2)
    c1.metric("🟢 Receivable", f"₹{tr:,.0f}")
    c2.metric("🔴 Payable", f"₹{tp:,.0f}")
    st.metric("Net Position", f"₹{net:,.0f}")
    st.markdown("---")
    
    c1, c2, c3, c4 = st.columns(4)
    if c1.button("📝\nEntry"): st.session_state.page = 'manual'; st.rerun()
    if c2.button("📅\nDayBook"): st.session_state.page = 'day_book'; st.rerun()
    if c3.button("📒\nLedger"): st.session_state.page = 'ledger'; st.rerun()
    if c4.button("🎙️\nVoice"): st.session_state.page = 'voice'; st.rerun()
    
    c5, c6, c7, c8 = st.columns(4)
    if c5.button("📸\nScan"): st.session_state.page = 'scan_hub'; st.rerun()
    if c6.button("🔔\nRemind"): st.session_state.page = 'reminders'; st.rerun()
    if c7.button("⚙️\nTools"): st.session_state.page = 'tools'; st.rerun()
    if c8.button("🔄\nSync"): st.cache_data.clear(); st.rerun()

def screen_day_book():
    st.markdown("### 📅 Day Book (Roznamcha)")
    if st.button("🏠 Home", use_container_width=True): st.session_state.page = 'home'; st.rerun()
    view_date = st.date_input("Select Date", date.today())
    
    sales = fetch_sheet_data("CustomerDues")
    received = fetch_sheet_data("PaymentsReceived")
    paid = fetch_sheet_data("PaymentsToSuppliers")
    purchases = fetch_sheet_data("GoodsReceived")

    def get_day_data(df, target_date):
        if df.empty or "Date" not in df.columns: return pd.DataFrame()
        target_str = target_date.strftime("%Y-%m-%d")
        mask = []
        for d in df["Date"]:
            pd_date = parse_date(str(d))
            if pd_date and pd_date.strftime("%Y-%m-%d") == target_str: mask.append(True)
            else: mask.append(False)
        return df[mask]

    d_s = get_day_data(sales, view_date)
    d_r = get_day_data(received, view_date)
    d_p = get_day_data(paid, view_date)
    d_g = get_day_data(purchases, view_date)

    t_s = d_s["Amount"].apply(clean_amount).sum() if not d_s.empty else 0
    t_r = d_r["Amount"].apply(clean_amount).sum() if not d_r.empty else 0
    t_p = d_p["Amount"].apply(clean_amount).sum() if not d_p.empty else 0
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Sales", f"₹{t_s:,.0f}")
    c2.metric("Received", f"₹{t_r:,.0f}")
    c3.metric("Paid", f"₹{t_p:,.0f}")
    
    def show_table(title, df, cols):
        st.markdown(f"**{title}**")
        if df.empty: st.caption("No entries."); return
        final_cols = [c for c in cols if c in df.columns]
        st.dataframe(df[final_cols], use_container_width=True)

    show_table("🔵 Sales", d_s, ["Party", "Amount"])
    show_table("🟢 Received", d_r, ["Party", "Amount", "Mode"])
    show_table("🔴 Paid", d_p, ["Supplier", "Amount", "Mode"])
    show_table("🟠 Purchases", d_g, ["Supplier", "Items", "Amount"])

def screen_ledger():
    st.markdown("### 📒 Party Ledger")
    if st.button("🏠 Home", use_container_width=True): st.session_state.page = 'home'; st.rerun()
    
    idx = None
    if 'voice_party' in st.session_state:
        all_p = get_all_party_names_display()
        match = difflib.get_close_matches(st.session_state.pop('voice_party'), all_p, n=1)
        if match: idx = all_p.index(match[0])

    if 'l_s' not in st.session_state: st.session_state['l_s'] = date(2023, 1, 1)
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
    
    sel = st.selectbox("Party", get_all_party_names_display(), index=idx)
    
    if st.button("🔎 Show", type="primary") or idx is not None:
        if not sel: return
        p_name = extract_name_display(sel)
        p_clean = p_name.lower().strip()
        
        d_df = fetch_sheet_data("CustomerDues")
        p_df = fetch_sheet_data("PaymentsReceived")
        supp_pay_df = fetch_sheet_data("PaymentsToSuppliers")
        goods_df = fetch_sheet_data("GoodsReceived")
        
        ledger = []
        
        def get_matches(df, col):
            if df.empty: return pd.DataFrame()
            return df[df[col].astype(str).str.lower().str.strip() == p_clean]

        # FIX: Explicitly name columns "Debit", "Credit", "Particulars" for PDF
        # 1. Sales (Debit)
        sub = get_matches(d_df, "Party")
        for _, r in sub.iterrows():
            dt = parse_date(str(r["Date"]))
            if dt and s <= dt <= e: 
                ledger.append({"Date": dt, "Particulars": "Sale", "Debit": clean_amount(r["Amount"]), "Credit": 0})
                    
        # 2. Received (Credit)
        sub = get_matches(p_df, "Party")
        for _, r in sub.iterrows():
            dt = parse_date(str(r["Date"]))
            if dt and s <= dt <= e: 
                ledger.append({"Date": dt, "Particulars": f"Rx {r.get('Mode','')}", "Debit": 0, "Credit": clean_amount(r["Amount"])})

        # 3. Paid Supplier (Debit - reduces liability)
        sub = get_matches(supp_pay_df, "Supplier")
        for _, r in sub.iterrows():
            dt = parse_date(str(r["Date"]))
            if dt and s <= dt <= e: 
                ledger.append({"Date": dt, "Particulars": f"Paid Supplier {r.get('Mode','')}", "Debit": clean_amount(r["Amount"]), "Credit": 0})

        # 4. Purchases (Credit - increases liability)
        sub = get_matches(goods_df, "Supplier")
        for _, r in sub.iterrows():
            dt = parse_date(str(r["Date"]))
            if dt and s <= dt <= e: 
                ledger.append({"Date": dt, "Particulars": f"Purchase ({r.get('Items','')})", "Debit": 0, "Credit": clean_amount(r["Amount"])})
        
        if ledger:
            df = pd.DataFrame(ledger).sort_values("Date")
            bal = df["Debit"].sum() - df["Credit"].sum()
            
            df["Date"] = df["Date"].astype(str)
            st.dataframe(df, use_container_width=True)
            
            status = "Receivable (Lena hai)" if bal > 0 else "Payable (Dena hai)"
            st.metric(f"Net Balance ({status})", f"₹{abs(bal):,.2f}")
            
            pdf = generate_pdf(p_name, df, s, e)
            st.download_button("📄 PDF", pdf, "stmt.pdf", "application/pdf")
            
            lnk = f"https://wa.me/?text={urllib.parse.quote(f'Hello {p_name}, Bal: {bal}')}"
            st.link_button("💬 Share WhatsApp", lnk)
        else: st.info("No records found.")

def screen_scan_hub():
    st.markdown("### 📸 Scanner")
    if st.button("🏠 Home", use_container_width=True): st.session_state.page = 'home'; st.rerun()
    t1, t2 = st.tabs(["Journal/Receipt", "Bill"])
    
    with t1:
        img = st.file_uploader("Upload Image", type=['jpg','png'], key="u1")
        if img and st.button("Process"):
            with st.spinner("Processing..."):
                compressed = compress_image(img)
                link = upload_to_drive(compressed, f"Scan_{date.today()}.jpg")
            img.seek(0)
            with st.spinner("AI Reading..."):
                p = """Analyze image. Extract Date. Identify Sales (CustomerDues) and Payments (PaymentsReceived). Return JSON: {"Date": "YYYY-MM-DD", "Sales": [{"Party": "Name", "Amount": 0}], "Payments": [{"Party": "Name", "Amount": 0}]}"""
                data = analyze_image_generic(p, img.read())
                if data:
                    st.session_state.scan_data = data
                    st.session_state.scan_link = link
                    st.rerun()
    
    if 'scan_data' in st.session_state:
        d = st.session_state.scan_data
        st.write("### Review")
        dt = st.date_input("Entry Date", parse_date(d.get("Date")) or date.today())
        st.write("Sales"); df_s = pd.DataFrame(d.get("Sales", [])); ed_s = st.data_editor(df_s, num_rows="dynamic")
        st.write("Payments"); df_p = pd.DataFrame(d.get("Payments", [])); ed_p = st.data_editor(df_p, num_rows="dynamic")
        if st.button("💾 Save All"):
            sh = get_sheet_object()
            link = st.session_state.get('scan_link', "")
            s_rows = [[str(dt), r.get("Party",""), clean_amount(r.get("Amount"))] for _, r in ed_s.iterrows() if r.get("Party")]
            if s_rows: sh.worksheet("CustomerDues").append_rows(s_rows)
            p_rows = [[str(dt), r.get("Party",""), clean_amount(r.get("Amount")), "Scan", link] for _, r in ed_p.iterrows() if r.get("Party")]
            if p_rows: sh.worksheet("PaymentsReceived").append_rows(p_rows)
            st.toast("Saved!"); del st.session_state.scan_data; st.rerun()

def screen_voice_assistant():
    st.markdown("### 🎙️ Voice")
    if st.button("🏠 Home"): st.session_state.page = 'home'; st.rerun()
    if not VOICE_AVAILABLE: st.error("Voice not supported."); return
    audio = mic_recorder(start_prompt="🎤 Speak", stop_prompt="⏹️ Stop", key='mic')
    if audio:
        with st.spinner("Thinking..."):
            try:
                api_key = st.secrets["OPENAI_API_KEY"]
                client = OpenAI(api_key=api_key)
                ab = io.BytesIO(audio['bytes'])
                ab.name = "audio.wav"
                txt = client.audio.transcriptions.create(model="whisper-1", file=ab).text
                st.info(f"You said: {txt}")
                p = f"""Command: "{txt}". Parties: {', '.join(list(get_master_map()[0].keys())[:50])}. Return JSON: {{"intent": "view_ledger", "party": "Name"}}"""
                resp = client.chat.completions.create(model="gpt-4o", messages=[{"role":"user", "content":p}])
                res = extract_json(resp.choices[0].message.content)
                if res and res["intent"] == "view_ledger":
                    st.session_state.voice_party = res["party"]; st.session_state.page = 'ledger'; st.rerun()
            except Exception as e: st.error(f"Error: {e}")

def screen_manual():
    st.markdown("### 📝 Entry")
    if st.button("🏠 Home"): st.session_state.page = 'home'; st.rerun()
    with st.form("manual"):
        d = st.date_input("Date", date.today())
        t = st.selectbox("Type", ["Sale", "Received", "Purchase", "Paid"])
        p = st.selectbox("Party", get_all_party_names_display())
        a = st.number_input("Amount", min_value=0.0)
        m = st.text_input("Note/Mode")
        if st.form_submit_button("Save"):
            sh = get_sheet_object()
            pn = extract_name_display(p)
            if t == "Sale": sh.worksheet("CustomerDues").append_row([str(d), pn, a])
            elif t == "Received": sh.worksheet("PaymentsReceived").append_row([str(d), pn, a, m])
            elif t == "Paid": sh.worksheet("PaymentsToSuppliers").append_row([str(d), pn, a, m])
            elif t == "Purchase": sh.worksheet("GoodsReceived").append_row([str(d), pn, m, a])
            st.toast("Saved!"); st.cache_data.clear()

def screen_reminders():
    st.write("### 🔔 Reminders")
    if st.button("🏠 Home"): st.session_state.page = 'home'; st.rerun()
    dues = fetch_sheet_data("CustomerDues")
    rec = fetch_sheet_data("PaymentsReceived")
    bal = {}
    if not dues.empty:
        for _, r in dues.iterrows(): p = str(r["Party"]).strip(); bal[p] = bal.get(p, 0) + clean_amount(r["Amount"])
    if not rec.empty:
        for _, r in rec.iterrows(): p = str(r["Party"]).strip(); bal[p] = bal.get(p, 0) - clean_amount(r["Amount"])
    data = [{"Party": k, "Balance": v} for k,v in bal.items() if v > 1]
    df = pd.DataFrame(data).sort_values("Balance", ascending=False)
    for _, r in df.iterrows():
        lnk = f"https://wa.me/?text={urllib.parse.quote(f'Hello {r['Party']}, Balance: {r['Balance']}')}"
        st.link_button(f"{r['Party']} (₹{r['Balance']:,.0f})", lnk, use_container_width=True)

def screen_tools():
    st.write("Tools: Edit/Merge")
    if st.button("🏠 Home"): st.session_state.page = 'home'; st.rerun()
    t1, t2, t3, t4 = st.tabs(["Edit", "Merge", "Master", "Reset"])
    
    with t1:
        s = st.selectbox("Sheet", ["CustomerDues", "PaymentsReceived", "PaymentsToSuppliers", "GoodsReceived"])
        if st.button("Load"): st.session_state.t_df = fetch_sheet_data(s); st.session_state.t_s = s
        if 't_df' in st.session_state:
            ed = st.data_editor(st.session_state.t_df, num_rows="dynamic")
            if st.button("Save"):
                sh = get_sheet_object(); ws = sh.worksheet(st.session_state.t_s); ws.clear()
                ws.update([ed.columns.tolist()] + ed.astype(str).values.tolist()); st.toast("Updated!")

    with t2:
        parties = get_all_party_names_display()
        old = st.selectbox("Wrong", parties, index=None)
        new = st.selectbox("Correct", parties, index=None)
        if st.button("Merge") and old and new:
            o_r, n_r = extract_name_display(old), extract_name_display(new)
            sh = get_sheet_object()
            for s in ["CustomerDues", "PaymentsReceived", "PaymentsToSuppliers", "GoodsReceived"]:
                try:
                    ws = sh.worksheet(s); vals = ws.get_all_values(); head = vals[0]
                    col = head.index("Party") if "Party" in head else (head.index("Supplier") if "Supplier" in head else -1)
                    if col != -1:
                        ups = [{"range": f"{chr(65+col)}{i+1}", "values": [[n_r]]} for i, r in enumerate(vals) if i>0 and r[col] == o_r]
                        if ups: ws.batch_update(ups)
                except: pass
            st.toast("Merged!")

    with t3:
        df_m = fetch_sheet_data("Party_Master")
        ed_m = st.data_editor(df_m, num_rows="dynamic")
        if st.button("Save Master"):
            sh = get_sheet_object(); ws = sh.worksheet("Party_Master"); ws.clear()
            ws.update([ed_m.columns.tolist()] + ed_m.astype(str).values.tolist()); st.toast("Saved!")

    with t4:
        if st.button("🧨 Factory Reset", disabled=(st.text_input("Type WIPE") != "WIPE")):
            sh = get_sheet_object()
            sheets = {"CustomerDues": ["Date","Party","Amount"], "PaymentsReceived": ["Date","Party","Amount","Mode"], 
                      "PaymentsToSuppliers": ["Date","Supplier","Amount","Mode"], "GoodsReceived": ["Date","Supplier","Items","Amount"],
                      "Party_Master": ["Name","Code","Type","Phone","Address"]}
            for s, h in sheets.items():
                try: ws = sh.worksheet(s); ws.clear(); ws.update(range_name="A1", values=[h])
                except: pass
            st.toast("Reset!"); time.sleep(2); st.rerun()

# --- MAIN APP LOGIC ---
try:
    if 'page' not in st.session_state: st.session_state.page = 'home'
    show_splash_screen()
    if st.session_state.page == 'home': screen_home()
    elif st.session_state.page == 'day_book': screen_day_book()
    elif st.session_state.page == 'ledger': screen_ledger()
    elif st.session_state.page == 'scan_hub': screen_scan_hub()
    elif st.session_state.page == 'voice': screen_voice_assistant()
    elif st.session_state.page == 'manual': screen_manual()
    elif st.session_state.page == 'reminders': screen_reminders()
    elif st.session_state.page == 'tools': screen_tools()
except Exception as e:
    st.error("🚨 App Error")
    st.code(traceback.format_exc())
    if st.button("Reload App"): st.rerun()
