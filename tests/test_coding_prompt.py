"""Unit tests for coding-intent detection and system-prompt routing in app/llm.py.

Run with:  python -m pytest tests/  (from the repo root, inside the venv)
"""
from app import llm

CODING_QUESTIONS = [
    "Write a python script that renames all files in a folder",
    "Can you write me a bash script to back up a directory?",
    "write a powershell script to list running processes",
    "Fix this code: def foo(): return 1/0",
    "Debug my script, it throws a traceback",
    "Write a regex to match email addresses",
    "write unit tests for this function",
    "How do I code a simple calculator in Python?",
]

NON_CODING_QUESTIONS = [
    "What are the key differences between cost engineering requirements for "
    "Civil Works versus Military Programs?",
    "Does this submittal comply with ER 1110-345-700?",
    "Please draft a memo summarizing our findings",
    "What guidance covers HVAC and mechanical systems design?",
]


def test_coding_intent_detected_for_coding_questions():
    for question in CODING_QUESTIONS:
        assert llm.question_wants_coding_help(question), f"expected coding intent for: {question}"


def test_coding_intent_not_detected_for_usace_questions():
    for question in NON_CODING_QUESTIONS:
        assert not llm.question_wants_coding_help(question), f"unexpected coding intent for: {question}"


def test_coding_mode_prompt_excludes_compliance_template():
    prompt = llm._build_system_prompt(
        grounded=False, compliance_mode=False, drafting_mode=False, coding_mode=True
    )
    assert "software engineering assistant" in prompt
    assert llm.COMPLIANCE_FORMAT not in prompt
    assert "How to run this" in prompt


def test_compliance_mode_takes_precedence_over_coding_mode():
    prompt = llm._build_system_prompt(
        grounded=False, compliance_mode=True, drafting_mode=False, coding_mode=True
    )
    assert "software engineering assistant" not in prompt
    assert llm.COMPLIANCE_FORMAT in prompt


def test_grounded_coding_mode_mentions_optional_doc_context():
    prompt = llm._build_system_prompt(
        grounded=True, compliance_mode=False, drafting_mode=False, coding_mode=True
    )
    assert "document context was retrieved" in prompt
    assert llm.COMPLIANCE_FORMAT not in prompt
