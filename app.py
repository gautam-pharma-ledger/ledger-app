import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI
from datetime import date, datetime, timedelta
import json
from fpdf import FPDF
import base64
import difflib
import urllib.parse
import time

# --- CONFIGURATION ---
st.set_page_config(page_title="Gautam Pharma Ledger", layout="wide", page_icon="💊")

# Custom CSS for "Chips" (Date Buttons) and Cards
st.markdown("""
    <style>
    .metric-card {
        background-color: #f0f2f6;
        border-radius: 10px;
        padding: 20px;
        box-shadow: 2px 2px 5px rgba(0,0,0,0.1);
    }
    .stButton>button { width: 100%; border-radius: 8px; height: 3em; }
    /* Style for date filter buttons to look like chips */
    div[data-testid="column"] .stButton>button {
        background-color: #e0e0ef;
        color: black;
        border: none;
        height: 2.5em;
    }
    div[data-testid="column"] .stButton>button:hover {
        background-color: #d0d0df;
        border: 1px solid #999;
    }
    </style>
    """, unsafe_allow_html=True)

# --- 1. CONNECTION & AUTO-REPAIR ---
@st.cache_resource
def get_gsheet_client():
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        credentials = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
        return gspread.authorize(credentials)
    except Exception as e:
        st.error(f"❌ Connection Error: {e}")
        return None

def check_and_fix_headers():
    """Checks if headers exist. If not, adds them to Row 1."""
    client = get_gsheet_client()
    if not client: return
    try:
        sh = client.open("Gautam_Pharma_Ledger")
        required_headers = {
            "CustomerDues": ["Date", "Party", "Amount"],
            "PaymentsReceived": ["Date", "Party", "Amount", "Mode"],
            "PaymentsToSuppliers": ["Date", "Supplier", "Amount", "Mode"],
            "GoodsReceived": ["Date", "Supplier", "Items", "Amount"]
        }
        for sheet_name, headers in required_headers.items():
            try:
                ws = sh.worksheet(sheet_name)
                existing = ws.row_values(1)
                # If row 1 is empty OR doesn't match the expected "Date" column
                if not existing or existing[0] != "Date":
                    ws.insert_row(headers, 1)
            except: pass
    except: pass

# Run repair once on load
check_and_fix_headers()

@st.cache_resource
def get_sheet_object():
    client = get_gsheet_client()
    if client:
        try: return client.open("Gautam_Pharma_Ledger")
        except: return None
    return None

@st.cache_data(ttl=10)
def fetch_sheet_data(sheet_name):
    try:
        sh = get_sheet_object()
        if not sh: return pd.DataFrame()
        data = sh.worksheet(sheet_name).get_all_records()
        return pd.DataFrame(data)
    except: return pd.DataFrame()

def get_all_party_names():
    names = set()
    for sheet in ["CustomerDues", "PaymentsReceived", "GoodsReceived", "PaymentsToSuppliers"]:
        df = fetch_sheet_data(sheet)
        if not df.empty:
            if "Party" in df.columns: names.update(df["Party"].astype(str).unique())
            if "Supplier" in df.columns: names.update(df["Supplier"].astype(str).unique())
    return sorted([n.strip() for n in list(names) if n.strip()])

# --- 2. AI EXTRACTION ---
def run_ai_extraction(image_bytes):
    try:
        api_key = st.secrets["OPENAI_API_KEY"]
        client = OpenAI(api_key=api_key)
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        prompt = """Analyze this handwritten journal page. Return JSON:
        { "Date": "YYYY-MM-DD", "CustomerDues": [{"Party": "Name", "Amount": 0}],
          "PaymentsReceived": [{"Party": "Name", "Amount": 0, "Mode": "Cash"}],
          "GoodsReceived": [{"Supplier": "Name", "Items": "Desc", "Amount": 0}],
          "PaymentsToSuppliers": [{"Supplier": "Name", "Amount": 0, "Mode": "Cash"}] }"""
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}],
            max_tokens=1000
        )
        content = response.choices[0].message.content.replace("```json", "").replace("```", "")
        return json.loads(content)
    except Exception as e:
        st.error(f"AI Error: {e}")
        return None

# --- 3. PROFESSIONAL PDF GENERATOR ---
def generate_ledger_pdf(party_name, dataframe, total_due, start_d, end_d):
    pdf = FPDF()
    pdf.add_page()
    
    # Title
    pdf.set_font("Arial", 'B', 18)
    pdf.cell(190, 10, "Gautam Pharma", ln=True, align='C')
    pdf.set_font("Arial", 'I', 10)
    pdf.cell(190, 6, "Pharmaceutical Distributors", ln=True, align='C')
    pdf.line(10, 25, 200, 25)
    pdf.ln(10)
    
    # Party Info
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(100, 8, f"To: {party_name}", ln=0)
    pdf.cell(90, 8, f"Statement Date: {date.today()}", ln=1, align='R')
    
    pdf.set_font("Arial", '', 10)
    pdf.cell(100, 6, f"Period: {start_d} to {end_d}", ln=1)
    pdf.ln(5)
    
    # Header
    pdf.set_fill_color(230, 230, 230)
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(25, 8, "Date", 1, 0, 'C', True)
    pdf.cell(85, 8, "Particulars", 1, 0, 'C', True)
    pdf.cell(25, 8, "Debit", 1, 0, 'C', True)
    pdf.cell(25, 8, "Credit", 1, 0, 'C', True)
    pdf.cell(30, 8, "Balance", 1, 1, 'C', True)
    
    # Rows
    pdf.set_font("Arial", '', 9)
    running_bal = 0.0
    
    for _, row in dataframe.iterrows():
        dr = float(row['Debit'])
        cr = float(row['Credit'])
        running_bal += (dr - cr)
        
        pdf.cell(25, 7, str(row['Date']), 1)
        pdf.cell(85, 7, str(row['Description'])[:45], 1)
        pdf.cell(25, 7, f"{dr:,.2f}" if dr > 0 else "-", 1, 0, 'R')
        pdf.cell(25, 7, f"{cr:,.2f}" if cr > 0 else "-", 1, 0, 'R')
        pdf.cell(30, 7, f"{running_bal:,.2f}", 1, 1, 'R')
        
    pdf.ln(5)
    
    # Final Total
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(110, 10, "", 0)
    pdf.cell(40, 10, "Net Closing Balance:", 0, 0, 'R')
    
    status = " (Receivable)" if total_due > 0 else " (Payable)"
    color = (200, 0, 0) if total_due > 0 else (0, 150, 0) # Red if they owe you, Green if you owe them
    pdf.set_text_color(*color)
    pdf.cell(40, 10, f"Rs. {abs(total_due):,.2f} {status}", 0, 1, 'R')
    pdf.set_text_color(0, 0, 0)
    
    # Footer
    pdf.ln(20)
    pdf.set_font("Arial", 'I', 8)
    pdf.cell(0, 5, "This is a computer-generated statement.", align='C')
    
    return pdf.output(dest='S').encode('latin-1')

# --- 4. TABS ---

def tab_dashboard():
    st.markdown("## 📊 Executive Dashboard")
    st.markdown("---")
    if st.button("🔄 Force Refresh Data"):
        st.cache_data.clear()
        st.rerun()

    dues_df = fetch_sheet_data("CustomerDues")
    rx_df = fetch_sheet_data("PaymentsReceived")

    def clean_sum(df):
        if df.empty or "Amount" not in df.columns: return 0.0
        clean_vals = df["Amount"].astype(str).str.replace(r'[^\d.]', '', regex=True)
        return pd.to_numeric(clean_vals, errors='coerce').sum()

    total_sold = clean_sum(dues_df)
    total_recvd = clean_sum(rx_df)
    market_outstanding = total_sold - total_recvd
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Market Outstanding", f"₹{market_outstanding:,.0f}", delta="Receivable")
    col2.metric("Total Collections", f"₹{total_recvd:,.0f}", delta="Cash In")
    col3.metric("Total Sales", f"₹{total_sold:,.0f}")

def tab_scan_ai():
    st.header("📸 AI Journal Scanner")
    existing_parties = get_all_party_names()
    
    if 'extracted_data' not in st.session_state:
        img_file = st.file_uploader("Upload Ledger Photo", type=["jpg", "png", "jpeg"])
        if img_file and st.button("🚀 Extract Data"):
            with st.spinner("AI is reading..."):
                data = run_ai_extraction(img_file.read())
                if data:
                    st.session_state['extracted_data'] = data
                    st.rerun()
    else:
        data = st.session_state['extracted_data']
        st.success("✅ Image Read! Review below.")
        with st.form("review_form"):
            def smart_input(label, scanned_val, key_suffix):
                final_val = scanned_val
                if scanned_val and existing_parties:
                    matches = difflib.get_close_matches(scanned_val, existing_parties, n=1, cutoff=0.7)
                    if matches and matches[0] != scanned_val: final_val = matches[0]
                return st.text_input(label, final_val, key=key_suffix)

            st.markdown("##### 1. Retailers Dues")
            dues = data.get("CustomerDues", [])
            final_dues = []
            for i, d in enumerate(dues):
                c1, c2 = st.columns([3, 1])
                p = smart_input("Party", d.get("Party"), f"d_p_{i}")
                a = c2.number_input("Amount", value=float(d.get("Amount", 0)), key=f"d_a_{i}")
                final_dues.append({"Party": p, "Amount": a})
            
            st.markdown("##### 2. Payments Received")
            rx = data.get("PaymentsReceived", [])
            final_rx = []
            for i, d in enumerate(rx):
                c1, c2, c3 = st.columns([2, 1, 1])
                p = smart_input("Party", d.get("Party"), f"r_p_{i}")
                a = c2.number_input("Amount", value=float(d.get("Amount", 0)), key=f"r_a_{i}")
                m = c3.selectbox("Mode", ["Cash", "UPI"], key=f"r_m_{i}")
                final_rx.append({"Party": p, "Amount": a, "Mode": m})

            st.markdown("##### 3. Payments To Suppliers")
            tx = data.get("PaymentsToSuppliers", [])
            final_tx = []
            for i, d in enumerate(tx):
                c1, c2, c3 = st.columns([2, 1, 1])
                s = smart_input("Supplier", d.get("Supplier"), f"t_s_{i}")
                a = c2.number_input("Amount", value=float(d.get("Amount", 0)), key=f"t_a_{i}")
                m = c3.selectbox("Mode", ["Cash", "UPI"], key=f"t_m_{i}")
                final_tx.append({"Supplier": s, "Amount": a, "Mode": m})
            
            st.markdown("##### 4. Purchase Details")
            gx = data.get("GoodsReceived", [])
            final_gx = []
            for i, d in enumerate(gx):
                c1, c2, c3 = st.columns([2, 2, 1])
                s = smart_input("Supplier", d.get("Supplier"), f"g_s_{i}")
                it = c2.text_input("Items", d.get("Items", "Goods"), key=f"g_i_{i}")
                a = c3.number_input("Amount", value=float(d.get("Amount", 0)), key=f"g_a_{i}")
                final_gx.append({"Supplier": s, "Items": it, "Amount": a})

            if st.form_submit_button("💾 Save to Cloud"):
                sh = get_sheet_object()
                txn_date = data.get("Date", str(date.today()))
                try:
                    def clean_rows(raw_rows):
                        cleaned = []
                        for row in raw_rows:
                            cleaned.append([item if isinstance(item, (int, float)) else str(item).strip() for item in row])
                        return cleaned

                    if final_dues: sh.worksheet("CustomerDues").append_rows(clean_rows([[txn_date, r["Party"], float(r["Amount"])] for r in final_dues if r["Party"]]))
                    if final_rx: sh.worksheet("PaymentsReceived").append_rows(clean_rows([[txn_date, r["Party"], float(r["Amount"], r["Mode"])] for r in final_rx if r["Party"]]))
                    if final_tx: sh.worksheet("PaymentsToSuppliers").append_rows(clean_rows([[txn_date, r["Supplier"], float(r["Amount"]), r["Mode"]] for r in final_tx if r["Supplier"]]))
                    if final_gx: sh.worksheet("GoodsReceived").append_rows(clean_rows([[txn_date, r["Supplier"], r["Items"], float(r["Amount"])] for r in final_gx if r["Supplier"]]))
                    
                    st.success("✅ SAVED SUCCESSFULLY!")
                    st.cache_data.clear()
                    time.sleep(1)
                    del st.session_state['extracted_data']
                    st.rerun()
                except Exception as e: st.error(f"❌ Save Failed: {e}")
        
        if st.button("Cancel / New Scan"):
            del st.session_state['extracted_data']
            st.rerun()

def tab_ledger_view():
    st.header("📒 Party Ledger")
    
    # Party Selection
    all_parties = get_all_party_names()
    sel_party = st.selectbox("Select Party", ["Select..."] + all_parties)

    # Date Filter Session State
    if 'ledger_start_date' not in st.session_state:
        st.session_state['ledger_start_date'] = date.today().replace(day=1)
    if 'ledger_end_date' not in st.session_state:
        st.session_state['ledger_end_date'] = date.today()

    # --- QUICK DATE BUTTONS ---
    st.write("📅 **Quick Filters:**")
    b1, b2, b3, b4 = st.columns(4)
    
    if b1.button("This Month"):
        st.session_state['ledger_start_date'] = date.today().replace(day=1)
        st.session_state['ledger_end_date'] = date.today()
        st.rerun()
        
    if b2.button("Last Month"):
        first = (date.today().replace(day=1) - timedelta(days=1)).replace(day=1)
        last = date.today().replace(day=1) - timedelta(days=1)
        st.session_state['ledger_start_date'] = first
        st.session_state['ledger_end_date'] = last
        st.rerun()

    if b3.button("Last 6 Months"):
        st.session_state['ledger_start_date'] = date.today() - timedelta(days=180)
        st.session_state['ledger_end_date'] = date.today()
        st.rerun()

    if b4.button("Financial Year"):
        today = date.today()
        # If before April, FY starts prev year April. If after April, FY starts this year April.
        if today.month < 4: start_year = today.year - 1
        else: start_year = today.year
        st.session_state['ledger_start_date'] = date(start_year, 4, 1)
        st.session_state['ledger_end_date'] = today
        st.rerun()

    # Manual Date Pickers (Connected to Session State)
    c1, c2 = st.columns(2)
    start_date = c1.date_input("From", st.session_state['ledger_start_date'])
    end_date = c2.date_input("To", st.session_state['ledger_end_date'])

    if sel_party != "Select...":
        ledger_data = []
        def process(sheet, desc, type_cd):
            df = fetch_sheet_data(sheet)
            if df.empty: return
            df.columns = df.columns.str.strip()
            p_col = "Party" if "Party" in df.columns else "Supplier"
            if p_col not in df.columns: return
            df[p_col] = df[p_col].astype(str).str.strip()
            df = df[df[p_col] == sel_party]
            df["Date"] = pd.to_datetime(df["Date"], errors='coerce').dt.date
            df = df[(df["Date"] >= start_date) & (df["Date"] <= end_date)]
            for _, r in df.iterrows():
                try: amt = float(str(r.get("Amount", 0)).replace(",", ""))
                except: amt = 0.0
                entry = {"Date": r["Date"], "Description": desc, "Debit": 0, "Credit": 0}
                if type_cd == "debit": entry["Debit"] = amt
                else: entry["Credit"] = amt
                if "Mode" in r: entry["Description"] += f" ({r['Mode']})"
                ledger_data.append(entry)

        process("CustomerDues", "Sale/Due", "debit")
        process("PaymentsReceived", "Payment Rx", "credit")
        process("GoodsReceived", "Purchase", "credit")
        process("PaymentsToSuppliers", "Payment Made", "debit")

        if ledger_data:
            l_df = pd.DataFrame(ledger_data).sort_values(by="Date")
            
            # Running Balance Calculation
            l_df["Net Change"] = l_df["Debit"] - l_df["Credit"]
            bal = l_df["Net Change"].sum()

            # Display Metrics
            m1, m2, m3 = st.columns(3)
            m1.metric("Total Debit (Sold)", f"₹{l_df['Debit'].sum():,.2f}")
            m2.metric("Total Credit (Recvd)", f"₹{l_df['Credit'].sum():,.2f}")
            m3.metric("Net Balance", f"₹{abs(bal):,.2f}", "Receivable" if bal>0 else "Payable")
            
            st.divider()

            # ACTION BUTTONS
            a1, a2 = st.columns(2)
            
            # 1. PDF Download
            with a1:
                pdf_bytes = generate_ledger_pdf(sel_party, l_df, bal, start_date, end_date)
                st.download_button(
                    label="📄 Download PDF Ledger",
                    data=pdf_bytes,
                    file_name=f"{sel_party}_Ledger_{start_date}_{end_date}.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
            
            # 2. WhatsApp Button
            with a2:
                wa_msg = f"*Gautam Pharma Statement*\n\nHello {sel_party},\n\nYour outstanding balance from {start_date} to {end_date} is *Rs. {abs(bal):,.2f}*.\n\nPlease find the detailed ledger attached (if sent separately) or pay at your earliest convenience."
                wa_link = f"https://wa.me/?text={urllib.parse.quote(wa_msg)}"
                st.link_button("💬 Share Update on WhatsApp", wa_link, use_container_width=True)
                st.caption("ℹ️ Tip: Download the PDF first, then attach it in WhatsApp.")

            st.dataframe(l_df, use_container_width=True)
        else:
            st.info("No transactions found for this period.")

def tab_manual_entry():
    st.header("⌨️ Manual Entry")
    all_parties = get_all_party_names()
    with st.form("manual"):
        c1, c2 = st.columns(2)
        date_val = c1.date_input("Date", date.today())
        entry_type = c2.selectbox("Type", ["Customer Due", "Payment Rx", "Supplier Payment", "Purchase"])
        
        c3, c4 = st.columns(2)
        party_in = c3.selectbox("Party / Supplier", ["Select...", "➕ Add New"] + all_parties)
        if party_in == "➕ Add New": party = c3.text_input("Enter New Name")
        else: party = party_in
        amt = c4.number_input("Amount", min_value=0.0)

        mode = "Cash"
        desc = "Goods"
        if entry_type in ["Payment Rx", "Supplier Payment"]:
            mode = st.selectbox("Mode", ["Cash", "UPI", "Cheque"])
        elif entry_type == "Purchase":
            desc = st.text_input("Item Description", "Goods")

        if st.form_submit_button("Save"):
            if not party or party == "Select..." or amt == 0:
                st.warning("Please fill Party Name and Amount.")
                st.stop()
                
            sh = get_sheet_object()
            if not sh: st.stop()
            try:
                if entry_type == "Customer Due": 
                    sh.worksheet("CustomerDues").append_row([str(date_val), party, amt])
                elif entry_type == "Payment Rx": 
                    sh.worksheet("PaymentsReceived").append_row([str(date_val), party, amt, mode])
                elif entry_type == "Supplier Payment": 
                    sh.worksheet("PaymentsToSuppliers").append_row([str(date_val), party, amt, mode])
                elif entry_type == "Purchase": 
                    sh.worksheet("GoodsReceived").append_row([str(date_val), party, desc, amt])
                
                st.success(f"Saved ₹{amt} for {party}!")
                st.cache_data.clear()
            except Exception as e: st.error(f"Error: {e}")

# --- MAIN ---
def main():
    st.sidebar.title("💊 Gautam Pharma")
    menu = st.sidebar.radio("Menu", ["📊 Dashboard", "📸 Scan (AI)", "📒 Ledger & PDF", "⌨️ Manual Entry"])
    if menu == "📊 Dashboard": tab_dashboard()
    elif menu == "📸 Scan (AI)": tab_scan_ai()
    elif menu == "📒 Ledger & PDF": tab_ledger_view()
    elif menu == "⌨️ Manual Entry": tab_manual_entry()

if __name__ == "__main__":
    main()
