@echo off
setlocal
cd /d "%~dp0"
echo.
echo   Setting up Wavy (one time, ~5 GB download incl. AI models on first song)
echo.

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo   ffmpeg was not found. Install it with:   winget install Gyan.FFmpeg
  echo   then open a new terminal and run setup.bat again.
  exit /b 1
)

py -3.12 --version >nul 2>nul
if errorlevel 1 (
  echo   Python 3.12 was not found. Install it with:   winget install Python.Python.3.12
  exit /b 1
)

if not exist .venv\Scripts\python.exe (
  py -3.12 -m venv .venv || exit /b 1
)
.venv\Scripts\python -m pip install --upgrade pip >nul

where nvidia-smi >nul 2>nul
if errorlevel 1 (
  echo   No NVIDIA GPU found - installing CPU PyTorch. Unbaking will be slow.
  .venv\Scripts\python -m pip install torch torchaudio || exit /b 1
) else (
  echo   Installing PyTorch with CUDA 12.8 ...
  .venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128 || exit /b 1
)

del .venv\constraints.tmp 2>nul
for /f "delims=" %%v in ('.venv\Scripts\python -c "import torch,torchaudio;print('torch=='+torch.__version__+chr(10)+'torchaudio=='+torchaudio.__version__)"') do echo %%v>> .venv\constraints.tmp
move /y .venv\constraints.tmp .venv\constraints.txt >nul

echo   Installing the rest ...
.venv\Scripts\python -m pip install -c .venv\constraints.txt -r requirements.txt || exit /b 1

if not exist samples\demo_duet.wav (
  echo   Generating the demo duet with the built-in Windows voices ...
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\tts_lines.ps1 -OutDir "%TEMP%\wavy_tts" >nul 2>nul && .venv\Scripts\python tools\make_test_song.py "%TEMP%\wavy_tts" samples\demo_duet.wav samples\demo_duet_truth >nul 2>nul
)

echo.
echo   Done. Run start.bat to launch Wavy.
endlocal
