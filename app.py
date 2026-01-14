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

# --- CONFIGURATION & STYLE ---
st.set_page_config(page_title="Gautam Pharma Ledger", layout="wide", page_icon="💊")

st.markdown("""
    <style>
    .metric-card {
        background-color: #f0f2f6;
        border-radius: 10px;
        padding: 20px;
        box-shadow: 2px 2px 5px rgba(0,0,0,0.1);
    }
    .stButton>button {
        width: 100%;
        border-radius: 8px;
        height: 3em;
    }
    </style>
    """, unsafe_allow_html=True)

# --- 1. CONNECTION & CACHING ---
@st.cache_resource
def get_gsheet_client():
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        credentials = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=scopes
        )
        return gspread.authorize(credentials)
    except Exception as e:
        st.error(f"❌ Connection Error: {e}")
        return None

@st.cache_resource
def get_sheet_object():
    client = get_gsheet_client()
    if client:
        try:
            return client.open("Gautam_Pharma_Ledger")
        except: return None
    return None

# CACHED DATA FETCHING
@st.cache_data(ttl=10) # Short cache to keep dashboard fresh
def fetch_sheet_data(sheet_name):
    try:
        sh = get_sheet_object()
        if not sh: return pd.DataFrame()
        data = sh.worksheet(sheet_name).get_all_records()
        return pd.DataFrame(data)
    except:
        return pd.DataFrame()

def get_all_party_names():
    names = set()
    for sheet in ["CustomerDues", "PaymentsReceived", "GoodsReceived", "PaymentsToSuppliers"]:
        df = fetch_sheet_data(sheet)
        if not df.empty:
            if "Party" in df.columns: names.update(df["Party"].dropna().astype(str).unique())
            if "Supplier" in df.columns: names.update(df["Supplier"].dropna().astype(str).unique())
    return sorted([n.strip() for n in list(names) if n.strip()])

# --- 2. AI EXTRACTION ---
def run_ai_extraction(image_bytes):
    try:
        api_key = st.secrets["OPENAI_API_KEY"]
        client = OpenAI(api_key=api_key)
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        prompt = """
        Analyze this handwritten journal page. Map the sections as follows:
        - "RETAILERS DUES" -> CustomerDues
        - "PAYMENT RECEIVED" -> PaymentsReceived
        - "PAYMENT TO SUPPLIER" -> PaymentsToSuppliers
        - "PURCHASE DETAILS" -> GoodsReceived

        Return ONLY valid JSON with this structure:
        {
          "Date": "YYYY-MM-DD", 
          "CustomerDues": [{"Party": "Name", "Amount": 0}], 
          "PaymentsReceived": [{"Party": "Name", "Amount": 0, "Mode": "Cash"}], 
          "GoodsReceived": [{"Supplier": "Name", "Items": "Desc", "Amount": 0}], 
          "PaymentsToSuppliers": [{"Supplier": "Name", "Amount": 0, "Mode": "Cash"}] 
        }
        """
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

# --- 3. PDF GENERATOR ---
def generate_ledger_pdf(party_name, dataframe, total_due, start_d, end_d):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(190, 10, "Gautam Pharma - Statement of Account", ln=True, align='C')
    pdf.set_font("Arial", '', 12)
    pdf.cell(190, 10, f"Party: {party_name}", ln=True, align='L')
    pdf.cell(190, 10, f"Period: {start_d} to {end_d}", ln=True, align='L')
    pdf.ln(5)
    pdf.set_fill_color(240, 240, 240)
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(30, 10, "Date", 1, 0, 'C', True)
    pdf.cell(80, 10, "Description", 1, 0, 'C', True)
    pdf.cell(30, 10, "Debit", 1, 0, 'C', True)
    pdf.cell(30, 10, "Credit", 1, 1, 'C', True)
    pdf.set_font("Arial", '', 10)
    for _, row in dataframe.iterrows():
        pdf.cell(30, 10, str(row['Date']), 1)
        pdf.cell(80, 10, str(row['Description'])[:35], 1)
        pdf.cell(30, 10, str(row['Debit']), 1)
        pdf.cell(30, 10, str(row['Credit']), 1)
        pdf.ln()
    pdf.ln(5)
    pdf.set_font("Arial", 'B', 12)
    status = "RECEIVABLE (They Owe You)" if total_due > 0 else "PAYABLE (You Owe Them)"
    pdf.cell(190, 10, f"Net Balance: Rs. {total_due:,.2f}  [{status}]", ln=True)
    return pdf.output(dest='S').encode('latin-1')

# --- 4. APP MODULES ---

def tab_dashboard():
    st.markdown("## 📊 Executive Dashboard")
    st.markdown("---")
    
    if st.button("🔄 Force Refresh Data"):
        st.cache_data.clear()
        st.rerun()

    # Use Cached Data
    dues_df = fetch_sheet_data("CustomerDues")
    rx_df = fetch_sheet_data("PaymentsReceived")

    def clean_sum(df):
        if df.empty or "Amount" not in df.columns: return 0.0
        # Aggressive cleaning
        clean_vals = df["Amount"].astype(str).str.replace(r'[^\d.]', '', regex=True) # Remove everything except numbers and dots
        return pd.to_numeric(clean_vals, errors='coerce').sum()

    total_sold = clean_sum(dues_df)
    total_recvd = clean_sum(rx_df)
    market_outstanding = total_sold - total_recvd
    
    todays_coll = 0.0
    if not rx_df.empty and "Date" in rx_df.columns:
        today_str = str(date.today())
        rx_df["Date"] = rx_df["Date"].astype(str)
        todays_df = rx_df[rx_df["Date"] == today_str]
        todays_coll = clean_sum(todays_df)

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Market Outstanding", f"₹{market_outstanding:,.0f}", delta="Receivable")
    col2.metric("Today's Collection", f"₹{todays_coll:,.0f}", delta="Cash Flow")
    col3.metric("Total Sales (Lifetime)", f"₹{total_sold:,.0f}")
    
    st.markdown("---")
    c1, c2 = st.columns(2)
    c1.info("💡 **Tip:** Use 'Scan (AI)' for daily entries.")
    c2.info("💡 **Tip:** 'Ledger' allows PDF downloads & WhatsApp sharing.")

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
        # REVIEW SCREEN
        data = st.session_state['extracted_data']
        st.success("✅ Image Read! Review below.")
        
        with st.form("review_form"):
            def smart_input(label, scanned_val, key_suffix):
                final_val = scanned_val
                if scanned_val and existing_parties:
                    matches = difflib.get_close_matches(scanned_val, existing_parties, n=1, cutoff=0.7)
                    if matches and matches[0] != scanned_val:
                        final_val = matches[0]
                return st.text_input(label, final_val, key=key_suffix)

            # 1. Retailers Dues
            st.markdown("##### 1. Retailers Dues")
            dues = data.get("CustomerDues", [])
            final_dues = []
            for i, d in enumerate(dues):
                c1, c2 = st.columns([3, 1])
                p = smart_input("Party", d.get("Party"), f"d_p_{i}")
                a = c2.number_input("Amount", value=float(d.get("Amount", 0)), key=f"d_a_{i}")
                final_dues.append({"Party": p, "Amount": a})
            
            # 2. Payments Rx
            st.markdown("##### 2. Payments Received")
            rx = data.get("PaymentsReceived", [])
            final_rx = []
            for i, d in enumerate(rx):
                c1, c2, c3 = st.columns([2, 1, 1])
                p = smart_input("Party", d.get("Party"), f"r_p_{i}")
                a = c2.number_input("Amount", value=float(d.get("Amount", 0)), key=f"r_a_{i}")
                m = c3.selectbox("Mode", ["Cash", "UPI"], key=f"r_m_{i}")
                final_rx.append({"Party": p, "Amount": a, "Mode": m})

            # 3. Payments To Suppliers
            st.markdown("##### 3. Payments To Suppliers")
            tx = data.get("PaymentsToSuppliers", [])
            final_tx = []
            for i, d in enumerate(tx):
                c1, c2, c3 = st.columns([2, 1, 1])
                s = smart_input("Supplier", d.get("Supplier"), f"t_s_{i}")
                a = c2.number_input("Amount", value=float(d.get("Amount", 0)), key=f"t_a_{i}")
                m = c3.selectbox("Mode", ["Cash", "UPI"], key=f"t_m_{i}")
                final_tx.append({"Supplier": s, "Amount": a, "Mode": m})
            
            # 4. Purchase Details
            st.markdown("##### 4. Purchase Details")
            gx = data.get("GoodsReceived", [])
            final_gx = []
            for i, d in enumerate(gx):
                c1, c2, c3 = st.columns([2, 2, 1])
                s = smart_input("Supplier", d.get("Supplier"), f"g_s_{i}")
                it = c2.text_input("Items", d.get("Items", "Goods"), key=f"g_i_{i}")
                a = c3.number_input("Amount", value=float(d.get("Amount", 0)), key=f"g_a_{i}")
                final_gx.append({"Supplier": s, "Items": it, "Amount": a})

            submitted = st.form_submit_button("💾 Save to Cloud")
            
            if submitted:
                sh = get_sheet_object()
                txn_date = data.get("Date", str(date.today()))
                
                try:
                    # LOGGING for Debug
                    st.write("🔄 Connecting to Google Sheets...")
                    
                    # Helper to clean Rows
                    def clean_rows(raw_rows):
                        cleaned = []
                        for row in raw_rows:
                            # Convert all elements to string or float, remove None
                            new_row = []
                            for item in row:
                                if isinstance(item, (int, float)): new_row.append(item)
                                else: new_row.append(str(item).strip())
                            cleaned.append(new_row)
                        return cleaned

                    if final_dues: 
                        rows = [[txn_date, r["Party"], float(r["Amount"])] for r in final_dues if r["Party"]]
                        st.write(f"📝 Saving {len(rows)} rows to CustomerDues...")
                        sh.worksheet("CustomerDues").append_rows(clean_rows(rows))
                    
                    if final_rx: 
                        rows = [[txn_date, r["Party"], float(r["Amount"]), r["Mode"]] for r in final_rx if r["Party"]]
                        st.write(f"📝 Saving {len(rows)} rows to PaymentsReceived...")
                        sh.worksheet("PaymentsReceived").append_rows(clean_rows(rows))
                    
                    if final_tx: 
                        rows = [[txn_date, r["Supplier"], float(r["Amount"]), r["Mode"]] for r in final_tx if r["Supplier"]]
                        st.write(f"📝 Saving {len(rows)} rows to PaymentsToSuppliers...")
                        sh.worksheet("PaymentsToSuppliers").append_rows(clean_rows(rows))
                    
                    if final_gx: 
                        rows = [[txn_date, r["Supplier"], r["Items"], float(r["Amount"])] for r in final_gx if r["Supplier"]]
                        st.write(f"📝 Saving {len(rows)} rows to GoodsReceived...")
                        sh.worksheet("GoodsReceived").append_rows(clean_rows(rows))
                    
                    st.success("✅ SAVED SUCCESSFULLY!")
                    st.balloons()
                    st.cache_data.clear() # Clear cache so Dashboard updates
                    
                    # DO NOT RERUN - Let user see the success message
                    st.info("Click the button below to scan a new image.")
                    
                except Exception as e:
                    st.error(f"❌ Save Failed: {e}")

        if st.button("📸 Scan New Image"):
            del st.session_state['extracted_data']
            st.rerun()

def tab_ledger_view():
    st.header("📒 Party Ledger")
    
    col_sel, col_date1, col_date2 = st.columns([2, 1, 1])
    with col_sel:
        all_parties = get_all_party_names()
        sel_party = st.selectbox("Select Party", ["Select..."] + all_parties)
    with col_date1: start_date = st.date_input("From Date", date.today() - timedelta(days=30))
    with col_date2: end_date = st.date_input("To Date", date.today())
        
    if sel_party != "Select...":
        ledger_data = []
        
        def process_cached_sheet(sheet_name, desc, type_cr_dr):
            df = fetch_sheet_data(sheet_name)
            if df.empty: return
            
            df.columns = df.columns.str.strip()
            p_col = "Party" if "Party" in df.columns else "Supplier"
            if p_col not in df.columns: return

            df[p_col] = df[p_col].astype(str).str.strip()
            df = df[df[p_col] == sel_party]
            df["Date"] = pd.to_datetime(df["Date"], errors='coerce').dt.date
            df = df[(df["Date"] >= start_date) & (df["Date"] <= end_date)]

            for _, r in df.iterrows():
                # Aggressive cleaning of Amounts
                raw_amt = str(r.get("Amount", 0))
                clean_amt = "".join(filter(lambda x: x.isdigit() or x == '.', raw_amt))
                try: amt = float(clean_amt)
                except: amt = 0.0
                
                entry = {"Date": r["Date"], "Description": desc, "Debit": 0, "Credit": 0}
                if type_cr_dr == "debit": entry["Debit"] = amt
                else: entry["Credit"] = amt
                if "Mode" in r: entry["Description"] += f" ({r['Mode']})"
                ledger_data.append(entry)

        process_cached_sheet("CustomerDues", "Sale/Due", "debit")
        process_cached_sheet("PaymentsReceived", "Payment Rx", "credit")
        process_cached_sheet("GoodsReceived", "Purchase", "credit")
        process_cached_sheet("PaymentsToSuppliers", "Payment Made", "debit")

        if ledger_data:
            l_df = pd.DataFrame(ledger_data).sort_values(by="Date")
            total_debit = l_df["Debit"].sum()
            total_credit = l_df["Credit"].sum()
            net_bal = total_debit - total_credit
            
            c1, c2, c3 = st.columns(3)
            c1.metric("Sold (Debit)", f"₹{total_debit:,.0f}")
            c2.metric("Received (Credit)", f"₹{total_credit:,.0f}")
            status = "TO RECEIVE" if net_bal > 0 else "TO PAY"
            c3.metric("Net Balance", f"₹{abs(net_bal):,.0f}", status, delta_color="inverse")
            
            wa_msg = f"Hello {sel_party}, your outstanding balance is Rs. {abs(net_bal):,.2f}. Please pay ASAP."
            wa_link = f"https://wa.me/?text={urllib.parse.quote(wa_msg)}"
            
            act1, act2 = st.columns(2)
            with act1:
                pdf_bytes = generate_ledger_pdf(sel_party, l_df, net_bal, start_date, end_date)
                st.download_button("📄 Download PDF", data=pdf_bytes, file_name=f"{sel_party}_Statement.pdf", mime="application/pdf", use_container_width=True)
            with act2:
                st.link_button("💬 Share via WhatsApp", wa_link, use_container_width=True)

            st.dataframe(l_df, use_container_width=True, hide_index=True)
        else:
            st.info("No transactions found.")

def tab_manual_entry():
    st.header("⌨️ Manual Entry")
    all_parties = get_all_party_names()
    with st.form("manual"):
        party = st.selectbox("Party", ["Select...", "Add New"] + all_parties)
        if party == "Add New": party = st.text_input("Name")
        amt = st.number_input("Amount")
        type_ = st.selectbox("Type", ["Customer Due", "Payment Rx", "Supplier Payment", "Purchase"])
        if st.form_submit_button("Save"):
            sh = get_sheet_object()
            if not sh: st.stop()
            
            # Simple saving without batch logic for manual entry
            try:
                row = []
                if type_ == "Customer Due": 
                    sh.worksheet("CustomerDues").append_row([str(date.today()), party, amt])
                elif type_ == "Payment Rx": 
                    sh.worksheet("PaymentsReceived").append_row([str(date.today()), party, amt, "Cash"])
                elif type_ == "Supplier Payment": 
                    sh.worksheet("PaymentsToSuppliers").append_row([str(date.today()), party, amt, "Cash"])
                elif type_ == "Purchase": 
                    sh.worksheet("GoodsReceived").append_row([str(date.today()), party, "Goods", amt])
                
                st.success("Saved!")
                st.cache_data.clear()
            except Exception as e:
                st.error(f"Error: {e}")

# --- MAIN MENU ---
def main():
    st.sidebar.title("💊 Gautam Pharma")
    menu = st.sidebar.radio("Menu", ["📊 Dashboard", "📸 Scan (AI)", "📒 Ledger & PDF", "⌨️ Manual Entry"])
    
    if menu == "📊 Dashboard": tab_dashboard()
    elif menu == "📸 Scan (AI)": tab_scan_ai()
    elif menu == "📒 Ledger & PDF": tab_ledger_view()
    elif menu == "⌨️ Manual Entry": tab_manual_entry()

if __name__ == "__main__":
    main()
