"""Record contracts between sources/ (readers) and widgets/ (renderers).

FROZEN interface (2026-07-02): do NOT change a field without updating every source AND widget that
uses it. Plain dataclasses, no external deps — importable in a headless test with nothing installed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict


@dataclass
class Job:
    """One background unit: a Hermes cron, a system crontab line, a harness bg job, or a research leg."""
    id: str
    name: str
    kind: str = "cron"          # cron | systemcron | bgjob | leg
    schedule: str = ""          # human display, e.g. "every 180m" or "0 */4 * * *"
    next_run: str = ""          # iso or ""
    last_run: str = ""          # iso or ""
    last_status: str = "unknown"  # ok | fail | unknown
    last_line: str = ""         # tail of last output/log (ANSI-stripped, one line)
    running: bool = False       # actively firing right now (Hermes state/fire_claim)
    command: str = ""           # full crontab command (systemcron only) — enables manual run-now of a raw cron


@dataclass
class InboxItem:
    """One pending actionable or gate. Sources emit ONLY pending/non-empty/unreviewed items."""
    source: str                 # dep | curation | github | hf | rejects
    title: str
    age: str = ""               # human, e.g. "3h"
    priority: str = "normal"    # crit | normal | fyi
    pending: bool = True
    detail: str = ""            # one-line summary (shown in the panel row)
    body: str = ""              # full multi-line detail (shown in the click-through modal)


@dataclass
class OpsItem:
    """One row in the Ops master-detail view: a loop/cron job, a dispatch, a research item, or a generic job — normalized for display."""
    id: str
    kind: str            # 'loop' | 'dispatch' | 'research' | 'job'
    title: str
    status: str           # 'running' | 'idle' | 'ok' | 'fail' | 'scheduled'
    last_run: float = None    # epoch seconds, or None if unknown/never run
    detail: str = ""
    source_ref: object = None


@dataclass
class LoadedModel:
    name: str
    gb: float = 0.0


@dataclass
class HealthSnapshot:
    services: Dict[str, bool] = field(default_factory=dict)      # name -> up?
    loaded: List[LoadedModel] = field(default_factory=list)      # models in VRAM now
    vram_note: str = ""                                          # e.g. "1 big model spans both cards"
    critical_caps: List[Dict] = field(default_factory=list)      # [{"cap","ok","detail"}]
    reliability_tail: str = ""                                   # short tail of FLEET_HEALTH_latest.txt
    gpu: List[Dict] = field(default_factory=list)                # per-card [{"used","total","temp"}] (MiB, °C)
    cpu_temp: int = 0                                            # CPU °C (k10temp Tctl); 0 = unknown
    cpu_util: int = None                                         # CPU busy % (from /proc/stat delta); None = unknown
    ssd_temp: int = 0                                            # internal NVMe SSD °C (hwmon Composite); 0 = unknown
    ssd_ext_temp: int = 0                                        # external USB-NVMe SSD °C (via root-cron file); 0 = unknown
    uptime: str = ""                                            # system uptime, e.g. "3d 4h"
    xid: str = "none"                                          # last GPU Xid/hang error from the forensics log ("none" = clean)
    disk_free_gb: float = 0.0                                   # free space on the model/home partition (GB); 0 = unknown
    disk_total_gb: float = 0.0                                  # total space on that partition (GB); 0 = unknown
    ram_used_gb: float = 0.0                                    # RAM used in GiB
    ram_total_gb: float = 0.0                                   # RAM total in GiB
    ram_pct: int = 0                                            # RAM usage percentage
    swap_used_gb: float = 0.0                                   # Swap used in GiB
    swap_total_gb: float = 0.0                                  # Swap total in GiB
    swap_pct: int = 0                                           # Swap usage percentage


@dataclass
class ModelState:
    """One fleet model's runtime state — loaded in VRAM (running) vs cold on disk (idle)."""
    name: str
    loaded: bool = False        # in VRAM right now?
    gb: float = 0.0             # VRAM footprint while loaded
    idle_in: str = ""           # human time until ollama idle-unloads it (e.g. "4m"); "" if not loaded
    busy: bool = False          # in-flight: loaded AND the GPU is actively computing
    device: str = ""            # configured device key, if supplied by a box relay
    port: int = 0                # sidecar port; zero for ordinary model runners
    state: str = ""             # busy | idle | asleep | down for sidecars
    wake_on_use: bool = False


@dataclass
class InstalledModel:
    """One model pulled to DISK (from ollama /api/tags) — the inventory view, independent of load state."""
    name: str
    size_gb: float = 0.0        # on-disk footprint (GB)
    family: str = ""            # model family, e.g. "qwen3"
    quant: str = ""             # quantization level, e.g. "Q4_K_M"
    param_size: str = ""        # parameter size, e.g. "30.5B"
    modified: str = ""          # last-modified date (YYYY-MM-DD); "" if unknown


@dataclass
class FocusState:
    on: bool = False
    since: str = ""
    scope: str = "noisy"        # noisy | all
    by: str = ""


@dataclass
class Playlist:
    """A research playlist (video-research pipeline source queue). Immutable record read from
    ~/.fleet_tui/research_playlists.json; last_checked/last_result come from the sidecar state file."""
    name: str
    url: str = ""
    kind: str = "youtube"
    last_checked: str = ""   # ISO ts of the last owner-requested check, "" if never
    last_result: str = ""    # short summary of the last stage pass, "" if none


@dataclass(frozen=True)
class DeviceLabel:
    """A user-owned display label for one device key."""
    badge: str = ""
    color: str = ""
    power_cap_w: float = 0.0


@dataclass(frozen=True)
class FleetBox:
    """One configured machine. Paths are local or mounted relay paths, never shell targets."""
    name: str = "local"
    kind: str = "local"
    receipts_path: str = ""
    models_path: str = ""
    health_path: str = ""
    ledger_path: str = ""
    downloads_path: str = ""
    throughput_path: str = ""
    device_labels: Dict[str, DeviceLabel] = field(default_factory=dict)


@dataclass
class ReceiptRow:
    """One artifact receipt recorded by a box-local or relayed JSONL ledger."""
    name: str
    box: str = "local"
    model: str = ""
    rc: str = ""
    bytes: int = 0
    ts: str = ""
    status: str = "unknown"


@dataclass
class ThroughputRow:
    """A measured serving rate; absence means no rate is rendered."""
    model: str
    tok_s: float = 0.0
    box: str = "local"
    ts: str = ""


@dataclass
class DownloadRow:
    """One acquisition-ledger entry attributed to an explicitly named box."""
    file: str
    box: str = "local"
    source: str = ""
    agent: str = ""
    kind: str = ""
    ts: int = 0


@dataclass
class LaneState:
    """Admission state for one lane, including per-box contributions."""
    lane: str
    live: int = 0
    admits_by_box: Dict[str, int] = field(default_factory=dict)
