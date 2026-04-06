import os

mappings = [
    (r"D:\AEAgenticSupport\agents\orchestrator.py", r"D:\AEAgenticSupport\AI_Studio_Local\Chatbot-Webservice\cognibot\agents\orchestrator.py"),
    (r"D:\AEAgenticSupport\custom\functions\python\actions.py", r"D:\AEAgenticSupport\AI_Studio_Local\Chatbot-Webservice\cognibot\custom\functions\python\actions.py"),
    (r"D:\AEAgenticSupport\gateway\message_gateway.py", r"D:\AEAgenticSupport\AI_Studio_Local\Chatbot-Webservice\cognibot\gateway\message_gateway.py"),
    (r"D:\AEAgenticSupport\main.py", r"D:\AEAgenticSupport\AI_Studio_Local\Chatbot-Webservice\cognibot\main.py"),
    (r"D:\AEAgenticSupport\tests\test_agent_server_async_reply.py", r"D:\AEAgenticSupport\AI_Studio_Local\Chatbot-Webservice\cognibot\tests\test_agent_server_async_reply.py"),
    (r"D:\AEAgenticSupport\tests\test_aistudio_actions.py", r"D:\AEAgenticSupport\AI_Studio_Local\Chatbot-Webservice\cognibot\tests\test_aistudio_actions.py"),
    (r"D:\AEAgenticSupport\tests\test_approval_gate.py", r"D:\AEAgenticSupport\AI_Studio_Local\Chatbot-Webservice\cognibot\tests\test_approval_gate.py"),
    (r"D:\AEAgenticSupport\tests\test_multi_agent.py", r"D:\AEAgenticSupport\AI_Studio_Local\Chatbot-Webservice\cognibot\tests\test_multi_agent.py"),
    (r"D:\AEAgenticSupport\tests\test_orchestrator_downstream.py", r"D:\AEAgenticSupport\AI_Studio_Local\Chatbot-Webservice\cognibot\tests\test_orchestrator_downstream.py"),
]

for src, dst in mappings:
    if os.path.exists(src) and os.path.exists(dst):
        src_size = os.path.getsize(src)
        dst_size = os.path.getsize(dst)
        print(f"{os.path.basename(src)}: Source={src_size}, Target={dst_size} - {'OK' if src_size == dst_size else 'MISMATCH'}")
    else:
        print(f"{os.path.basename(src)}: MISSING - Source={os.path.exists(src)}, Target={os.path.exists(dst)}")
