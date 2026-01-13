import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# --- CONFIGURATION ---
st.set_page_config(page_title="Gautam Pharma Ledger", layout="wide")

# --- GOOGLE SHEETS CONNECTION ---
def get_gsheet_client():
    """Connects to Google Sheets using Streamlit Secrets."""
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        # This grabs the credentials you saved in Streamlit secrets
        credentials = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=scopes
        )
        return gspread.authorize(credentials)
    except Exception as e:
        st.error(f"❌ Connection Error: {e}")
        st.info("Check your .streamlit/secrets.toml file to make sure 'gcp_service_account' is set up correctly.")
        return None

# --- LOAD DATA FUNCTION ---
def load_data(sheet_name):
    """Reads a specific worksheet and returns a DataFrame."""
    try:
        client = get_gsheet_client()
        
        if client is None:
            return pd.DataFrame() # Return empty if connection failed

        # Open the Spreadsheet by Name
        sh = client.open("Gautam_Pharma_Ledger") 
        
        # Open the specific Worksheet
        worksheet = sh.worksheet(sheet_name)
        
        # Get all records
        data = worksheet.get_all_records()
        return pd.DataFrame(data)
        
    except gspread.exceptions.WorksheetNotFound:
        st.warning(f"⚠️ Worksheet named '{sheet_name}' not found.")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"Error loading data: {e}")
        return pd.DataFrame()

# --- MAIN APP INTERFACE ---
def main():
    st.title("Gautam Pharma Ledger")
    st.write("Welcome to the ledger application.")

    # 1. Load the data (Change 'Sheet1' if your tab has a different name)
    df = load_data("CustomerDues")
    
    # 2. Display the data
    if not df.empty:
        st.success("Data loaded successfully!")
        st.dataframe(df, use_container_width=True)
    else:
        st.warning("No data to display yet.")

if __name__ == "__main__":
    main()


