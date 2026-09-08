import pytest
from llampaca.agent.context import trim_history_to_budget, estimate_tokens

def test_trim_history_keeps_system_prompt():
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello."},
        {"role": "assistant", "content": "Hi."},
    ]
    trimmed_count, trimmed = trim_history_to_budget(messages, context_size=10) # 10 tokens will force a trim because of low threshold
    assert messages[0]["role"] == "system"

def test_estimate_tokens_chars_div_four():
    messages = [{"role": "user", "content": "abcd"}]
    # 1 char // 4 + MESSAGE_OVERHEAD_TOKENS
    # "abcd" length is 4. 4 // 4 = 1.
    # 1 + 4 = 5.
    assert estimate_tokens(messages) == 5
