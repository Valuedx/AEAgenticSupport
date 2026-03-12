# AI Studio Run Guide

This document contains instructions for starting the various components of the AI Studio system.

## Components

### 1. Agent Server
Responsible for agentic logic and communication.
- **Directory**: `D:\AEAgenticSupport`
- **Command**: `python agent_server.py`

### 2. MCP Server
Provides Model Context Protocol (MCP) tools over HTTP.
- **Directory**: `D:\AEAgenticSupport`
- **Command**: `python -m mcp_server --transport streamable-http --host 127.0.0.1 --port 3000`

### 3. AI Studio Engine
The main backend engine for AI Studio.
- **Directory**: `D:\AEAgenticSupport\AI_Studio_Local\AIStudio\engine`
- **Python**: `D:\AEAgenticSupport\AI_Studio_Local\AIStudio\python\python.exe`
- **Command**: `..\python\python.exe manage.pyc runserver localhost:8000`

### 4. Chatbot Webservice (Cognibot)
The chatbot interface service.
- **Directory**: `D:\AEAgenticSupport\AI_Studio_Local\Chatbot-Webservice\cognibot`
- **Python**: `D:\AEAgenticSupport\AI_Studio_Local\Chatbot-Webservice\python\python.exe`
- **Command**: `..\python\python.exe manage.pyc runserver localhost:3978`

---

## Quick Start

You can use the provided `start_servers.bat` script to launch all components in separate terminal windows.

```bash
cd D:\AEAgenticSupport
.\start_servers.bat
```
