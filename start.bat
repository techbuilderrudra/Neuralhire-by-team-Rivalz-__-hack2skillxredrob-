@echo off
echo.
echo  ============================================
echo    NeuralHire - Starting...
echo  ============================================
echo.
echo  If this is your first run, make sure you:
echo  1. Renamed env.example to .env
echo  2. Filled in your GEMINI_API_KEY in .env
echo  3. Installed: pip install -r requirements.txt
echo.
python builder.py
pause
