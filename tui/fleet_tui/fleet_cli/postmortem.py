"""
Helper module for fleet postmortem analysis.
"""

import re
from fleet_tui.sources import dispatch


def classify_failure(text: str) -> dict:
    """
    Classify a dispatch log/output text into a known failure class.
    
    Args:
        text (str): The output text to analyze
        
    Returns:
        dict: Classification result with keys 'class', 'reason', and 'suggested_next'
    """
    if not text:
        return {
            "class": "no_output",
            "reason": "No output available",
            "suggested_next": "Check if the dispatch was started correctly"
        }
        
    # Normalize text for matching
    lower_text = text.lower()
    
    # Check for specific failure patterns (first match wins)
    if "(no output yet)" in text:
        return {
            "class": "no_output",
            "reason": "No output yet available",
            "suggested_next": "Wait for the dispatch to complete or check if it started properly"
        }
        
    # Model-related failures
    if any(pattern in lower_text for pattern in ["model not found", "pull the model", "404", "not found"]):
        return {
            "class": "model_missing",
            "reason": "Requested model is missing or not found",
            "suggested_next": "Run 'ollama pull <model_name>' or use a different alias"
        }
        
    # Memory failures
    if any(pattern in lower_text for pattern in ["out of memory", "oom", "cuda out of memory", "insufficient", "memory error"]):
        return {
            "class": "oom",
            "reason": "Out of memory / VRAM issue",
            "suggested_next": "Try with a smaller model, reduce batch size, or free up VRAM"
        }
        
    # Busy failures
    if any(pattern in lower_text for pattern in ["busy", "loading", "try again", "currently loading", "already running"]):
        return {
            "class": "busy",
            "reason": "Target is busy or loading",
            "suggested_next": "Try again later, or select a different target"
        }
        
    # Authorization failures
    if any(pattern in lower_text for pattern in ["auth", "unauthorized", "401", "token", "access denied"]):
        return {
            "class": "hermes_auth",
            "reason": "Authorization or authentication failure",
            "suggested_next": "Check credentials and token permissions"
        }
        
    # Secret guard failures (assumed to be a string pattern for now)
    if "secret" in lower_text and ("blocked" in lower_text or "dispatch_secret_guard" in text):
        return {
            "class": "secret_guard",
            "reason": "Secret access blocked by security guard",
            "suggested_next": "Check secret permissions or bypass guard mechanism"
        }
        
    # Timeout failures
    if any(pattern in lower_text for pattern in ["timed out", "timeout"]):
        return {
            "class": "timeout",
            "reason": "Operation timed out",
            "suggested_next": "Increase timeout, check connectivity, or retry"
        }
        
    # Success but no deliverable (refusal cases)
    if any(pattern in lower_text for pattern in ["i cannot", "cannot", "i refuse", "missing input", "need more context"]):
        return {
            "class": "success_no_deliverable",
            "reason": "Completed execution but output is a refusal or non-deliverable",
            "suggested_next": "Check prompt for completeness or adjust parameters"
        }
        
    # Default case
    return {
        "class": "unknown",
        "reason": "Unknown failure type",
        "suggested_next": "Analyze the full output manually to determine cause"
    }


def postmortem(base_name: str) -> dict:
    """
    Generate a postmortem analysis for a dispatch.
    
    Args:
        base_name (str): The base name of the dispatch
        
    Returns:
        dict: Postmortem data including id, running status, snippet, tail, and classification
    """
    # Get full output
    full_output = dispatch.full_output(base_name)
    
    if not full_output:
        return {"ok": False, "reason": "no such dispatch"}
        
    output_text = full_output.get("text", "").strip()
    
    # Build result
    result = {
        "id": base_name,
        "running": full_output.get("running", False),
        "brief_snippet": output_text[:200],
        "output_tail": output_text[-800:] if len(output_text) > 800 else output_text,
        "classification": classify_failure(output_text)
    }
    
    return result

