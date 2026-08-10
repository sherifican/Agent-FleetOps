import argparse
import json
import os
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any


@dataclass(frozen=True)
class StageRecord:
    stage: str
    items_in: int
    items_out: int
    dropped: dict          # reason -> count; reasons are free-form strings
    run_id: str

    @property
    def accounted(self) -> int:      # items_out + sum(dropped.values())
        return self.items_out + sum(self.dropped.values())

    @property
    def closes(self) -> bool:        # items_in == accounted
        return self.items_in == self.accounted


class Ledger:
    def __init__(self, path: str, run_id: str, declared_stages: list[str]):
        self.path = path
        self.run_id = run_id
        self.declared_stages = declared_stages
        # Ensure the file exists
        if not os.path.exists(path):
            with open(path, 'w') as f:
                pass

    def record(self, stage, items_in, items_out, dropped=None) -> StageRecord:
        if dropped is None:
            dropped = {}
        
        # Just write the record without validation - all validation moves to check()
        seq = 0
        try:
            with open(self.path, 'r') as f:
                lines = f.readlines()
                seq = len(lines)
        except FileNotFoundError:
            pass

        record = StageRecord(stage, items_in, items_out, dropped, self.run_id)
        data = {
            'stage': stage,
            'items_in': items_in,
            'items_out': items_out,
            'dropped': dropped,
            'run_id': self.run_id,
            'seq': seq
        }
        
        with open(self.path, 'a') as f:
            f.write(json.dumps(data) + '\n')
            
        return record

    def records(self) -> List[StageRecord]:
        result = []
        try:
            with open(self.path, 'r') as f:
                for line_num, line in enumerate(f, 1):
                    if line.strip():
                        try:
                            data = json.loads(line)
                            # Skip lines that don't have a dict structure
                            if not isinstance(data, dict):
                                continue
                            if data.get('run_id') == self.run_id:
                                record = StageRecord(
                                    stage=data['stage'],
                                    items_in=data['items_in'],
                                    items_out=data['items_out'],
                                    dropped=data['dropped'],
                                    run_id=data['run_id']
                                )
                                result.append(record)
                        except (json.JSONDecodeError, KeyError):
                            # Skip malformed lines
                            pass
        except FileNotFoundError:
            pass
        return result

    def _read_raw_lines_with_errors(self) -> Tuple[List[Dict], List[str]]:
        """Read raw file lines and return (parsed_records, parse_errors).
        
        parse_errors contains strings with 1-based line numbers of unparseable lines.
        """
        records = []
        errors = []
        
        try:
            with open(self.path, 'r') as f:
                for line_num, line in enumerate(f, 1):
                    if line.strip():
                        try:
                            data = json.loads(line)
                            # Only include dict structures
                            if isinstance(data, dict):
                                records.append(data)
                            else:
                                errors.append(f"ledger line {line_num}: non-dict JSON object")
                        except json.JSONDecodeError as e:
                            errors.append(f"ledger line {line_num}: unparseable JSON - {str(e)}")
        except FileNotFoundError:
            pass
            
        return records, errors

    def check(self) -> Tuple[List[str], List[str]]:
        # Read raw lines to detect parse errors
        raw_records, parse_errors = self._read_raw_lines_with_errors()
        
        # Filter records for this run_id
        records = []
        for record_data in raw_records:
            if record_data.get('run_id') == self.run_id:
                try:
                    record = StageRecord(
                        stage=record_data['stage'],
                        items_in=record_data['items_in'],
                        items_out=record_data['items_out'],
                        dropped=record_data['dropped'],
                        run_id=record_data['run_id']
                    )
                    records.append(record)
                except KeyError:
                    # Skip malformed record data
                    pass
        
        if not records:
            # No records for this run - all declared stages are unmeasured
            unmeasured = [f"UNMEASURED: {stage} — declared but never recorded" 
                         for stage in self.declared_stages]
            return ([], unmeasured)
        
        # Build a mapping from stage to record
        stage_to_record = {r.stage: r for r in records}
        
        violations = parse_errors  # Start with parse errors
        unmeasured = []
        
        # Check each declared stage
        for i, stage in enumerate(self.declared_stages):
            if stage not in stage_to_record:
                unmeasured.append(f"UNMEASURED: {stage} — declared but never recorded")
            else:
                record = stage_to_record[stage]
                # Check closure
                if not record.closes:
                    unexplained = record.items_in - record.accounted
                    violations.append(
                        f"{record.stage}: {record.items_in} in, {record.items_out} out + "
                        f"{sum(record.dropped.values())} dropped = {record.accounted} accounted — "
                        f"{unexplained} items unexplained"
                    )
                
                # Check chain with previous stage if both are recorded
                if i > 0:
                    prev_stage = self.declared_stages[i - 1]
                    if prev_stage in stage_to_record and stage in stage_to_record:
                        prev_record = stage_to_record[prev_stage]
                        curr_record = stage_to_record[stage]
                        if prev_record.items_out != curr_record.items_in:
                            violations.append(
                                f"Chain break: {prev_stage} output ({prev_record.items_out}) "
                                f"does not match {stage} input ({curr_record.items_in})"
                            )
        
        # Check for duplicate records
        seen = set()
        duplicate_records = set()
        for record in records:
            key = (record.run_id, record.stage)
            if key in seen:
                duplicate_records.add(key)
            else:
                seen.add(key)
        
        # Report duplicates with both sets of numbers
        for run_stage in duplicate_records:
            # Find all records with this run_id and stage
            dup_records = [r for r in records if (r.run_id, r.stage) == run_stage]
            if len(dup_records) >= 2:
                first = dup_records[0]
                second = dup_records[1]
                violations.append(
                    f"Duplicate record for (run_id, stage) = ({run_stage[0]}, {run_stage[1]}): "
                    f"first: {first.items_in} in, {first.items_out} out; "
                    f"second: {second.items_in} in, {second.items_out} out"
                )
        
        # Check for negative or non-integer counts
        for record in records:
            try:
                if not isinstance(record.items_in, int) or record.items_in < 0:
                    violations.append(
                        f"Invalid items_in count: {record.items_in} in stage {record.stage}"
                    )
                if not isinstance(record.items_out, int) or record.items_out < 0:
                    violations.append(
                        f"Invalid items_out count: {record.items_out} in stage {record.stage}"
                    )
                for reason, count in record.dropped.items():
                    if not isinstance(count, int) or count < 0:
                        violations.append(
                            f"Invalid dropped count for '{reason}': {count} in stage {record.stage}"
                        )
            except Exception:
                # This shouldn't happen due to validation in record(), but just in case
                pass

        # ── ASSERT THE DENOMINATOR ───────────────────────────────────────────────────────────────
        # `items_in == items_out + dropped` is satisfied trivially by 0 == 0 + 0, so a pipeline that
        # processed NOTHING earned a clean bill of health: no violations, no unmeasured. Verified
        # 2026-07-31 — every declared stage at zero passed silently.
        #
        # The rule, from a peer agent's INV49 post-mortem the same day:
        #   **A check that finds nothing because it looked NOWHERE must FAIL, not pass.**
        # He hit the mirror image: an invariant whose scope collapsed to an empty file set, which
        # would have reported "zero violations over zero files" had he not made emptiness fatal.
        #
        # A legitimately empty run exists (an empty backlog), so this is a VIOLATION the operator
        # adjudicates — not a crash. What is not acceptable is silence.
        if records and all(r.items_in == 0 and r.items_out == 0 for r in records):
            violations.append(
                f"EMPTY RUN: all {len(records)} recorded stage(s) report 0 in / 0 out — the closure "
                f"check passed only because it had nothing to check (0 == 0 + 0). A run that "
                f"processed nothing must not report success; confirm the input was genuinely empty."
            )
        else:
            for record in records:
                if record.items_in == 0 and record.items_out == 0 and not record.dropped:
                    violations.append(
                        f"{record.stage}: 0 in / 0 out — this stage contributed no evidence. Its "
                        f"closure holds vacuously; assert it was meant to receive nothing."
                    )

        return (violations, unmeasured)


def check_file(path: str, run_id: str, declared_stages: list[str]) -> Tuple[List[str], List[str]]:
    ledger = Ledger(path, run_id, declared_stages)
    return ledger.check()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('path', help='Path to the ledger file')
    parser.add_argument('--run-id', required=True, help='Run ID')
    parser.add_argument('--stages', required=True, help='Comma-separated list of stages')
    
    args = parser.parse_args(argv)
    
    declared_stages = [s.strip() for s in args.stages.split(',')]
    
    # Handle malformed lines in the file
    violations = []
    unmeasured = []
    
    try:
        violations, unmeasured = check_file(args.path, args.run_id, declared_stages)
    except Exception as e:
        # If there's an unexpected error during check, treat it as a violation
        violations.append(f"Unexpected error during check: {str(e)}")
    
    if unmeasured:
        for u in unmeasured:
            print(u)
        if violations:
            for v in violations:
                print(v)
        return 2
    elif violations:
        for v in violations:
            print(v)
        return 1
    else:
        # Print the records to show a clean run
        ledger = Ledger(args.path, args.run_id, declared_stages)
        records = ledger.records()
        for record in records:
            print(f"{record.stage}: {record.items_in} in, {record.items_out} out")
        return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
