
import re
from enum import Enum

class MessageIntent(Enum):
    ADDITIVE = "additive"
    INTERRUPT = "interrupt"
    CANCEL = "cancel"
    APPROVAL = "approval"
    NEW_REQUEST = "new_request"

def mock_classify_message_intent(user_message, has_pending_action):
    msg_lower = user_message.strip().lower()
    
    # Original problematic logic (simplified)
    # if has_pending_action and any(cue in msg_lower for cue in ("no", "do not")):
    #     return "APPROVAL"

    # New logic
    if has_pending_action and any(
        re.search(rf"\b{re.escape(cue)}\b", msg_lower)
        for cue in (
            "approve", "approved", "go ahead", "proceed", "yes",
            "reject", "denied", "deny", "no", "don't do it", "do not",
        )
    ):
        return MessageIntent.APPROVAL
    
    return MessageIntent.ADDITIVE

# Test patterns
test_message = "you not restarted another workflow 2515286"
print(f"Testing message: '{test_message}'")

result = mock_classify_message_intent(test_message, True)
print(f"Classification result: {result}")

if result == MessageIntent.APPROVAL:
    print("FAILED: Message still incorrectly triggering approval/rejection logic.")
else:
    print("SUCCESS: Message correctly ignored by approval logic (due to word boundaries).")

# Test with actual 'no'
test_no = "no, don't do it"
result_no = mock_classify_message_intent(test_no, True)
print(f"\nTesting message: '{test_no}'")
print(f"Classification result: {result_no}")

if result_no == MessageIntent.APPROVAL:
    print("SUCCESS: Actual 'no' still triggers approval logic.")
else:
    print("FAILED: Actual 'no' failed to trigger approval logic.")

# Test ApprovalGate cues
NEW_REQUEST_CUES = (
    "instead", "also", "rather", "check", "investigate", "look into",
    "try", "run", "restart", "disable", "enable", "fix",
)

def mock_looks_like_new_request(message):
    message = message.lower()
    if " instead" in message:
        return True
    return any(re.search(rf"\b{re.escape(cue)}\b", message) for cue in NEW_REQUEST_CUES)

print(f"\nTesting ApprovalGate _looks_like_new_request with: '{test_message}'")
# "restarted" should NOT match "restart" with word boundaries
res_ag = mock_looks_like_new_request(test_message)
print(f"New request detected: {res_ag}")

if not res_ag:
    print("SUCCESS: 'restarted' does not trigger 'restart' cue due to word boundaries.")
else:
    print("FAILED: 'restarted' still triggering 'restart' cue.")
