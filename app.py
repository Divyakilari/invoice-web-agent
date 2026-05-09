import streamlit as st
import json
import io
import time
import pandas as pd
import pdfplumber
from pdf2image import convert_from_bytes
from google import genai
from google.genai import types

# --- 1. CONFIGURATION & UI ---
st.set_page_config(page_title="AI Invoice Agent", page_icon="📄", layout="centered")

st.title("📄 AI Invoice Data Extractor")
st.markdown("""
Upload your **Stone** or **Cement** PDFs. This agent will automatically:
1. Decide if the file is a digital document or a scan.
2. Scan all pages for hidden invoices.
3. Format everything for your Master Excel Register.
""")

# --- 2. API KEY SECRETS LOGIC ---
# This looks for GEMINI_API_KEY in Streamlit Cloud "Secrets" first.
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
else:
    # Fallback sidebar for local testing or if secrets aren't set yet
    api_key = st.sidebar.text_input("Enter Gemini API Key", type="password")

COLUMNS = [
    "S.No", "Invoice Number", "Invoice Date", "Supplier Name", "Customer Name",
    "SAP Invoice Number", "Item Description", "HSN Code", "UoM", 
    "Quantity (MT/Nos)", "Rate per Unit (₹)", "Taxable Value (₹)", 
    "GST Rate %", "CGST & SGST Amt (₹)", "IGST Amt (₹)", 
    "Freight Charges (₹)", "Total Invoice Value (₹)"
]

# --- 3. PROMPT LOGIC ---
def get_strict_prompt(context_type):
    return f"""
    Act as a precise data entry clerk. Extract EVERY line item from EVERY invoice found in this {context_type} into a JSON LIST.
    
    ### COLUMN MAPPING RULES:
    1. **Invoice Number**: Look for 'Ref. Inv No', 'Invoice No', or 'Inv No'.
    2. **Supplier Name**: Use 'SAGAR CEMENTS LIMITED' or 'SRI LAXMI STONE PRODUCTS'. 
    3. **Item Description**: Use ONLY the product name (e.g., 'OPC 43 HDPE BAG', '10MM').
    4. **Taxable Value (₹)**: Basic value before GST.
    5. **CGST & SGST Amt (₹)**: Sum the CGST and SGST amounts.
    
    Return a JSON LIST of objects with these keys: {COLUMNS}
    """

def extract_data(client, content, is_image=False):
    model_id = "gemini-3.1-flash-lite"
    prompt = get_strict_prompt("image scan" if is_image else "text")
    
    if is_image:
        parts = [prompt, types.Part.from_bytes(data=content, mime_type="image/jpeg")]
    else:
        parts = [prompt, content]
    
    response = client.models.generate_content(
        model=model_id, 
        contents=parts,
        config=types.GenerateContentConfig(response_mime_type="application/json")
    )
    # Clean the response text to ensure valid JSON
    clean_json = response.text.replace("```json", "").replace("```", "").strip()
    return json.loads(clean_json)

# --- 4. WEB INTERFACE ---
uploaded_files = st.file_uploader("Upload PDF Invoices", type="pdf", accept_multiple_files=True)

if uploaded_files:
    if not api_key:
        st.warning("Please enter an API Key in the sidebar or add it to Streamlit Secrets to proceed.")
    else:
        client = genai.Client(api_key=api_key)
        all_extracted_items = []

        if st.button("🚀 Process Invoices"):
            for uploaded_file in uploaded_files:
                st.info(f"Analyzing {uploaded_file.name}...")
                file_bytes = uploaded_file.read()
                
                # Check if PDF has selectable text
                with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                    raw_text = "\n".join([p.extract_text() for p in pdf.pages if p.extract_text()])
                
                try:
                    if len(raw_text.strip()) > 50:
                        st.write("✅ Mode: Text (Digital PDF)")
                        items = extract_data(client, raw_text)
                    else:
                        st.write("📸 Mode: Vision (Scanned Image - Processing all pages...)")
                        images = convert_from_bytes(file_bytes)
                        items = []
                        progress_bar = st.progress(0)
                        for i, img in enumerate(images):
                            img_byte_arr = io.BytesIO()
                            img.save(img_byte_arr, format='JPEG')
                            items.extend(extract_data(client, img_byte_arr.getvalue(), is_image=True))
                            progress_bar.progress((i + 1) / len(images))
                            time.sleep(4) # Quota guard for free tier
                    
                    all_extracted_items.extend(items)
                except Exception as e:
                    st.error(f"Error processing {uploaded_file.name}: {e}")

            # --- 5. DATA CLEANING & DOWNLOAD ---
            if all_extracted_items:
                df = pd.DataFrame(all_extracted_items)
                
                # Filter out garbage headers or empty rows
                if 'Item Description' in df.columns:
                    df = df[~df['Item Description'].str.upper().isin(["CEMENT", "TOTAL", "SUBTOTAL"])]
                
                # Ensure all columns exist and are in order
                df = df.reindex(columns=COLUMNS)
                df['S.No'] = range(1, len(df) + 1)

                st.success(f"Successfully extracted {len(df)} line items!")
                st.dataframe(df)

                # Excel Export
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False)
                
                st.download_button(
                    label="📥 Download Invoice_Register.xlsx",
                    data=output.getvalue(),
                    file_name="Invoice_Register_Export.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.error("No data could be extracted. Please check the PDF quality.")
