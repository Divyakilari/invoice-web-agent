import streamlit as st
import json
import io
import time
import pandas as pd
import pdfplumber
from pdf2image import convert_from_bytes
from google import genai
from google.genai import types

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="AI Invoice Agent", layout="centered")
st.title("📄 AI Invoice Data Extractor")
st.write("Upload Stone or Cement PDFs to update your Invoice Register.")

# Sidebar for API Key (or use st.secrets for deployment)
api_key = st.sidebar.text_input("Enter Gemini API Key", type="password")

COLUMNS = [
    "S.No", "Invoice Number", "Invoice Date", "Supplier Name", "Customer Name",
    "SAP Invoice Number", "Item Description", "HSN Code", "UoM", 
    "Quantity (MT/Nos)", "Rate per Unit (₹)", "Taxable Value (₹)", 
    "GST Rate %", "CGST & SGST Amt (₹)", "IGST Amt (₹)", 
    "Freight Charges (₹)", "Total Invoice Value (₹)"
]

# --- 2. CORE LOGIC ---
def get_prompt():
    return f"Extract every line item into a JSON LIST: {COLUMNS}. Sum CGST/SGST. Use 'OPC 43' style descriptions."

def extract_data(client, content, is_image=False):
    model_id = "gemini-3.1-flash-lite"
    if is_image:
        parts = [get_prompt(), types.Part.from_bytes(data=content, mime_type="image/jpeg")]
    else:
        parts = [get_prompt(), content]
    
    response = client.models.generate_content(
        model=model_id, contents=parts,
        config=types.GenerateContentConfig(response_mime_type="application/json")
    )
    return json.loads(response.text.replace("```json", "").replace("```", ""))

# --- 3. UPLOADER ---
uploaded_files = st.file_uploader("Choose PDF files", type="pdf", accept_multiple_files=True)

if uploaded_files and api_key:
    client = genai.Client(api_key=api_key)
    all_extracted_items = []

    if st.button("🚀 Process Invoices"):
        for uploaded_file in uploaded_files:
            st.info(f"Analyzing {uploaded_file.name}...")
            file_bytes = uploaded_file.read()
            
            # Text check
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                text = "\n".join([p.extract_text() for p in pdf.pages if p.extract_text()])
            
            if len(text.strip()) > 50:
                st.write("Mode: Text")
                items = extract_data(client, text)
            else:
                st.write("Mode: Vision (Scanning all pages...)")
                images = convert_from_bytes(file_bytes)
                items = []
                for img in images:
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='JPEG')
                    items.extend(extract_data(client, img_byte_arr.getvalue(), is_image=True))
                    time.sleep(4) # Quota guard
            
            all_extracted_items.extend(items)

        # Create DataFrame
        df = pd.DataFrame(all_extracted_items)
        # Filter junk and format
        df = df[df['Item Description'].str.upper() != "CEMENT"]
        df['S.No'] = range(1, len(df) + 1)
        df = df.reindex(columns=COLUMNS)

        st.success(f"Extracted {len(df)} rows!")
        st.dataframe(df)

        # Download Button
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        
        st.download_button(
            label="📥 Download Invoice_Register.xlsx",
            data=output.getvalue(),
            file_name="Invoice_Register.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
