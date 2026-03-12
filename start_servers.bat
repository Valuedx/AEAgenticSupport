@echo off
TITLE AI Studio Services Startup

echo Starting Agent Server...
start "Agent Server" cmd /k "cd /d D:\AEAgenticSupport && python agent_server.py"

echo Starting MCP Server...
start "MCP Server" cmd /k "cd /d D:\AEAgenticSupport && python -m mcp_server --transport streamable-http --host 127.0.0.1 --port 3000"

echo Starting AI Studio Engine...
start "AI Studio Engine" cmd /k "cd /d D:\AEAgenticSupport\AI_Studio_Local\AIStudio\engine && ..\python\python.exe manage.pyc runserver localhost:8000"

echo Starting Chatbot Webservice...
start "Chatbot Webservice" cmd /k "cd /d D:\AEAgenticSupport\AI_Studio_Local\Chatbot-Webservice\cognibot && ..\python\python.exe manage.pyc runserver localhost:3978"

echo All services launched.
pause
