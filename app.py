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

# --- 1. OPTIMIZED GOOGLE SHEETS CONNECTION (The Fix) ---
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
    """Opens the sheet ONCE and remembers it to avoid API Rate Limits."""
    client = get_gsheet_client()
    if client:
        try:
            return client.open("Gautam_Pharma_Ledger")
        except gspread.exceptions.SpreadsheetNotFound:
            st.error("❌ Critical Error: Google Sheet 'Gautam_Pharma_Ledger' not found. Check spelling.")
            return None
        except Exception as e:
            st.error(f"❌ Google API Error: {e}")
            return None
    return None

def get_all_party_names():
    names = set()
    try:
        sh = get_sheet_object()
        if not sh: return []
        # Combine names from all sheets
        try: names.update(sh.worksheet("CustomerDues").col_values(2)[1:]) 
        except: pass
        try: names.update(sh.worksheet("PaymentsReceived").col_values(2)[1:]) 
        except: pass
        try: names.update(sh.worksheet("GoodsReceived").col_values(2)[1:]) 
        except: pass
        try: names.update(sh.worksheet("PaymentsToSuppliers").col_values(2)[1:]) 
        except: pass
    except: pass
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

# --- A. DASHBOARD (Fixed & Cached) ---
def tab_dashboard():
    st.markdown("## 📊 Executive Dashboard")
    st.markdown("---")
    
    if st.button("🔄 Refresh Data"):
        st.cache_resource.clear() # Clears cache to fetch fresh data
        st.rerun()

    with st.spinner("Crunching the numbers..."):
        sh = get_sheet_object()
        if not sh: return
        
        def get_total(sheet_name):
            try:
                data = sh.worksheet(sheet_name).get_all_records()
                df = pd.DataFrame(data)
                if df.empty or "Amount" not in df.columns: return 0.0
                clean_vals = df["Amount"].astype(str).str.replace(",", "").str.replace("₹", "").str.replace("Rs", "")
                return pd.to_numeric(clean_vals, errors='coerce').sum()
            except: return 0.0

        def get_todays_coll():
            try:
                data = sh.worksheet("PaymentsReceived").get_all_records()
                df = pd.DataFrame(data)
                if df.empty or "Amount" not in df.columns or "Date" not in df.columns: return 0.0
                today_str = str(date.today())
                df["Date"] = df["Date"].astype(str)
                todays_df = df[df["Date"] == today_str]
                clean_vals = todays_df["Amount"].astype(str).str.replace(",", "").str.replace("₹", "")
                return pd.to_numeric(clean_vals, errors='coerce').sum()
            except: return 0.0

        total_sold = get_total("CustomerDues")
        total_recvd = get_total("PaymentsReceived")
        market_outstanding = total_sold - total_recvd
        todays_coll = get_todays_coll()
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Market Outstanding", f"₹{market_outstanding:,.0f}", delta="Receivable")
        col2.metric("Today's Collection", f"₹{todays_coll:,.0f}", delta="Cash Flow")
        col3.metric("Total Sales (Lifetime)", f"₹{total_sold:,.0f}")
        
        st.markdown("---")
        st.subheader("🚀 Quick Actions")
        c1, c2 = st.columns(2)
        c1.info("💡 **Tip:** Go to 'Scan (AI)' to upload today's journal pages.")
        c2.info("💡 **Tip:** Go to 'Ledger' to send payment reminders.")

# --- B. SCANNER (Fixed) ---
def tab_scan_ai():
    st.header("📸 AI Journal Scanner")
    existing_parties = get_all_party_names()
    
    img_file = st.file_uploader("Upload Ledger Photo", type=["jpg", "png", "jpeg"])
    
    if img_file and st.button("🚀 Extract Data"):
        with st.spinner("AI is analyzing handwriting..."):
            data = run_ai_extraction(img_file.read())
            if data:
                st.session_state['extracted_data'] = data
                st.success("Extraction Complete! Review below.")

    if 'extracted_data' in st.session_state:
        data = st.session_state['extracted_data']
        st.divider()
        st.subheader("📝 Review & Save")
        
        with st.form("review_form"):
            def smart_input(label, scanned_val, key_suffix):
                final_val = scanned_val
                msg = None
                if scanned_val and existing_parties:
                    matches = difflib.get_close_matches(scanned_val, existing_parties, n=1, cutoff=0.7)
                    if matches and matches[0] != scanned_val:
                        msg = f"⚠️ Auto-corrected '{scanned_val}' to '{matches[0]}'"
                        final_val = matches[0]
                if msg: st.caption(msg)
                return st.text_input(label, final_val, key=key_suffix)

            # RETAILERS DUES
            st.markdown("##### 1. Retailers Dues")
            dues = data.get("CustomerDues", [])
            final_dues = []
            if not dues: st.caption("No data found.")
            for i, d in enumerate(dues):
                c1, c2 = st.columns([3, 1])
                p = smart_input("Party", d.get("Party"), f"d_p_{i}")
                a = c2.number_input("Amount", value=float(d.get("Amount", 0)), key=f"d_a_{i}")
                final_dues.append({"Party": p, "Amount": a})
            
            # PAYMENTS RECEIVED
            st.markdown("##### 2. Payments Received")
            rx = data.get("PaymentsReceived", [])
            final_rx = []
            if not rx: st.caption("No data found.")
            for i, d in enumerate(rx):
                c1, c2, c3 = st.columns([2, 1, 1])
                p = smart_input("Party", d.get("Party"), f"r_p_{i}")
                a = c2.number_input("Amount", value=float(d.get("Amount", 0)), key=f"r_a_{i}")
                m = c3.selectbox("Mode", ["Cash", "UPI"], key=f"r_m_{i}")
                final_rx.append({"Party": p, "Amount": a, "Mode": m})

            # PAYMENTS TO SUPPLIERS
            st.markdown("##### 3. Payments To Suppliers")
            tx = data.get("PaymentsToSuppliers", [])
            final_tx = []
            if not tx: st.caption("No data found.")
            for i, d in enumerate(tx):
                c1, c2, c3 = st.columns([2, 1, 1])
                s = smart_input("Supplier", d.get("Supplier"), f"t_s_{i}")
                a = c2.number_input("Amount", value=float(d.get("Amount", 0)), key=f"t_a_{i}")
                m = c3.selectbox("Mode", ["Cash", "UPI"], key=f"t_m_{i}")
                final_tx.append({"Supplier": s, "Amount": a, "Mode": m})
            
            # PURCHASE DETAILS
            st.markdown("##### 4. Purchase Details")
            gx = data.get("GoodsReceived", [])
            final_gx = []
            if not gx: st.caption("No data found.")
            for i, d in enumerate(gx):
                c1, c2, c3 = st.columns([2, 2, 1])
                s = smart_input("Supplier", d.get("Supplier"), f"g_s_{i}")
                it = c2.text_input("Items", d.get("Items", "Goods"), key=f"g_i_{i}")
                a = c3.number_input("Amount", value=float(d.get("Amount", 0)), key=f"g_a_{i}")
                final_gx.append({"Supplier": s, "Items": it, "Amount": a})

            # SAVE BUTTON
            if st.form_submit_button("💾 Save to Cloud"):
                sh = get_sheet_object()
                if not sh: st.stop() # Stop if connection failed
                
                txn_date = data.get("Date", str(date.today()))
                
                try:
                    # Save Dues
                    if final_dues: 
                        rows = [[txn_date, r["Party"], float(r["Amount"])] for r in final_dues if r["Party"]]
                        if rows: sh.worksheet("CustomerDues").append_rows(rows)
                    
                    # Save Rx
                    if final_rx: 
                        rows = [[txn_date, r["Party"], float(r["Amount"]), r["Mode"]] for r in final_rx if r["Party"]]
                        if rows: sh.worksheet("PaymentsReceived").append_rows(rows)
                    
                    # Save Suppliers
                    if final_tx: 
                        rows = [[txn_date, r["Supplier"], float(r["Amount"]), r["Mode"]] for r in final_tx if r["Supplier"]]
                        if rows: sh.worksheet("PaymentsToSuppliers").append_rows(rows)
                    
                    # Save Purchases
                    if final_gx: 
                        rows = [[txn_date, r["Supplier"], r["Items"], float(r["Amount"])] for r in final_gx if r["Supplier"]]
                        if rows: sh.worksheet("GoodsReceived").append_rows(rows)
                    
                    st.success("✅ SAVED SUCCESSFULLY! App will refresh momentarily.")
                    time.sleep(2)
                    del st.session_state['extracted_data']
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Save Failed (Wait 1 min & try again): {e}")

# --- C. LEDGER ---
def tab_ledger_view():
    st.header("📒 Party Ledger & Statements")
    
    col_sel, col_date1, col_date2 = st.columns([2, 1, 1])
    with col_sel:
        all_parties = get_all_party_names()
        sel_party = st.selectbox("Select Party", ["Select..."] + all_parties)
    with col_date1: start_date = st.date_input("From Date", date.today() - timedelta(days=30))
    with col_date2: end_date = st.date_input("To Date", date.today())
        
    if sel_party != "Select...":
        sh = get_sheet_object()
        if not sh: return

        ledger_data = []
        
        def fetch_sheet(sheet_name, desc_label, type_cr_dr):
            try:
                df = pd.DataFrame(sh.worksheet(sheet_name).get_all_records())
                if df.empty: return
                df.columns = df.columns.str.strip()
                p_col = "Party" if "Party" in df.columns else "Supplier"
                if p_col not in df.columns: return

                df[p_col] = df[p_col].astype(str).str.strip()
                df = df[df[p_col] == sel_party]
                df["Date"] = pd.to_datetime(df["Date"], errors='coerce').dt.date
                df = df[(df["Date"] >= start_date) & (df["Date"] <= end_date)]
                
                for _, r in df.iterrows():
                    amt_str = str(r.get("Amount", 0)).replace(",", "").replace("₹", "")
                    try: amt = float(amt_str)
                    except: amt = 0.0
                    
                    entry = {
                        "Date": r["Date"], "Description": desc_label,
                        "Debit": amt if type_cr_dr == "debit" else 0,
                        "Credit": amt if type_cr_dr == "credit" else 0
                    }
                    if "Mode" in r: entry["Description"] += f" ({r['Mode']})"
                    ledger_data.append(entry)
            except: pass

        with st.spinner("Generating Statement..."):
            fetch_sheet("CustomerDues", "Sale/Due", "debit")
            fetch_sheet("PaymentsReceived", "Payment Rx", "credit")
            fetch_sheet("GoodsReceived", "Purchase", "credit")
            fetch_sheet("PaymentsToSuppliers", "Payment Made", "debit")

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
            
            wa_msg = f"Hello {sel_party}, your outstanding balance with Gautam Pharma from {start_date} to {end_date} is Rs. {abs(net_bal):,.2f}. Please pay at the earliest."
            wa_link = f"https://wa.me/?text={urllib.parse.quote(wa_msg)}"
            
            act1, act2 = st.columns(2)
            with act1:
                pdf_bytes = generate_ledger_pdf(sel_party, l_df, net_bal, start_date, end_date)
                st.download_button("📄 Download PDF", data=pdf_bytes, file_name=f"{sel_party}_Statement.pdf", mime="application/pdf", use_container_width=True)
            with act2:
                st.link_button("💬 Share via WhatsApp", wa_link, use_container_width=True)

            st.markdown("### Transaction Details")
            st.dataframe(l_df, use_container_width=True, hide_index=True)
        else:
            st.info("No transactions found in this date range.")

# --- MAIN MENU ---
def main():
    st.sidebar.title("💊 Gautam Pharma")
    menu = st.sidebar.radio("Menu", ["📊 Dashboard", "📸 Scan (AI)", "📒 Ledger & PDF", "⌨️ Manual Entry"])
    
    if menu == "📊 Dashboard": tab_dashboard()
    elif menu == "📸 Scan (AI)": tab_scan_ai()
    elif menu == "📒 Ledger & PDF": tab_ledger_view()
    elif menu == "⌨️ Manual Entry":
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
                
                if type_ == "Customer Due": sh.worksheet("CustomerDues").append_row([str(date.today()), party, amt])
                elif type_ == "Payment Rx": sh.worksheet("PaymentsReceived").append_row([str(date.today()), party, amt, "Cash"])
                elif type_ == "Supplier Payment": sh.worksheet("PaymentsToSuppliers").append_row([str(date.today()), party, amt, "Cash"])
                elif type_ == "Purchase": sh.worksheet("GoodsReceived").append_row([str(date.today()), party, "Goods", amt])
                st.success("Saved!")

if __name__ == "__main__":
    main()
