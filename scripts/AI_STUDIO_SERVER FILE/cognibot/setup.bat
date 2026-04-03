@echo off
setlocal enabledelayedexpansion
SET AELI_PYTHON_PATH=python

:processargs
SET ARG=%1
IF DEFINED ARG (
    IF "%ARG%"=="-p" (
    set AELI_PYTHON_PATH=%2
    )
    IF "%ARG%"=="-c" (
    set CUSTOM_CODE_PATH=%2
    )
    SHIFT
    GOTO processargs
)

REM Install dependencies
%AELI_PYTHON_PATH% -m pip install -r requirements.txt --no-cache-dir

REM Apply database migrations
echo Applying database migrations for aistudiobot
%AELI_PYTHON_PATH% manage.pyc migrate aistudiobot

REM Deploy custom code
echo Deploying custom code
IF DEFINED CUSTOM_CODE_PATH (
    %AELI_PYTHON_PATH% copy_custom.pyc -p %AELI_PYTHON_PATH% -c %CUSTOM_CODE_PATH%
) ELSE (
    %AELI_PYTHON_PATH% copy_custom.pyc -p %AELI_PYTHON_PATH%
)

IF !ERRORLEVEL! NEQ 0 (
    echo Error while copying custom chatbot webservice code
    exit /b 1
)

REM Generate dummy data
REM python manage.pyc generate_data

REM Start server
echo Starting server
REM daphne -p 3978 -b 0.0.0.0 webchat_channel.routing:application
%AELI_PYTHON_PATH% manage.pyc runserver