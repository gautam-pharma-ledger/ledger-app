import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI
from datetime import date, datetime
import json
from fpdf import FPDF
import base64

# --- CONFIGURATION ---
st.set_page_config(page_title="Gautam Pharma AI", layout="wide", page_icon="💊")

# --- 1. GOOGLE SHEETS CONNECTION ---
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

def get_sheet_data(tab_name):
    """Reads data from a specific Google Sheet tab."""
    try:
        client = get_gsheet_client()
        sh = client.open("Gautam_Pharma_Ledger")
        try:
            ws = sh.worksheet(tab_name)
            data = ws.get_all_records()
            return pd.DataFrame(data)
        except gspread.exceptions.WorksheetNotFound:
            st.error(f"⚠️ Tab '{tab_name}' missing in Google Sheet.")
            return pd.DataFrame()
    except Exception as e:
        return pd.DataFrame()

def append_to_sheet(tab_name, row_data):
    """Appends a list of values to a Google Sheet tab."""
    try:
        client = get_gsheet_client()
        sh = client.open("Gautam_Pharma_Ledger")
        ws = sh.worksheet(tab_name)
        ws.append_row(row_data)
        return True
    except Exception as e:
        st.error(f"❌ Save Failed: {e}")
        return False

# --- 2. AI EXTRACTION ENGINE ---
def run_ai_extraction(image_bytes):
    """Sends image to OpenAI GPT-4o for JSON extraction."""
    try:
        # Get API Key from Streamlit Secrets
        api_key = st.secrets["OPENAI_API_KEY"]
        client = OpenAI(api_key=api_key)
        
        # Encode Image
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        # Fetch known parties to help AI
        parties_df = get_sheet_data("Party_Codes")
        known_parties = ", ".join(parties_df["Name"].tolist()) if not parties_df.empty else "None"

        prompt = f"""
        Extract transaction data from this ledger image. Known parties: {known_parties}.
        Return ONLY valid JSON. Structure:
        {{
          "Date": "YYYY-MM-DD", 
          "CustomerDues": [{{"Party": "Name", "Amount": 0}}], 
          "PaymentsReceived": [{{"Party": "Name", "Amount": 0, "Mode": "Cash"}}], 
          "GoodsReceived": [{{"Supplier": "Name", "Items": "Desc", "Amount": 0}}], 
          "PaymentsToSuppliers": [{{"Supplier": "Name", "Amount": 0, "Mode": "Cash"}}] 
        }}
        """

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ],
                }
            ],
            max_tokens=1000
        )
        
        content = response.choices[0].message.content
        # Clean Markdown if present
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
            
        return json.loads(content)

    except Exception as e:
        st.error(f"AI Error: {e}")
        return None

# --- 3. PDF GENERATOR ---
def generate_pdf(party_name):
    """Generates a ledger PDF for a specific party."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(190, 10, f"Ledger: {party_name}", ln=True, align='C')
    
    # Headers
    pdf.set_font("Arial", 'B', 10)
    pdf.ln(10)
    pdf.cell(30, 10, "Date", 1)
    pdf.cell(80, 10, "Description", 1)
    pdf.cell(30, 10, "Debit", 1)
    pdf.cell(30, 10, "Credit", 1)
    pdf.ln()
    
    # Data Aggregation
    pdf.set_font("Arial", '', 10)
    ledger_items = []
    
    # Check Dues (Debit)
    df = get_sheet_data("CustomerDues")
    if not df.empty:
        rows = df[df['Party'] == party_name]
        for _, r in rows.iterrows(): ledger_items.append((r['Date'], "Sale/Due", float(r['Amount']), 0))

    # Check Payments Rx (Credit)
    df = get_sheet_data("PaymentsReceived")
    if not df.empty:
        rows = df[df['Party'] == party_name]
        for _, r in rows.iterrows(): ledger_items.append((r['Date'], f"Received ({r.get('Mode','Cash')})", 0, float(r['Amount'])))

    # Sort and Print
    ledger_items.sort(key=lambda x: x[0])
    balance = 0
    for date_val, desc, dr, cr in ledger_items:
        balance += (dr - cr) # Positive = Receivable
        pdf.cell(30, 10, str(date_val), 1)
        pdf.cell(80, 10, str(desc), 1)
        pdf.cell(30, 10, str(dr), 1)
        pdf.cell(30, 10, str(cr), 1)
        pdf.ln()

    pdf.ln(5)
    pdf.set_font("Arial", 'B', 12)
    status = "Receivable (They Owe You)" if balance > 0 else "Payable/Clear"
    pdf.cell(190, 10, f"Net Balance: {balance} ({status})", ln=True)
    
    return pdf.output(dest='S').encode('latin-1')

# --- 4. UI MODULES ---

def tab_scan():
    st.header("📸 AI Journal Scanner")
    
    # 1. Upload
    img_file = st.file_uploader("Upload Ledger Photo", type=["jpg", "png", "jpeg"])
    
    if img_file and st.button("🚀 Extract Data"):
        with st.spinner("AI is reading..."):
            data = run_ai_extraction(img_file.read())
            if data:
                st.session_state['extracted_data'] = data
                st.success("Extraction Complete! Review below.")

    # 2. Review & Edit
    if 'extracted_data' in st.session_state:
        data = st.session_state['extracted_data']
        st.divider()
        st.subheader("📝 Review & Save")
        
        with st.form("ai_review_form"):
            # Customer Dues Section
            st.markdown("### Customer Dues (Udhaari)")
            dues = data.get("CustomerDues", [])
            updated_dues = []
            for i, d in enumerate(dues):
                c1, c2 = st.columns(2)
                p = c1.text_input(f"Party {i}", d.get("Party"), key=f"d_p_{i}")
                a = c2.number_input(f"Amount {i}", value=float(d.get("Amount", 0)), key=f"d_a_{i}")
                updated_dues.append({"Date": data.get("Date"), "Party": p, "Amount": a})
            
            # Save Button
            if st.form_submit_button("💾 Save to Google Sheet"):
                # Save Dues
                for row in updated_dues:
                    if row["Party"]:
                        append_to_sheet("CustomerDues", [row["Date"], row["Party"], row["Amount"]])
                
                st.success("✅ Saved Successfully!")
                del st.session_state['extracted_data']
                st.rerun()

def tab_manual():
    st.header("✏️ Manual Data Entry")
    
    tab1, tab2 = st.tabs(["Customer (Retailer)", "Supplier (Purchase)"])
    
    # Customer Entry
    with tab1:
        with st.form("manual_customer"):
            date_val = st.date_input("Date", date.today())
            
            # Load parties for dropdown
            parties = get_sheet_data("Party_Codes")
            party_list = parties["Name"].tolist() if not parties.empty else []
            party_name = st.selectbox("Select Party", ["New..."] + party_list)
            
            if party_name == "New...":
                new_party = st.text_input("Enter New Party Name")
            else:
                new_party = party_name
                
            trans_type = st.radio("Type", ["Sale (Credit Given)", "Payment Received"])
            amount = st.number_input("Amount", min_value=0.0)
            mode = st.selectbox("Mode", ["Cash", "UPI", "Cheque"])
            
            if st.form_submit_button("Save Entry"):
                final_party = new_party
                if trans_type == "Sale (Credit Given)":
                    append_to_sheet("CustomerDues", [str(date_val), final_party, amount])
                else:
                    append_to_sheet("PaymentsReceived", [str(date_val), final_party, amount, mode])
                
                # Check if we need to create new party
                if party_name == "New..." and final_party:
                    append_to_sheet("Party_Codes", ["Auto", "Retailer", final_party, "", "", ""])
                
                st.success("Saved!")

    # Supplier Entry
    with tab2:
        st.info("Supplier Form acts similarly (Purchase / Payment to Supplier)")

def tab_parties():
    st.header("👥 Party Management")
    df = get_sheet_data("Party_Codes")
    if not df.empty:
        st.dataframe(df)
    else:
        st.info("No parties found. Add some in Manual Entry or AI Scan.")

def tab_export():
    st.header("📤 Export Ledger")
    
    parties = get_sheet_data("Party_Codes")
    if not parties.empty:
        selected_party = st.selectbox("Select Party to Download", parties["Name"].unique())
        
        if st.button("Generate PDF"):
            pdf_bytes = generate_pdf(selected_party)
            st.download_button(
                label="⬇️ Download PDF",
                data=pdf_bytes,
                file_name=f"{selected_party}_ledger.pdf",
                mime="application/pdf"
            )

# --- MAIN APP SHELL ---
def main():
    st.sidebar.title("💊 Gautam Pharma")
    
    menu_options = ["Scan (AI)", "Manual Entry", "Parties", "Export Reports"]
    choice = st.sidebar.radio("Navigate", menu_options)
    
    if choice == "Scan (AI)":
        tab_scan()
    elif choice == "Manual Entry":
        tab_manual()
    elif choice == "Parties":
        tab_parties()
    elif choice == "Export Reports":
        tab_export()

if __name__ == "__main__":
    main()
