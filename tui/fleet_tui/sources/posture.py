import os
import json
import re

# MODULE-LEVEL path constants
BACKUP_LOG   = os.path.expanduser("~/.claude/curation/BACKUP_LOG.md")
SUPPLY_LOG   = os.path.expanduser("~/.claude/curation/SUPPLY_CHAIN_LOG.md")
UPSTREAM     = os.path.expanduser("~/.claude/curation/UPSTREAM_UPDATES.md")
BACKUP_ALERT = os.path.expanduser("~/.claude/curation/.backup_alert")
SUPPLY_ALERT = os.path.expanduser("~/.claude/curation/.supply_chain_alert")


def _read(path):
    """Read file text or return "" on any error."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return ""


def _read_json(path):
    """Read JSON dict or return {} on any error."""
    try:
        data = _read(path)
        if not data:
            return {}
        obj = json.loads(data)
        return obj if isinstance(obj, dict) else {}   # a valid non-dict JSON (list/scalar) must not crash .get()
    except Exception:
        return {}


def _parse_backup(text):
    """Parse backup log text and return dict with last, repos_ok, mirror_ok, and abort info."""
    lines = text.strip().split('\n') if text.strip() else []
    
    last = None
    last_repos_ok = None
    last_mirror_ok = None
    last_abort = None
    
    # Process lines in order (oldest to newest)
    for line in lines:
        match = re.match(r'^- (\d{4}-\d\d-\d\d \d\d:\d\d) ([✓⚠]) (.*)$', line)
        if not match:
            continue
            
        ts, glyph, msg = match.groups()
        
        # Determine if this is a success or failure
        is_success = glyph == '✓'
        is_abort = glyph == '⚠' or 'ABORT' in msg
        
        # Update last entry
        last = {
            'ts': ts,
            'ok': is_success,
            'msg': msg
        }
        
        # Track successful repos/mirror pushes
        if is_success:
            if 'repos pushed' in msg:
                last_repos_ok = ts
            if 'mirror pushed' in msg:
                last_mirror_ok = ts
        
        # Track most recent abort
        if is_abort and last_abort is None:
            last_abort = {
                'ts': ts,
                'reason': msg
            }
    
    return {
        'last': last,
        'last_repos_ok': last_repos_ok,
        'last_mirror_ok': last_mirror_ok,
        'last_abort': last_abort
    }


def _parse_supply(text):
    """Parse supply chain log text and return dict with ts, alerts, install_hooks, new_since_last."""
    lines = text.strip().split('\n') if text.strip() else []
    
    # Get the last line that matches the pattern
    last_line = None
    for line in reversed(lines):  # Process from bottom to top
        match = re.search(r'(\d{4}-\d\d-\d\d \d\d:\d\d).*alerts:(\d+).*install-hooks:(\d+).*new-since-last:(\d+)', line)
        if match:
            last_line = match
            break
    
    if not last_line:
        return {
            'ts': None,
            'alerts': 0,
            'install_hooks': 0,
            'new_since_last': 0
        }
    
    ts = last_line.group(1)
    try:
        alerts = int(last_line.group(2))
        install_hooks = int(last_line.group(3))
        new_since_last = int(last_line.group(4))
    except (ValueError, IndexError):
        alerts = 0
        install_hooks = 0
        new_since_last = 0
    
    return {
        'ts': ts,
        'alerts': alerts,
        'install_hooks': install_hooks,
        'new_since_last': new_since_last
    }


def _parse_upstream(text):
    """Parse upstream updates text and return dict with checked, behind, and critical items."""
    if not text.strip():
        return {
            'checked': None,
            'behind': 0,
            'critical': []
        }
    
    # Split into blocks by headers
    blocks = re.split(r'^(## check .+?)$', text, flags=re.MULTILINE)
    
    # Find the last block (most recent)
    last_block_header = None
    last_block_content = ""
    
    for i in range(len(blocks) - 1, -1, -1):
        if blocks[i].startswith("## check"):
            last_block_header = blocks[i]
            if i + 1 < len(blocks):
                last_block_content = blocks[i + 1]
            break
    
    if not last_block_header:
        return {
            'checked': None,
            'behind': 0,
            'critical': []
        }
    
    # Extract timestamp from header
    check_match = re.match(r'^## check (.+?)\s*$', last_block_header)
    checked_ts = check_match.group(1) if check_match else None
    
    # Parse items in the block
    behind_count = 0
    critical_items = []
    
    item_lines = last_block_content.strip().split('\n') if last_block_content.strip() else []
    
    for line in item_lines:
        if not line.startswith('- '):
            continue
            
        # Extract name, local, and latest versions
        name_match = re.match(r'^- (.+?): local `([^`]*)` / latest `([^`]*)`', line)
        if not name_match:
            continue
            
        name = name_match.group(1).strip()
        local = name_match.group(2).strip()
        latest = name_match.group(3).strip()
        
        # Check if it's behind
        is_behind = 'BEHIND' in line
        
        # Check if it's critical
        is_critical = 'CRITICAL' in line
        
        if is_behind:
            behind_count += 1
            
        if is_behind and is_critical:
            critical_items.append({
                'name': name,
                'local': local,
                'latest': latest
            })
    
    return {
        'checked': checked_ts,
        'behind': behind_count,
        'critical': critical_items
    }


def snapshot():
    """Return a composed snapshot dict of backup, supply, and upstream posture."""
    # Read all files safely
    backup_text = _read(BACKUP_LOG)
    supply_text = _read(SUPPLY_LOG)
    upstream_text = _read(UPSTREAM)
    
    backup_alert_data = _read_json(BACKUP_ALERT)
    supply_alert_data = _read_json(SUPPLY_ALERT)
    
    # Parse each component
    backup_parsed = _parse_backup(backup_text)
    supply_parsed = _parse_supply(supply_text)
    upstream_parsed = _parse_upstream(upstream_text)
    
    # Add alert_pending flags
    backup_parsed['alert_pending'] = bool(backup_alert_data.get('pending'))
    supply_parsed['alert_pending'] = bool(supply_alert_data.get('pending'))
    
    return {
        'backup': backup_parsed,
        'supply': supply_parsed,
        'upstream': upstream_parsed
    }
