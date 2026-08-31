#!/usr/bin/env python3

"""
Fleet control-plane CLI.
"""

import argparse
import json
import os
import sys
import traceback
from typing import List, Dict, Any

from fleet_tui.sources import jobs, health, dispatch, ops, cloud_legs, ratings, targets, network, modelstate


def handle_error(message: str) -> None:
    """Print error message to stderr and exit with code 2."""
    print(f"fleet: degraded — {message}", file=sys.stderr)
    sys.exit(2)


def fleet_presets(args) -> None:
    """Handle 'fleet presets' command."""
    try:
        from fleet_tui.fleet_cli import presets
        
        if args.action == "list" or args.action is None:
            presets_data = presets.load_presets()
            if args.json:
                print(json.dumps(presets_data, default=str))
            else:
                print("=== Fleet Presets ===")
                for name, preset in presets_data.items():
                    print(f"{name}:")
                    print(f"  cmd: {preset['cmd']}")
                    print(f"  desc: {preset['desc']}")
                    print("")
        elif args.action == "run":
            if not args.preset_name:
                handle_error("preset name is required for 'fleet presets run'")
            presets_data = presets.load_presets()
            if args.preset_name not in presets_data:
                handle_error(f"unknown preset: {args.preset_name}")
            
            preset = presets_data[args.preset_name]
            full_brief = preset["prefix"] + " ".join(args.brief)
            dispatched = dispatch.submit(preset["cmd"], full_brief, label=args.preset_name)
            if args.json:
                print(json.dumps({
                    "preset": args.preset_name,
                    "cmd": preset["cmd"],
                    "dispatch": dispatched
                }, default=str))
            else:
                if dispatched:
                    print(f"dispatched: {dispatched}")
                else:
                    handle_error("dispatch submission failed")
    except Exception as e:
        handle_error(f"Failed to handle presets command: {str(e)}")


def fleet_research(args) -> None:
    """Handle 'fleet research' command."""
    try:
        from fleet_tui.fleet_cli import research
        
        if args.action == "list":
            # For now, just say list is not implemented
            # This could be implemented later to check existing research runs if needed 
            handle_error("list action not implemented for research")
        else:
            # Run research with slug (required) and question (joined)
            result = research.launch_research(args.slug, " ".join(args.question))
            
            if args.json:
                print(json.dumps(result, default=str))
            else:
                print(f"briefed → 2 legs launched (grok + codex)")
                print(f"  brief path: {result['brief_path']}")
                print(f"  grok dispatch: {result['grok_dispatch']}")
                print(f"  codex dispatch: {result['codex_dispatch']}")
                print("next: reconcile-dispatch once both legs return")
    except Exception as e:
        handle_error(f"Failed to handle research command: {str(e)}")


def fleet_feedback(args) -> None:
    """Handle 'fleet feedback' command."""
    try:
        if args.action == "list" or args.action is None:
            # List open feedback debts
            debts = dispatch.open_feedback_debts()
            if args.json:
                print(json.dumps(debts, default=str))
            else:
                print(f"{len(debts)} feedback due:")
                for debt in debts:
                    status = "ready" if debt["output_ready"] else "running"
                    brief_snippet = debt["brief"][:117] + "..." if len(debt["brief"]) > 120 else debt["brief"]
                    print(f"  {debt['base']}  {debt['leg']}  [{status}]  {brief_snippet}")
        elif args.action == "close":
            # Close a feedback debt
            if not args.note:
                handle_error("note is required for 'fleet feedback close'")
            
            # Append to the ledger file
            ledger_path = os.path.expanduser("~/.claude/curation/FEEDBACK_LOOP_LEDGER.md")
            os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
            
            with open(ledger_path, "a") as f:
                # Get the leg from debt or fallback to unknown
                debt_leg = "?"
                debts = dispatch.open_feedback_debts()
                for debt in debts:
                    if debt["base"] == args.id:
                        debt_leg = debt["leg"]
                        break
                f.write(f"- [{args.id}] ({debt_leg}): {args.note}\\n")
            
            # Close the feedback debt  
            success = dispatch.close_feedback(args.id)
            if not success:
                handle_error("no such open feedback debt")
            
            # Log the feedback event
            try:
                from fleet_tui.fleet_cli import events
                events.log_event("feedback", {"id": args.id, "note": args.note})
            except Exception:
                # Never fails - logging must not break feedback
                pass
                
            print(f"feedback closed: {args.id}")
    except Exception as e:
        handle_error(f"Failed to handle feedback command: {str(e)}")


def fleet_status(args) -> None:
    """Handle 'fleet status' command."""
    try:
        # Get health snapshot
        health_snapshot = health.snapshot()
        
        # Count running jobs
        all_jobs = jobs.list_jobs()
        running_jobs = [j for j in all_jobs if j.running]
        
        # Get cloud legs
        recent_dispatches = dispatch.recent()
        active_cloud_legs = cloud_legs.cloud_snapshot(recent_dispatches)
        
        # Build output structure
        result = {
            "services": health_snapshot.services,
            "loaded_models": [
                {"name": m.name, "gb": m.gb} for m in health_snapshot.loaded
            ],
            "gpu": [
                {
                    "used": g.get("used", 0),
                    "total": g.get("total", 0),
                    "temp": g.get("temp", 0),
                    "util": g.get("util", 0)
                } for g in health_snapshot.gpu
            ],
            "cpu_temp": health_snapshot.cpu_temp,
            "ssd_temp": health_snapshot.ssd_temp,
            "uptime": health_snapshot.uptime,
            "disk_free_gb": health_snapshot.disk_free_gb,
            "disk_total_gb": health_snapshot.disk_total_gb,
            "running_jobs_count": len(running_jobs),
            "active_cloud_legs_count": len(active_cloud_legs)
        }
        
        if args.json:
            print(json.dumps(result, default=str))
        else:
            # Print human-readable status
            print("=== Fleet Status ===")
            print(f"Services: {', '.join([f'{k}: {v}' for k, v in health_snapshot.services.items()])}")
            print("Loaded models:")
            for m in health_snapshot.loaded:
                print(f"  {m.name} ({m.gb} GB)")
            print("GPU usage:")
            for i, g in enumerate(health_snapshot.gpu):
                used = g.get("used", 0)
                total = g.get("total", 0)
                temp = g.get("temp", 0)
                util = g.get("util", 0)
                print(f"  Card {i}: {used}/{total} MiB, {temp}°C, {util}%")
            print(f"CPU Temp: {health_snapshot.cpu_temp}°C")
            print(f"SSD Temp: {health_snapshot.ssd_temp}°C")
            print(f"Uptime: {health_snapshot.uptime}")
            print(f"Disk: {health_snapshot.disk_free_gb:.1f}/{health_snapshot.disk_total_gb:.1f} GB free")
            print(f"Running jobs: {len(running_jobs)}")
            print(f"Active cloud legs: {len(active_cloud_legs)}")
            
    except Exception as e:
        handle_error(f"Failed to get status: {str(e)}")


def fleet_targets(args) -> None:
    """Handle 'fleet targets' command."""
    try:
        all_targets = targets.all_targets()
        groups = targets.list_groups()
        
        # Handle ranking if --ranked flag is set
        if args.ranked:
            ratings_summary = ratings.summary()
            
            # Create a ranked list based on win rate or other metrics
            ranked_targets = []
            for target in all_targets:
                target_id = target.get('id') if isinstance(target, dict) else str(target)
                rating_data = ratings_summary.get(target_id, {})
                
                wins = rating_data.get('wins', 0)
                losses = rating_data.get('losses', 0)
                total = wins + losses
                
                win_rate = (wins / total) if total > 0 else 0
                ranked_targets.append((target, win_rate))
            
            # Sort targets by win rate descending
            sorted_targets = sorted(ranked_targets, key=lambda x: x[1], reverse=True)
            
            result = {
                "targets": [t[0] for t in sorted_targets],
                "groups": groups,
                "ranked": True
            }
        else:
            result = {
                "targets": all_targets,
                "groups": groups
            }
        
        if args.json:
            print(json.dumps(result, default=str))
        else:
            # Print human-readable targets
            print("=== Fleet Targets ===")
            for group in groups:
                gname = group.get('name', '?') if isinstance(group, dict) else str(group)
                print(f"\n{gname}")
                tgls = group.get('targets', []) if isinstance(group, dict) else []
                for tgt in tgls:
                    tid = tgt['id'] if isinstance(tgt, dict) else str(tgt)
                    desc = tgt.get('desc', '') if isinstance(tgt, dict) else ''
                    cloud_marker = " ☁" if cloud_legs.is_cloud_leg(tid) else ""
                    
                    # Add rank information if ranked mode
                    rating_data = ratings.summary().get(tid, {})
                    wins = rating_data.get('wins', 0)
                    losses = rating_data.get('losses', 0)
                    total = wins + losses
                    
                    if args.ranked:
                        win_rate = (wins / total) if total > 0 else 0
                        print(f"  {tid}{cloud_marker} — {desc}   [👍{wins} 👎{losses}]")
                    else:
                        print(f"  {tid}{cloud_marker} — {desc}")
            
    except Exception as e:
        handle_error(f"Failed to get targets: {str(e)}")


def fleet_tail(args) -> None:
    """Handle 'fleet tail' command."""
    try:
        output = dispatch.full_output(args.id)
        if not output or (output.get("text", "").strip() == "" or output.get("text") == "(no output yet)"):
            handle_error(f"No output found for dispatch '{args.id}'")
            
        out_lines = output.get("out", "").split("\n") if isinstance(output.get("out"), str) else []
        log_lines = output.get("log", "").split("\n") if isinstance(output.get("log"), str) else []
        
        # Get last N lines
        tail_lines = out_lines[-args.n:] + log_lines[-args.n:]
        tail_lines = [line for line in tail_lines if line.strip()]

        result = {
            "id": args.id,
            "out_tail": out_lines[-args.n:],
            "log_tail": log_lines[-args.n:]
        }
        
        if args.json:
            print(json.dumps(result, default=str))
        else:
            # Print human-readable tail
            print(f"=== Tail of dispatch '{args.id}' (last {args.n} lines) ===")
            for line in tail_lines:
                print(line)
            
    except Exception as e:
        handle_error(f"Failed to get tail: {str(e)}")


def fleet_route(args) -> None:
    """Handle 'fleet route' command."""
    try:
        task = " ".join(args.task).lower()
        
        # Keyword matching table (case-insensitive, first match wins)
        routing_table = [
            (r"audit|review", "qwen3.6:35b-a3b — best code auditor"),
            (r"generate|write code|implement", "qwen3-coder:30b — best generator"),
            (r"agentic|refactor|repo", "Ornith-1.0-35B — dedicated agentic coder"),
            (r"tool|chain", "GLM-4.7-Flash:8090 — best tool-caller"),
            (r"research|web|survey", "grok-research + codex-fleet — divergent/convergent pair"),
            (r"reconcile", "the 3-way rotation (reconcile-dispatch)"),
            (r"reason|think", "Ornith-1.0-35B — reasoning"),
            (r"simple|cheap|condense|summarize", "gemma4:12b")
        ]
        
        # Find first matching route
        target = None
        rationale = None
        
        for pattern, route in routing_table:
            # Split pattern and check if any of the alternatives match
            patterns = pattern.split('|')
            if any(patt in task for patt in patterns):
                parts = route.split(' — ')
                if len(parts) >= 2:
                    target = parts[0]
                    rationale = ' — '.join(parts[1:])
                break
        
        if not target:
            target = "gemma4:12b"
            rationale = "cheapest capable"
        
        result = {
            "task": task,
            "target": target,
            "rationale": rationale
        }
        
        if args.json:
            print(json.dumps(result, default=str))
        else:
            print(f"route: {target}  —  {rationale}")
            
    except Exception as e:
        handle_error(f"Failed to get route: {str(e)}")


def fleet_digest(args):
    """fleet digest [--send] — gather 24h fleet state, condense, print (or push to Telegram)."""
    try:
        from fleet_tui.fleet_cli import digest
        result = digest.run_digest(send=args.send)
    except Exception as e:
        handle_error(f"Failed digest: {str(e)}")
        return
    if args.json:
        print(json.dumps(result, indent=2))
        return
    if not result.get("ok"):
        print(f"digest: {result.get('reason', 'failed')}")
        return
    print(result.get("text", ""))
    print("(sent to telegram)" if result.get("sent") else "(dry-run — pass --send to push)")


def fleet_summarize(args):
    """fleet summarize <id> — condense a dispatch output into .summary.md + .actions.json sidecars."""
    try:
        from fleet_tui.fleet_cli import summarize
        result = summarize.summarize_dispatch(args.id)
    except Exception as e:
        handle_error(f"Failed summarize: {str(e)}")
        return
    if args.json:
        print(json.dumps(result, indent=2))
        return
    if not result.get("ok"):
        print(f"summarize: {result.get('reason', 'failed')}")
        return
    if result.get("skipped"):
        print(f"skipped: {result['skipped']} (base={args.id})")
        return
    print(f"summarized {args.id} [{result.get('status', '')}]")
    print(f"  summary → {result.get('summary_path')}")
    print(f"  actions → {result.get('actions_path')}")


def fleet_preflight(args):
    """fleet preflight <target> <brief> — gate a dispatch before launch.

    Exit 1 if any BLOCK fired (don't dispatch), else 0. Warns are advisory.
    """
    try:
        from fleet_tui.fleet_cli import preflight as preflight_mod
        r = preflight_mod.preflight(args.target, args.brief)
    except Exception as e:
        handle_error(f"Failed preflight: {str(e)}")
        return
    blocks = r.get("blocks") or []
    warnings = r.get("warnings") or []
    if args.json:
        print(json.dumps(r, indent=2))
    else:
        if blocks:
            print("✗ BLOCK:")
            for b in blocks:
                print(f"  - {b}")
        if warnings:
            print("⚠ warn:")
            for w in warnings:
                print(f"  - {w}")
        if not blocks and not warnings:
            print("✓ preflight clear")
    sys.exit(1 if blocks else 0)


def fleet_context(args) -> None:
    """Handle 'fleet context' command."""
    try:
        from fleet_tui.sources import health, jobs, dispatch, modelstate, cloud_legs
        # Get health snapshot
        health_snapshot = health.snapshot()
        
        # Count running jobs
        all_jobs = jobs.list_jobs()
        running_jobs = [j for j in all_jobs if j.running]
        
        # Get cloud legs
        recent_dispatches = dispatch.recent()
        active_cloud_legs = cloud_legs.cloud_snapshot(recent_dispatches)
        
        # Build output structure
        result = {
            "services": health_snapshot.services,
            "loaded_models": [
                {"name": m.name, "gb": m.gb} for m in health_snapshot.loaded
            ],
            "gpu": [
                {
                    "used": g.get("used", 0),
                    "total": g.get("total", 0),
                    "temp": g.get("temp", 0),
                    "util": g.get("util", 0)
                } for g in health_snapshot.gpu
            ],
            "cpu_temp": health_snapshot.cpu_temp,
            "ssd_temp": health_snapshot.ssd_temp,
            "uptime": health_snapshot.uptime,
            "disk_free_gb": health_snapshot.disk_free_gb,
            "disk_total_gb": health_snapshot.disk_total_gb,
            "running_jobs_count": len(running_jobs),
            "active_cloud_legs_count": len(active_cloud_legs)
        }
        
        if args.json:
            print(json.dumps(result, default=str))
        else:
            # Print human-readable context
            print("## Fleet context")
            print("")
            print("### Services")
            for service, is_active in health_snapshot.services.items():
                status = "✅" if is_active else "❌"
                print(f"- {status} {service}")
            
            print("")
            print("### Loaded models")
            if health_snapshot.loaded:
                for m in health_snapshot.loaded:
                    print(f"- {m.name} ({m.gb} GB)")
            else:
                print("- None")
                
            print("")
            print("### GPU usage")
            if health_snapshot.gpu:
                for i, g in enumerate(health_snapshot.gpu):
                    used = g.get("used", 0)
                    total = g.get("total", 0)
                    temp = g.get("temp", 0)
                    util = g.get("util", 0)
                    print(f"- Card {i}: {used}/{total} MiB, {temp}°C, {util}%")
            else:
                print("- None")
                
            print("")
            print("### System info")
            print(f"- CPU Temp: {health_snapshot.cpu_temp}°C")
            print(f"- SSD Temp: {health_snapshot.ssd_temp}°C")
            print(f"- Uptime: {health_snapshot.uptime}")
            print(f"- Disk: {health_snapshot.disk_free_gb:.1f}/{health_snapshot.disk_total_gb:.1f} GB free")
            
            print("")
            print("### Jobs & Cloud")
            print(f"- Running jobs: {len(running_jobs)}")
            print(f"- Active cloud legs: {len(active_cloud_legs)}")
            
    except Exception as e:
        handle_error(f"Failed to get context: {str(e)}")


def fleet_mode(args) -> None:
    """Handle 'fleet mode' command."""
    try:
        from fleet_tui.fleet_cli.modes import get_mode, set_mode
        
        if not args.name:
            # Print current mode
            mode = get_mode()
            if args.json:
                print(json.dumps({"mode": mode}, default=str))
            else:
                print(mode)
        else:
            # Set new mode
            if set_mode(args.name):
                if args.json:
                    print(json.dumps({"mode": args.name}, default=str))
                else:
                    print(f"mode -> {args.name}")
            else:
                handle_error(f"Invalid mode: {args.name}")
    except Exception as e:
        handle_error(f"Failed to handle mode command: {str(e)}")


def fleet_log(args) -> None:
    """Handle 'fleet log' command."""
    try:
        from fleet_tui.fleet_cli import events
        
        event_list = events.read_events(limit=args.n, kind=args.kind)
        
        if args.json:
            print(json.dumps(event_list, default=str))
            return
            
        if not event_list:
            print("0 events:")
            return
            
        print(f"{len(event_list)} events:")
        for event in event_list:
            ts = event.get("ts", 0)
            kind = event.get("kind", "unknown")
            data = event.get("data", {})
            
            # Format timestamp to ISO
            import datetime
            dt = datetime.datetime.fromtimestamp(ts)
            iso_time = dt.isoformat()
            
            # Create compact summary 
            if kind == "dispatch":
                summary = f"cmd={data.get('cmd', 'unknown')}, label={data.get('label', 'unknown')}"
            elif kind == "feedback":
                summary = f"id={data.get('id', 'unknown')}, note={data.get('note', 'no note')[:50]}..."
            else:
                # Generic summary for any other event type
                summary = ", ".join(f"{k}={v}" for k, v in data.items())[:80] + "..." if len(str(data)) > 80 else str(data)
                
            print(f"  {iso_time}  [{kind}]  {summary}")
            
    except Exception as e:
        handle_error(f"Failed to read events: {str(e)}")


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="fleet",
        description="Fleet control-plane CLI"
    )
    
    # Create subparsers for each command
    subparsers = parser.add_subparsers(dest='verb', help='Available commands')
    
    # Status command
    status_parser = subparsers.add_parser('status', help='Show fleet health status')
    status_parser.add_argument('--json', action='store_true', help='Output JSON format')
    
    # Targets command
    targets_parser = subparsers.add_parser('targets', help='List dispatch targets')
    targets_parser.add_argument('--json', action='store_true', help='Output JSON format')
    targets_parser.add_argument('--ranked', action='store_true', help='Show ranked list by win rate')
    
    # Tail command
    tail_parser = subparsers.add_parser('tail', help='Show output tail of a dispatch')
    tail_parser.add_argument('id', help='Dispatch ID')
    tail_parser.add_argument('-n', type=int, default=20, help='Number of lines (default: 20)')
    tail_parser.add_argument('--json', action='store_true', help='Output JSON format')
    
    # Route command
    route_parser = subparsers.add_parser('route', help='Get recommended target for a task')
    route_parser.add_argument('task', nargs='+', help='Task description')
    route_parser.add_argument('--json', action='store_true', help='Output JSON format')
    
    # Feedback command
    feedback_parser = subparsers.add_parser('feedback', help='Manage feedback debts')
    feedback_parser.add_argument('action', nargs='?', default='list', choices=['list', 'close'], 
                                help='Feedback action (default: list)')
    feedback_parser.add_argument('id', nargs='?', help='Dispatch ID to close feedback for')
    feedback_parser.add_argument('--note', help='Note explaining why the dispatch was closed')
    feedback_parser.add_argument('--json', action='store_true', help='Output JSON format')
    
    # preflight command
    preflight_parser = subparsers.add_parser('preflight', help='Pre-flight gate a dispatch before launch')
    preflight_parser.add_argument('target', help='Dispatch target id or allowed command')
    preflight_parser.add_argument('brief', help='Path to the brief file')
    preflight_parser.add_argument('--json', action='store_true', help='Output JSON format')
    
    # summarize command
    summarize_parser = subparsers.add_parser('summarize', help='Summarize a dispatch output into sidecars')
    summarize_parser.add_argument('id', help='Dispatch base id')
    summarize_parser.add_argument('--json', action='store_true', help='Output JSON format')
    
    # digest command
    digest_parser = subparsers.add_parser('digest', help='Gather + condense the 24h fleet digest (Telegram with --send)')
    digest_parser.add_argument('--send', action='store_true', help='Push to Telegram (default: dry-run print)')
    digest_parser.add_argument('--json', action='store_true', help='Output JSON format')
    
    # context command
    context_parser = subparsers.add_parser('context', help='Show current fleet state')
    context_parser.add_argument('--json', action='store_true', help='Output JSON format')
    
    # mode command
    mode_parser = subparsers.add_parser('mode', help='Set/get fleet mode')
    mode_parser.add_argument('name', nargs='?', help='Mode name (get if omitted)')
    mode_parser.add_argument('--json', action='store_true', help='Output JSON format')
    
    # presets command
    presets_parser = subparsers.add_parser('presets', help='Manage and run preset dispatches')
    presets_parser.add_argument('action', nargs='?', default='list', choices=['list', 'run'], 
                               help='Preset action (default: list)')
    presets_parser.add_argument('preset_name', nargs='?', help='Name of the preset to run')
    presets_parser.add_argument('brief', nargs='*', help='Brief for the preset')
    presets_parser.add_argument('--json', action='store_true', help='Output JSON format')
    
    # research command
    research_parser = subparsers.add_parser('research', help='Launch a new research pipeline')
    research_parser.add_argument('action', nargs='?', default='run', choices=['run'], 
                                 help='Research action (default: run)')
    research_parser.add_argument('slug', help='Slug for the research pipeline')
    research_parser.add_argument('question', nargs='+', help='Question/ brief for the research')
    research_parser.add_argument('--json', action='store_true', help='Output JSON format')
    
    # log command
    log_parser = subparsers.add_parser('log', help='Show fleet event log')
    log_parser.add_argument('--kind', help='Filter by event kind')
    log_parser.add_argument('-n', type=int, default=30, help='Number of events to show (default: 30)')
    log_parser.add_argument('--json', action='store_true', help='Output JSON format')

    # postmortem command
    postmortem_parser = subparsers.add_parser('postmortem', help='Analyze dispatch output')
    postmortem_parser.add_argument('id', help='Dispatch ID')
    postmortem_parser.add_argument('--json', action='store_true', help='Output JSON format')
    
    # Parse arguments
    args = parser.parse_args()
    
    if not args.verb:
        # No verb provided - show usage
        parser.print_help()
        sys.exit(2)
    
    try:
        if args.verb == 'status':
            fleet_status(args)
        elif args.verb == 'targets':
            fleet_targets(args)
        elif args.verb == 'tail':
            fleet_tail(args)
        elif args.verb == 'route':
            fleet_route(args)
        elif args.verb == 'feedback':
            fleet_feedback(args)
        elif args.verb == 'preflight':
            fleet_preflight(args)
        elif args.verb == 'summarize':
            fleet_summarize(args)
        elif args.verb == 'digest':
            fleet_digest(args)
        elif args.verb == 'context':
            fleet_context(args)
        elif args.verb == 'mode':
            fleet_mode(args)
        elif args.verb == 'presets':
            fleet_presets(args)
        elif args.verb == 'research':
            fleet_research(args)
        elif args.verb == 'log':
            fleet_log(args)
        elif args.verb == 'postmortem':
            try:
                from fleet_tui.fleet_cli import postmortem
                result = postmortem.postmortem(args.id)
                if args.json:
                    print(json.dumps(result, default=str))
                else:
                    if not result.get("ok", True):
                        handle_error(result.get("reason", "Unknown error"))
                    else:
                        print(f"=== Postmortem for {result['id']} ===")
                        print(f"Running: {'Yes' if result['running'] else 'No'}")
                        print(f"Class: {result['classification']['class']}")
                        print(f"Reason: {result['classification']['reason']}")
                        print(f"Suggested next: {result['classification']['suggested_next']}")
                        print("\nOutput tail:")
                        print(result['output_tail'])
            except Exception as e:
                handle_error(f"Failed to run postmortem: {str(e)}")
    except Exception as e:
        handle_error(f"Failed to execute command: {str(e)}")


if __name__ == "__main__":
    main()