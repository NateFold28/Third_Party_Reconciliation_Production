@echo off
REM Launch the Third Party Reconciliation Streamlit app.
REM Run this file from anywhere -- it sets the correct working directory.
cd /d "C:\Users\Nate.Fold\projects\PROJECTS\Third_Party_Reconciliation\Combined_Recon_Prod_Pipeline"
"C:\Users\Nate.Fold\projects\.venv\Scripts\streamlit.exe" run app\combined_recon_app.py --server.port 8501
