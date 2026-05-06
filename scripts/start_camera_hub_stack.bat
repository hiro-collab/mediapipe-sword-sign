@echo off
setlocal
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%.."
uv run python "%SCRIPT_DIR%camera_hub_stack.py" %*
