from datetime import timedelta
import pandas as pd
from ortools.sat.python import cp_model
import calendar
from datetime import date as dt_date
import logging
import streamlit as st
import re
from datetime import datetime
from collections import defaultdict
import os

logging.basicConfig(level=logging.INFO, force=True)

# Placeholder for future ortools-based engine

def parse_date(val):
    if isinstance(val, dt_date):
        return val
    if isinstance(val, str):
        # Try to parse 'datetime.date(YYYY, M, D)'
        m = re.match(r"datetime\.date\((\d+), (\d+), (\d+)\)", val)
        if m:
            return dt_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        # Try ISO format
        try:
            return datetime.fromisoformat(val).date()
        except Exception:
            pass
    # Fallback: try pandas
    try:
        return pd.to_datetime(val).date()
    except Exception:
        raise ValueError(f"Cannot parse date: {val}")

def generate_ortools_schedule(residents, pgy_levels, start_date, end_date, holidays=None, pgy4_call_cap=None, hard_constraints=None, soft_constraints=None, dev_settings=None, previous_block_data=None, block_transition=None, rotation_periods=None):
    """
    Uses OR-Tools CP-SAT to assign a call and backup resident to each day in the date range.
    Each day must have two different residents assigned, and only allowed PGY levels for that weekday.
    Holidays (list of dicts with 'date', 'call', 'backup') override ALL constraints for those days.
    
    Holiday Override Behavior:
    - Holidays bypass PGY level matching requirements
    - Holidays bypass hard constraints (PTO/rotation blocks)
    - Holidays bypass soft constraints (non-call requests, rotations)
    - Holidays act as manual override mechanism for special circumstances
    
    pgy4_call_cap: int or None, max number of call assignments for any PGY-4 resident in the block
    residents: list of resident names
    pgy_levels: list of PGY levels (same order as residents)
    holidays: list of dicts with 'date' (datetime.date), 'call' (str), 'backup' (str)
    hard_constraints: dict mapping resident name to list of (start_date, end_date) tuples
    soft_constraints: dict mapping resident name to list of (start_date, end_date, priority) tuples
    dev_settings: dict with custom weights for optimization
    previous_block_data: DataFrame with previous block call distribution data for inter-block fairness
    block_transition: dict with last 4 days of previous block for spacing constraints
    rotation_periods: list of dicts with 'switch_date' and 'rotation_name' for rotation-based constraints
    Returns: pd.DataFrame with columns ['Date', 'Call', 'Backup']
    """
    if not residents or start_date > end_date:
        return pd.DataFrame(columns=['Date', 'Call', 'Backup'])
    if holidays is None:
        holidays = []
    if hard_constraints is None:
        hard_constraints = {}
    if soft_constraints is None:
        soft_constraints = {}
    if dev_settings is None:
        dev_settings = {}
    if block_transition is None:
        block_transition = {}
    if rotation_periods is None:
        rotation_periods = []
    
    # Process rotation periods for rotation-based constraints
    rotation_ranges = []
    if rotation_periods:
        # Sort rotation periods by switch date
        sorted_rotations = sorted(rotation_periods, key=lambda x: x['switch_date'])
        
        # Create rotations from switch dates (last switch date is just end marker)
        for i in range(len(sorted_rotations) - 1):
            rotation = sorted_rotations[i]
            rotation_start = rotation['switch_date']
            rotation_end = sorted_rotations[i + 1]['switch_date'] - timedelta(days=1)
            
            rotation_ranges.append({
                'name': rotation.get('rotation_name', f'Rotation {i + 1}'),
                'start_date': rotation_start,
                'end_date': rotation_end
            })
        
        logging.info(f"Using {len(rotation_ranges)} rotation periods for constraints")
    else:
        logging.info("No rotation periods provided - will use 4-week rolling windows for constraints")
    
    # Process block transition data for spacing constraints
    transition_assignments = []
    if block_transition:
        for day_num in range(1, 5):
            day_key = f'day{day_num}'
            if day_key in block_transition:
                day_data = block_transition[day_key]
                if (day_data.get('date') and 
                    day_data.get('call') and 
                    day_data.get('backup')):
                    transition_assignments.append({
                        'date': parse_date(day_data['date']),
                        'call': day_data['call'],
                        'backup': day_data['backup']
                    })
        if transition_assignments:
            logging.info(f"Loaded {len(transition_assignments)} transition assignments from previous block")
        else:
            logging.info("No valid transition assignments provided")
    
    # Process previous block data for inter-block fairness
    previous_totals = {}
    if previous_block_data is not None:
        logging.info(f"Processing previous block data for {len(previous_block_data)} residents")
        # Create a mapping from resident names to their previous block totals
        for _, row in previous_block_data.iterrows():
            resident_name = str(row['Resident']).strip()  # Extra safety: strip whitespace
            previous_totals[resident_name] = {
                'call_weekday': int(row.get('Call_Weekday', 0)),
                'call_friday': int(row.get('Call_Friday', 0)),
                'call_saturday': int(row.get('Call_Saturday', 0)),
                'call_sunday': int(row.get('Call_Sunday', 0)),
                'call_total': int(row.get('Call_Total', 0)),
                'backup_weekday': int(row.get('Backup_Weekday', 0)),
                'backup_friday': int(row.get('Backup_Friday', 0)),
                'backup_saturday': int(row.get('Backup_Saturday', 0)),
                'backup_sunday': int(row.get('Backup_Sunday', 0)),
                'backup_total': int(row.get('Backup_Total', 0))
            }
        logging.info(f"Loaded previous block data for residents: {list(previous_totals.keys())}")
    else:
        logging.info("No previous block data provided - using intra-block fairness only")
    
    # Get custom weights or use defaults
    call_fairness_weight = dev_settings.get('call_fairness_weight', 1.0)
    backup_fairness_weight = dev_settings.get('backup_fairness_weight', 0.3)
    non_call_request_weight = dev_settings.get('non_call_request_weight', 10.0)
    rotation_lecture_weight = dev_settings.get('rotation_lecture_weight', 0.1)
    golden_weekend_penalty = dev_settings.get('golden_weekend_weight', 0.01)
    rotation_fairness_weight = dev_settings.get('rotation_fairness_weight', 0.5)
    same_weekday_spacing_weight = dev_settings.get('same_weekday_spacing_weight', 0.2)
    pgy4_thursday_bonus = dev_settings.get('pgy4_thursday_bonus', 0.1)
    pgy2_wednesday_bonus = dev_settings.get('pgy2_wednesday_bonus', 0.05)

    dates = pd.date_range(start=start_date, end=end_date)
    n_days = len(dates)
    n_residents = len(residents)

    model = cp_model.CpModel()

    # Variables: call[d] and backup[d] for each day d
    call = [model.NewIntVar(0, n_residents - 1, f'call_{d}') for d in range(n_days)]
    backup = [model.NewIntVar(0, n_residents - 1, f'backup_{d}') for d in range(n_days)]

    # Normalize all hard constraint dates to datetime.date and resident names
    normalized_hard_constraints = {}
    for resident, ranges in hard_constraints.items():
        norm_resident = str(resident).strip()
        new_ranges = []
        for start, end in ranges:
            start = parse_date(start)
            end = parse_date(end)
            new_ranges.append((start, end))
        normalized_hard_constraints[norm_resident] = new_ranges
    # Use normalized resident names in residents list as well
    normalized_residents = [str(r).strip() for r in residents]

    # Map holidays by date for fast lookup
    holiday_map = {pd.to_datetime(h['date']).date(): h for h in holidays}

    # --- Hard Constraints: Forbid assignments during blocked dates (except holidays) ---
    for r, resident in enumerate(normalized_residents):
        for (start, end) in normalized_hard_constraints.get(resident, []):
            # Calculate buffer day (day before constraint starts)
            buffer_day = start - timedelta(days=1)
            
            for d, dt in enumerate(dates):
                day = dt.date() if hasattr(dt, 'date') else dt
                
                # Original constraint period (skip for holidays)
                if start <= day <= end:
                    # Skip hard constraints for holiday dates (manual override)
                    if day in holiday_map:
                        continue
                    model.Add(call[d] != r)
                    model.Add(backup[d] != r)
                
                # Buffer day: prevent call and backup assignment on day before constraint starts
                # Also skip buffer day constraint if it's a holiday
                if day == buffer_day and day not in holiday_map:
                    model.Add(call[d] != r)    # Restrict call
                    model.Add(backup[d] != r)  # Restrict backup too

    # Constraints: call != backup for each day
    for d in range(n_days):
        model.Add(call[d] != backup[d])

    # Weekday/PGY constraints (skip for holidays)
    weekday_pgy = {
        6: [2],      # Sunday (6): PGY2 only
        0: [3],      # Monday (0): PGY3 only
        1: [2],      # Tuesday (1): PGY2 only
        2: [2, 3],   # Wednesday (2): PGY2 or PGY3
        3: [3, 4],   # Thursday (3): PGY3 or PGY4
        4: [2],      # Friday (4): PGY2 only
        5: [3],      # Saturday (5): PGY3 only
    }
    for d, date in enumerate(dates):
        date_only = date.date()
        if date_only in holiday_map:
            # Holiday: force assignments
            h = holiday_map[date_only]
            if h['call'] in residents:
                model.Add(call[d] == residents.index(h['call']))
            if h['backup'] in residents:
                model.Add(backup[d] == residents.index(h['backup']))
            continue
        weekday = date.weekday()
        allowed_pgy = weekday_pgy[weekday]
        allowed_indices = [i for i, pgy in enumerate(pgy_levels) if pgy in allowed_pgy]
        # At least two eligible residents for call/backup
        if len(allowed_indices) < 2:
            return pd.DataFrame(columns=['Date', 'Call', 'Backup'])
        model.AddAllowedAssignments([call[d]], [[i] for i in allowed_indices])
        model.AddAllowedAssignments([backup[d]], [[i] for i in allowed_indices])

    # Q4/Q3 constraints for each resident (apply to all days, including holidays)
    for r in range(n_residents):
        resident_name = normalized_residents[r]
        
        # Check transition assignments for spacing violations at start of block
        for d in range(min(4, n_days)):  # Only check first 4 days of current block
            current_date = dates[d].date() if hasattr(dates[d], 'date') else dates[d]
            
            for transition in transition_assignments:
                trans_date = transition['date']
                trans_call = transition['call']
                trans_backup = transition['backup']
                days_apart = (current_date - trans_date).days
                
                # Q4 constraints: Call→Call, Call→Backup, Backup→Call (4 days)
                if 0 < days_apart < 4:
                    # Previous call → current call
                    if trans_call == resident_name:
                        model.Add(call[d] != r)
                    # Previous call → current backup  
                    if trans_call == resident_name:
                        model.Add(backup[d] != r)
                    # Previous backup → current call
                    if trans_backup == resident_name:
                        model.Add(call[d] != r)
                
                # Q3 constraints: Backup→Backup (3 days)
                if 0 < days_apart < 3:
                    # Previous backup → current backup
                    if trans_backup == resident_name:
                        model.Add(backup[d] != r)
        
        # Standard Q4/Q3 constraints within current block
        # Call→Call, Call→Backup, Backup→Call: at least 4 days between
        for d1 in range(n_days):
            for offset in range(1, 4):  # 1, 2, 3 days after
                d2 = d1 + offset
                if d2 >= n_days:
                    continue
                # Call→Call
                model.AddForbiddenAssignments([call[d1], call[d2]], [(r, r)])
                # Call→Backup
                model.AddForbiddenAssignments([call[d1], backup[d2]], [(r, r)])
                # Backup→Call
                model.AddForbiddenAssignments([backup[d1], call[d2]], [(r, r)])
        # Backup→Backup: at least 3 days between
        for d1 in range(n_days):
            for offset in range(1, 3):  # 1, 2 days after
                d2 = d1 + offset
                if d2 >= n_days:
                    continue
                model.AddForbiddenAssignments([backup[d1], backup[d2]], [(r, r)])

    # PGY-4 call cap constraint
    if pgy4_call_cap is not None:
        for r, pgy in enumerate(pgy_levels):
            if pgy == 4:
                call_count = []
                for d in range(n_days):
                    is_call = model.NewBoolVar(f'is_call_{d}_{r}')
                    model.Add(call[d] == r).OnlyEnforceIf(is_call)
                    model.Add(call[d] != r).OnlyEnforceIf(is_call.Not())
                    call_count.append(is_call)
                model.Add(sum(call_count) <= pgy4_call_cap)

    # --- PGY4 Cap Enforcement ---
    # (Removed hard minimum call constraint for PGY4s)
    # Instead, add a soft preference for PGY4s on Thursdays (weekday==3)
    pgy4_indices = [i for i, pgy in enumerate(pgy_levels) if pgy == 4]
    thursday_pgy4_bonus_vars = []
    if pgy4_indices:
        for d, date in enumerate(dates):
            if date.weekday() == 3:  # Thursday
                for r in pgy4_indices:
                    is_pgy4_thu_call = model.NewBoolVar(f'pgy4_{r}_is_thu_call_{d}')
                    model.Add(call[d] == r).OnlyEnforceIf(is_pgy4_thu_call)
                    model.Add(call[d] != r).OnlyEnforceIf(is_pgy4_thu_call.Not())
                    thursday_pgy4_bonus_vars.append(is_pgy4_thu_call)

    # --- Soft preference for PGY2s on Wednesdays (weekday==2) ---
    pgy2_indices = [i for i, pgy in enumerate(pgy_levels) if pgy == 2]
    wednesday_pgy2_bonus_vars = []
    if pgy2_indices:
        for d, date in enumerate(dates):
            if date.weekday() == 2:  # Wednesday
                for r in pgy2_indices:
                    is_pgy2_wed_call = model.NewBoolVar(f'pgy2_{r}_is_wed_call_{d}')
                    model.Add(call[d] == r).OnlyEnforceIf(is_pgy2_wed_call)
                    model.Add(call[d] != r).OnlyEnforceIf(is_pgy2_wed_call.Not())
                    wednesday_pgy2_bonus_vars.append(is_pgy2_wed_call)

    # --- Fairness Optimization ---
    # Track call assignments by type for each resident
    pgy2_indices = [i for i, pgy in enumerate(pgy_levels) if pgy == 2]
    pgy3_indices = [i for i, pgy in enumerate(pgy_levels) if pgy == 3]
    pgy4_indices = [i for i, pgy in enumerate(pgy_levels) if pgy == 4]

    # Helper: for each resident, count call and backup assignments by type
    weekday_call = {r: [] for r in range(n_residents)}  # Mon-Thu
    friday_call = {r: [] for r in range(n_residents)}   # Fri
    sunday_call = {r: [] for r in range(n_residents)}   # Sun
    saturday_call = {r: [] for r in range(n_residents)} # Sat
    total_call = {r: [] for r in range(n_residents)}
    weekday_backup = {r: [] for r in range(n_residents)}
    friday_backup = {r: [] for r in range(n_residents)}
    sunday_backup = {r: [] for r in range(n_residents)}
    saturday_backup = {r: [] for r in range(n_residents)}
    total_backup = {r: [] for r in range(n_residents)}
    for d, date in enumerate(dates):
        wd = date.weekday()
        for r in range(n_residents):
            is_call = model.NewBoolVar(f'is_call_{d}_{r}_fair')
            model.Add(call[d] == r).OnlyEnforceIf(is_call)
            model.Add(call[d] != r).OnlyEnforceIf(is_call.Not())
            total_call[r].append(is_call)
            if wd in [0, 1, 2, 3]:  # Mon-Thu
                weekday_call[r].append(is_call)
            if wd == 4:  # Fri
                friday_call[r].append(is_call)
            if wd == 5:  # Sat
                saturday_call[r].append(is_call)
            if wd == 6:  # Sun
                sunday_call[r].append(is_call)
            # Backup
            is_backup = model.NewBoolVar(f'is_backup_{d}_{r}_fair')
            model.Add(backup[d] == r).OnlyEnforceIf(is_backup)
            model.Add(backup[d] != r).OnlyEnforceIf(is_backup.Not())
            total_backup[r].append(is_backup)
            if wd in [0, 1, 2, 3]:
                weekday_backup[r].append(is_backup)
            if wd == 4:
                friday_backup[r].append(is_backup)
            if wd == 5:
                saturday_backup[r].append(is_backup)
            if wd == 6:
                sunday_backup[r].append(is_backup)

    # PGY2: weekday, Friday, Sunday fairness (call and backup)
    fairness_vars = []
    # Call fairness (higher weight)
    for call_type, indices in [(weekday_call, pgy2_indices), (friday_call, pgy2_indices), (sunday_call, pgy2_indices)]:
        if indices:
            counts = [model.NewIntVar(0, n_days, f'pgy2_{t}_count_{r}') for t, r in enumerate(indices)]
            for idx, r in enumerate(indices):
                model.Add(counts[idx] == sum(call_type[r]))
            
            # Add previous block totals for inter-block fairness
            cumulative_counts = []
            for idx, r in enumerate(indices):
                resident_name = residents[r]
                if resident_name in previous_totals:
                    # Map call_type to the appropriate previous total
                    if call_type == weekday_call:
                        prev_total = previous_totals[resident_name]['call_weekday']
                    elif call_type == friday_call:
                        prev_total = previous_totals[resident_name]['call_friday']
                    elif call_type == sunday_call:
                        prev_total = previous_totals[resident_name]['call_sunday']
                    else:
                        prev_total = 0
                    
                    # Create cumulative count variable
                    cumulative = model.NewIntVar(0, n_days + 100, f'pgy2_cumulative_{r}')  # +100 for previous block totals
                    model.Add(cumulative == counts[idx] + prev_total)
                    cumulative_counts.append(cumulative)
                else:
                    # No previous data, use current block count
                    cumulative_counts.append(counts[idx])
            
            # Use cumulative counts for fairness optimization
            max_count = model.NewIntVar(0, n_days + 100, 'pgy2_max')
            min_count = model.NewIntVar(0, n_days + 100, 'pgy2_min')
            model.AddMaxEquality(max_count, cumulative_counts)
            model.AddMinEquality(min_count, cumulative_counts)
            fairness_vars.append(call_fairness_weight * (max_count - min_count))
    # Backup fairness (lower weight)
    for backup_type, indices in [(weekday_backup, pgy2_indices), (friday_backup, pgy2_indices), (sunday_backup, pgy2_indices)]:
        if indices:
            counts = [model.NewIntVar(0, n_days, f'pgy2_bk_{t}_count_{r}') for t, r in enumerate(indices)]
            for idx, r in enumerate(indices):
                model.Add(counts[idx] == sum(backup_type[r]))
            
            # Add previous block totals for inter-block fairness
            cumulative_counts = []
            for idx, r in enumerate(indices):
                resident_name = residents[r]
                if resident_name in previous_totals:
                    # Map backup_type to the appropriate previous total
                    if backup_type == weekday_backup:
                        prev_total = previous_totals[resident_name]['backup_weekday']
                    elif backup_type == friday_backup:
                        prev_total = previous_totals[resident_name]['backup_friday']
                    elif backup_type == sunday_backup:
                        prev_total = previous_totals[resident_name]['backup_sunday']
                    else:
                        prev_total = 0
                    
                    # Create cumulative count variable
                    cumulative = model.NewIntVar(0, n_days + 100, f'pgy2_bk_cumulative_{r}')
                    model.Add(cumulative == counts[idx] + prev_total)
                    cumulative_counts.append(cumulative)
                else:
                    # No previous data, use current block count
                    cumulative_counts.append(counts[idx])
            
            # Use cumulative counts for fairness optimization
            max_count = model.NewIntVar(0, n_days + 100, 'pgy2_bk_max')
            min_count = model.NewIntVar(0, n_days + 100, 'pgy2_bk_min')
            model.AddMaxEquality(max_count, cumulative_counts)
            model.AddMinEquality(min_count, cumulative_counts)
            fairness_vars.append(backup_fairness_weight * (max_count - min_count))
    # PGY3: weekday, Saturday fairness (call and backup)
    for call_type, indices in [(weekday_call, pgy3_indices), (saturday_call, pgy3_indices)]:
        if indices:
            counts = [model.NewIntVar(0, n_days, f'pgy3_{t}_count_{r}') for t, r in enumerate(indices)]
            for idx, r in enumerate(indices):
                model.Add(counts[idx] == sum(call_type[r]))
            
            # Add previous block totals for inter-block fairness
            cumulative_counts = []
            for idx, r in enumerate(indices):
                resident_name = residents[r]
                if resident_name in previous_totals:
                    # Map call_type to the appropriate previous total
                    if call_type == weekday_call:
                        prev_total = previous_totals[resident_name]['call_weekday']
                    elif call_type == saturday_call:
                        prev_total = previous_totals[resident_name]['call_saturday']
                    else:
                        prev_total = 0
                    
                    # Create cumulative count variable
                    cumulative = model.NewIntVar(0, n_days + 100, f'pgy3_cumulative_{r}')
                    model.Add(cumulative == counts[idx] + prev_total)
                    cumulative_counts.append(cumulative)
                else:
                    # No previous data, use current block count
                    cumulative_counts.append(counts[idx])
            
            # Use cumulative counts for fairness optimization
            max_count = model.NewIntVar(0, n_days + 100, 'pgy3_max')
            min_count = model.NewIntVar(0, n_days + 100, 'pgy3_min')
            model.AddMaxEquality(max_count, cumulative_counts)
            model.AddMinEquality(min_count, cumulative_counts)
            fairness_vars.append(call_fairness_weight * (max_count - min_count))
    for backup_type, indices in [(weekday_backup, pgy3_indices), (saturday_backup, pgy3_indices)]:
        if indices:
            counts = [model.NewIntVar(0, n_days, f'pgy3_bk_{t}_count_{r}') for t, r in enumerate(indices)]
            for idx, r in enumerate(indices):
                model.Add(counts[idx] == sum(backup_type[r]))
            
            # Add previous block totals for inter-block fairness
            cumulative_counts = []
            for idx, r in enumerate(indices):
                resident_name = residents[r]
                if resident_name in previous_totals:
                    # Map backup_type to the appropriate previous total
                    if backup_type == weekday_backup:
                        prev_total = previous_totals[resident_name]['backup_weekday']
                    elif backup_type == saturday_backup:
                        prev_total = previous_totals[resident_name]['backup_saturday']
                    else:
                        prev_total = 0
                    
                    # Create cumulative count variable
                    cumulative = model.NewIntVar(0, n_days + 100, f'pgy3_bk_cumulative_{r}')
                    model.Add(cumulative == counts[idx] + prev_total)
                    cumulative_counts.append(cumulative)
                else:
                    # No previous data, use current block count
                    cumulative_counts.append(counts[idx])
            
            # Use cumulative counts for fairness optimization
            max_count = model.NewIntVar(0, n_days + 100, 'pgy3_bk_max')
            min_count = model.NewIntVar(0, n_days + 100, 'pgy3_bk_min')
            model.AddMaxEquality(max_count, cumulative_counts)
            model.AddMinEquality(min_count, cumulative_counts)
            fairness_vars.append(backup_fairness_weight * (max_count - min_count))
    # PGY4: total call and backup fairness
    if pgy4_indices:
        counts = [model.NewIntVar(0, n_days, f'pgy4_count_{r}') for r in pgy4_indices]
        for idx, r in enumerate(pgy4_indices):
            model.Add(counts[idx] == sum(total_call[r]))
        
        # Add previous block totals for inter-block fairness
        cumulative_counts = []
        for idx, r in enumerate(pgy4_indices):
            resident_name = residents[r]
            if resident_name in previous_totals:
                prev_total = previous_totals[resident_name]['call_total']
                # Create cumulative count variable
                cumulative = model.NewIntVar(0, n_days + 100, f'pgy4_cumulative_{r}')
                model.Add(cumulative == counts[idx] + prev_total)
                cumulative_counts.append(cumulative)
            else:
                # No previous data, use current block count
                cumulative_counts.append(counts[idx])
        
        # Use cumulative counts for fairness optimization
        max_count = model.NewIntVar(0, n_days + 100, 'pgy4_max')
        min_count = model.NewIntVar(0, n_days + 100, 'pgy4_min')
        model.AddMaxEquality(max_count, cumulative_counts)
        model.AddMinEquality(min_count, cumulative_counts)
        fairness_vars.append(call_fairness_weight * (max_count - min_count))
        # Backup fairness for PGY4
        counts_bk = [model.NewIntVar(0, n_days, f'pgy4_bk_count_{r}') for r in pgy4_indices]
        for idx, r in enumerate(pgy4_indices):
            model.Add(counts_bk[idx] == sum(total_backup[r]))
        
        # Add previous block totals for inter-block fairness
        cumulative_counts_bk = []
        for idx, r in enumerate(pgy4_indices):
            resident_name = residents[r]
            if resident_name in previous_totals:
                prev_total = previous_totals[resident_name]['backup_total']
                # Create cumulative count variable
                cumulative = model.NewIntVar(0, n_days + 100, f'pgy4_bk_cumulative_{r}')
                model.Add(cumulative == counts_bk[idx] + prev_total)
                cumulative_counts_bk.append(cumulative)
            else:
                # No previous data, use current block count
                cumulative_counts_bk.append(counts_bk[idx])
        
        # Use cumulative counts for fairness optimization
        max_count_bk = model.NewIntVar(0, n_days + 100, 'pgy4_bk_max')
        min_count_bk = model.NewIntVar(0, n_days + 100, 'pgy4_bk_min')
        model.AddMaxEquality(max_count_bk, cumulative_counts_bk)
        model.AddMinEquality(min_count_bk, cumulative_counts_bk)
        fairness_vars.append(backup_fairness_weight * (max_count_bk - min_count_bk))
    # --- Soft Constraints: Violation variables (except holidays) ---
    soft_violation_vars = []
    for r, resident in enumerate(residents):
        for constraint in soft_constraints.get(resident, []):
            # Support both (start, end) and (start, end, priority)
            if len(constraint) == 2:
                start, end = constraint
                priority = "Rotation/Lecture"
            else:
                start, end, priority = constraint
            start = parse_date(start)
            end = parse_date(end)
            for d, dt in enumerate(dates):
                day = dt.date() if hasattr(dt, 'date') else dt
                # Skip soft constraints for holiday dates (manual override)
                if day in holiday_map:
                    continue
                if start <= day <= end:
                    if priority == "Non-call request":
                        # Violation if assigned as call or backup
                        is_call = model.NewBoolVar(f'soft_call_{resident}_{d}')
                        is_backup = model.NewBoolVar(f'soft_backup_{resident}_{d}')
                        model.Add(call[d] == r).OnlyEnforceIf(is_call)
                        model.Add(call[d] != r).OnlyEnforceIf(is_call.Not())
                        model.Add(backup[d] == r).OnlyEnforceIf(is_backup)
                        model.Add(backup[d] != r).OnlyEnforceIf(is_backup.Not())
                        violation = model.NewBoolVar(f'soft_violation_{resident}_{d}')
                        model.AddMaxEquality(violation, [is_call, is_backup])
                        # Higher weight for non-call request
                        soft_violation_vars.append((violation, non_call_request_weight))
                    else:
                        # Violation if assigned as call only
                        is_call = model.NewBoolVar(f'soft_call_{resident}_{d}')
                        model.Add(call[d] == r).OnlyEnforceIf(is_call)
                        model.Add(call[d] != r).OnlyEnforceIf(is_call.Not())
                        # Lower weight for rotation/lecture
                        soft_violation_vars.append((is_call, rotation_lecture_weight))
    # --- Rotation Fairness: Encourage at least 1 call and 1 backup per resident per rotation ---
    rotation_fairness_violations = []
    
    if rotation_ranges:
        # Apply rotation fairness for PGY2 and PGY3 only
        pgy2_pgy3_indices = [i for i, pgy in enumerate(pgy_levels) if pgy in [2, 3]]
        
        for r in pgy2_pgy3_indices:
            for rotation in rotation_ranges:
                rotation_start = rotation['start_date']
                rotation_end = rotation['end_date']
                
                # Find all days within this rotation period
                rotation_days = []
                for d, date in enumerate(dates):
                    date_obj = date.date() if hasattr(date, 'date') else date
                    if rotation_start <= date_obj <= rotation_end:
                        rotation_days.append(d)
                
                if rotation_days:  # Only if there are days in this rotation
                    # Call fairness: penalize if resident has 0 calls in this rotation
                    call_assignments = [model.NewBoolVar(f'call_in_rotation_{r}_{rotation["name"]}_{d}') for d in rotation_days]
                    for i, d in enumerate(rotation_days):
                        model.Add(call[d] == r).OnlyEnforceIf(call_assignments[i])
                        model.Add(call[d] != r).OnlyEnforceIf(call_assignments[i].Not())
                    
                    # Violation if sum of call assignments in rotation is 0
                    call_violation = model.NewBoolVar(f'call_fairness_violation_{r}_{rotation["name"]}')
                    model.Add(sum(call_assignments) == 0).OnlyEnforceIf(call_violation)
                    model.Add(sum(call_assignments) >= 1).OnlyEnforceIf(call_violation.Not())
                    rotation_fairness_violations.append(call_violation)
                    
                    # Backup fairness: penalize if resident has 0 backups in this rotation
                    backup_assignments = [model.NewBoolVar(f'backup_in_rotation_{r}_{rotation["name"]}_{d}') for d in rotation_days]
                    for i, d in enumerate(rotation_days):
                        model.Add(backup[d] == r).OnlyEnforceIf(backup_assignments[i])
                        model.Add(backup[d] != r).OnlyEnforceIf(backup_assignments[i].Not())
                    
                    # Violation if sum of backup assignments in rotation is 0
                    backup_violation = model.NewBoolVar(f'backup_fairness_violation_{r}_{rotation["name"]}')
                    model.Add(sum(backup_assignments) == 0).OnlyEnforceIf(backup_violation)
                    model.Add(sum(backup_assignments) >= 1).OnlyEnforceIf(backup_violation.Not())
                    rotation_fairness_violations.append(backup_violation)
        
        logging.info(f"Added {len(rotation_fairness_violations)} rotation fairness constraints")
    else:
        logging.info("No rotation periods provided - skipping rotation fairness constraints")
    
    # --- Same-Weekday Spacing: Prevent clustering of same weekday assignments ---
    same_weekday_spacing_violations = []
    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    
    for r, resident in enumerate(residents):
        for weekday in range(7):
            # Find all indices for this weekday
            day_indices = [d for d, date in enumerate(dates) if date.weekday() == weekday]
            
            # Check pairs within 2-week (14-day) window instead of 3-week
            for i in range(len(day_indices) - 1):
                d1 = day_indices[i]
                for j in range(i + 1, len(day_indices)):
                    d2 = day_indices[j]
                    # Penalize if d2 is within 14 days (2 weeks) of d1
                    if 0 < (dates[d2] - dates[d1]).days <= 14:
                        is_assigned_d1 = model.NewBoolVar(f'same_weekday_{resident}_{weekday_names[weekday]}_{d1}')
                        is_assigned_d2 = model.NewBoolVar(f'same_weekday_{resident}_{weekday_names[weekday]}_{d2}')
                        
                        model.Add(call[d1] == r).OnlyEnforceIf(is_assigned_d1)
                        model.Add(call[d1] != r).OnlyEnforceIf(is_assigned_d1.Not())
                        model.Add(call[d2] == r).OnlyEnforceIf(is_assigned_d2)
                        model.Add(call[d2] != r).OnlyEnforceIf(is_assigned_d2.Not())
                        
                        spacing_violation = model.NewBoolVar(f'spacing_violation_{resident}_{weekday_names[weekday]}_{d1}_{d2}')
                        model.AddBoolAnd([is_assigned_d1, is_assigned_d2]).OnlyEnforceIf(spacing_violation)
                        model.AddBoolOr([is_assigned_d1.Not(), is_assigned_d2.Not()]).OnlyEnforceIf(spacing_violation.Not())
                        same_weekday_spacing_violations.append(spacing_violation)
    
    logging.info(f"Added {len(same_weekday_spacing_violations)} same-weekday spacing constraints (2-week window)")
    # --- Golden Weekend Soft Constraint for PGY2s ---
    golden_weekends = {r: [] for r in pgy2_indices}  # r: list of (fri_date, is_golden)
    golden_weekend_vars = []
    for fri_idx, date in enumerate(dates):
        if date.weekday() != 4:  # Friday
            continue
        # Get indices for Fri, Sat, Sun
        fri = fri_idx
        sat = fri + 1 if fri + 1 < n_days and dates[fri + 1].weekday() == 5 else None
        sun = fri + 2 if fri + 2 < n_days and dates[fri + 2].weekday() == 6 else None
        for r in pgy2_indices:
            # Not assigned to call or backup on Fri, Sat, Sun
            not_call_bk = []
            for d in [fri, sat, sun]:
                if d is not None:
                    not_call = model.NewBoolVar(f'gw_not_call_{r}_{d}')
                    not_bk = model.NewBoolVar(f'gw_not_bk_{r}_{d}')
                    model.Add(call[d] != r).OnlyEnforceIf(not_call)
                    model.Add(call[d] == r).OnlyEnforceIf(not_call.Not())
                    model.Add(backup[d] != r).OnlyEnforceIf(not_bk)
                    model.Add(backup[d] == r).OnlyEnforceIf(not_bk.Not())
                    not_call_bk.append(not_call)
                    not_call_bk.append(not_bk)
            # Golden weekend if all not_call_bk are true
            if not_call_bk:
                is_golden = model.NewBoolVar(f'gw_{r}_{fri}')
                model.AddBoolAnd(not_call_bk).OnlyEnforceIf(is_golden)
                model.AddBoolOr([x.Not() for x in not_call_bk]).OnlyEnforceIf(is_golden.Not())
                # Penalty for not getting golden weekend
                not_golden = model.NewBoolVar(f'gw_penalty_{r}_{fri}')
                model.Add(is_golden == 0).OnlyEnforceIf(not_golden)
                model.Add(is_golden == 1).OnlyEnforceIf(not_golden.Not())
                golden_weekend_vars.append(not_golden)
                golden_weekends[r].append((date.date(), is_golden))

    # --- Golden Weekend Rotation Constraint: At least 1 golden weekend per PGY2 per rotation period ---
    golden_rotation_violations = []
    
    if rotation_ranges:
        # Use rotation periods
        for r in pgy2_indices:
            for rotation in rotation_ranges:
                rotation_start = rotation['start_date']
                rotation_end = rotation['end_date']
                
                # Find all golden weekends (Friday dates) within this rotation period
                golden_weekends_in_rotation = []
                for fri_date, is_golden_var in golden_weekends[r]:
                    if rotation_start <= fri_date <= rotation_end:
                        golden_weekends_in_rotation.append(is_golden_var)
                
                # If there are potential golden weekends in this rotation, add constraint
                if len(golden_weekends_in_rotation) >= 1:
                    # Create violation variable for this rotation period
                    violation = model.NewBoolVar(f'gw_rotation_violation_{r}_{rotation["name"]}')
                    # Violation occurs if sum of golden weekends in rotation < 1
                    model.Add(sum(golden_weekends_in_rotation) >= 1).OnlyEnforceIf(violation.Not())
                    model.Add(sum(golden_weekends_in_rotation) == 0).OnlyEnforceIf(violation)
                    golden_rotation_violations.append(violation)
        
        logging.info(f"Added {len(golden_rotation_violations)} golden weekend rotation constraints")
    else:
        # Fallback to 4-week rolling windows
        window_size_days = 28  # 4 weeks = 28 days
        
        for r in pgy2_indices:
            # For each possible 4-week window, ensure PGY2 has at least 1 golden weekend
            for start_idx in range(n_days):
                start_date = dates[start_idx].date() if hasattr(dates[start_idx], 'date') else dates[start_idx]
                end_date = start_date + timedelta(days=window_size_days - 1)
                
                # Find all golden weekends (Friday dates) within this 4-week window
                golden_weekends_in_window = []
                for fri_date, is_golden_var in golden_weekends[r]:
                    if start_date <= fri_date <= end_date:
                        golden_weekends_in_window.append(is_golden_var)
                
                # If there are potential golden weekends in this window, add constraint
                if len(golden_weekends_in_window) >= 1:
                    # Create violation variable for this 4-week window
                    violation = model.NewBoolVar(f'gw_4week_violation_{r}_{start_idx}')
                    # Violation occurs if sum of golden weekends in window < 1
                    model.Add(sum(golden_weekends_in_window) >= 1).OnlyEnforceIf(violation.Not())
                    model.Add(sum(golden_weekends_in_window) == 0).OnlyEnforceIf(violation)
                    golden_rotation_violations.append(violation)
        
        logging.info(f"Added {len(golden_rotation_violations)} golden weekend 4-week fallback constraints")

    # Objective: minimize sum of all spreads, soft constraint violations, rotation fairness, same-weekday spacing, golden weekend penalties, and add soft preference for PGY4s on Thursdays and PGY2s on Wednesdays
    soft_obj = sum(weight * var for var, weight in soft_violation_vars) if soft_violation_vars else model.NewConstant(0)
    rotation_fairness_obj = rotation_fairness_weight * sum(rotation_fairness_violations) if rotation_fairness_violations else 0
    same_weekday_spacing_obj = same_weekday_spacing_weight * sum(same_weekday_spacing_violations) if same_weekday_spacing_violations else 0
    golden_obj = golden_weekend_penalty * sum(golden_weekend_vars) if golden_weekend_vars else 0
    golden_rotation_obj = 5.0 * sum(golden_rotation_violations) if golden_rotation_violations else 0  # Higher weight for rotation constraint
    
    if fairness_vars or thursday_pgy4_bonus_vars or soft_violation_vars or rotation_fairness_violations or same_weekday_spacing_violations or golden_weekend_vars or wednesday_pgy2_bonus_vars or golden_rotation_violations:
        model.Minimize(
            sum(fairness_vars)
            - pgy4_thursday_bonus * sum(thursday_pgy4_bonus_vars)
            - pgy2_wednesday_bonus * sum(wednesday_pgy2_bonus_vars)
            + soft_obj + rotation_fairness_obj + same_weekday_spacing_obj + golden_obj + golden_rotation_obj
        )

    # Constraint: backup PGY level must match call PGY level for each day (except holidays)
    for d in range(n_days):
        date_only = dates[d].date()
        # Skip PGY matching constraint for holiday dates (manual override)
        if date_only in holiday_map:
            continue
        for r_call in range(n_residents):
            for r_backup in range(n_residents):
                if pgy_levels[r_call] != pgy_levels[r_backup]:
                    # If call[d] == r_call and backup[d] == r_backup, this is forbidden
                    model.AddForbiddenAssignments([call[d], backup[d]], [(r_call, r_backup)])

    # Solve
    solver = cp_model.CpSolver()
    status = solver.Solve(model)
    
    # Get the final objective value
    final_objective_value = solver.ObjectiveValue() if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None

    assignments = []
    golden_weekends_count = {}
    
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for d, date in enumerate(dates):
            call_idx = solver.Value(call[d])
            backup_idx = solver.Value(backup[d])
            assignments.append({
                'Date': date.date(),
                'Call': residents[call_idx],
                'Backup': residents[backup_idx]
            })
        
        # Count golden weekends by rotation period if available, otherwise use totals
        if rotation_ranges:
            # Initialize rotation-based counting
            for rotation in rotation_ranges:
                golden_weekends_count[rotation['name']] = {residents[r]: 0 for r in pgy2_indices}
            
            # Count golden weekends for each PGY2 by rotation
            for r in pgy2_indices:
                for fri_date, is_golden_var in golden_weekends[r]:
                    if solver.Value(is_golden_var):
                        # Find which rotation this golden weekend belongs to
                        for rotation in rotation_ranges:
                            if rotation['start_date'] <= fri_date <= rotation['end_date']:
                                golden_weekends_count[rotation['name']][residents[r]] += 1
                                break
        else:
            # Fallback to total counts if no rotation periods
            golden_weekends_count = {residents[r]: 0 for r in pgy2_indices}
            for r in pgy2_indices:
                for fri_date, is_golden_var in golden_weekends[r]:
                    if solver.Value(is_golden_var):
                        golden_weekends_count[residents[r]] += 1
    else:
        # No solution found
        return pd.DataFrame(columns=['Date', 'Call', 'Backup']), {residents[r]: 0 for r in pgy2_indices}, None

    return pd.DataFrame(assignments), golden_weekends_count, final_objective_value 

def optimize_intern_assignments(schedule_df, residents, pgy_levels, hard_constraints, soft_constraints, dev_settings=None, intern_cap=None, rotation_periods=None):
    """
    OPTIMIZED: Uses simplified OR-Tools CP-SAT to efficiently optimize intern assignments.
    Focuses on essential constraints only for better performance.
    
    Args:
        intern_cap: Maximum number of assignments per intern per 4-week period (default: no limit)
    """
    if dev_settings is None:
        dev_settings = {}
    if rotation_periods is None:
        rotation_periods = []
    
    # Process rotation periods for rotation-based constraints
    rotation_ranges = []
    if rotation_periods:
        # Sort rotation periods by switch date
        sorted_rotations = sorted(rotation_periods, key=lambda x: x['switch_date'])
        
        # Get block start and end dates from schedule
        schedule_start = pd.to_datetime(schedule_df['Date']).min().date()
        schedule_end = pd.to_datetime(schedule_df['Date']).max().date()
        
        # Create rotations from switch dates (last switch date is just end marker)
        for i in range(len(sorted_rotations) - 1):
            rotation = sorted_rotations[i]
            rotation_start = rotation['switch_date']
            rotation_end = sorted_rotations[i + 1]['switch_date'] - timedelta(days=1)
            
            rotation_ranges.append({
                'name': rotation.get('rotation_name', f'Rotation {i + 1}'),
                'start_date': rotation_start,
                'end_date': rotation_end
            })
        
        logging.info(f"Using {len(rotation_ranges)} rotation periods for intern constraints")
    else:
        logging.info("No rotation periods provided - will use 4-week rolling windows for intern constraints")
    
    # Identify interns (PGY1)
    intern_indices = [i for i, pgy in enumerate(pgy_levels) if pgy == 1]
    intern_names = [residents[i] for i in intern_indices]
    
    if not intern_names:
        # No interns to assign
        schedule_df = schedule_df.copy()
        schedule_df['Intern'] = None
        return schedule_df, pd.DataFrame(), None
    
    # Find days where interns can be assigned (PGY3/PGY4 on call)
    intern_days = []
    for idx, row in schedule_df.iterrows():
        call = row['Call']
        call_pgy = None
        if call in residents:
            call_pgy = pgy_levels[residents.index(call)]
        if call_pgy in [3, 4]:
            intern_days.append(idx)
    
    logging.info(f"Found {len(intern_days)} intern days for {len(intern_names)} interns")
    
    if not intern_days:
        # No days where interns can be assigned
        logging.warning("No intern days found - no PGY3/PGY4 call assignments in schedule")
        schedule_df = schedule_df.copy()
        schedule_df['Intern'] = None
        return schedule_df, pd.DataFrame(), None
    
    # OPTIMIZED: Build constraint lookups more efficiently
    hard_lookup = defaultdict(set)
    soft_violations_lookup = defaultdict(set)  # Only track "Non-call request" soft constraints
    
    for name in intern_names:
        # Hard constraints - same as before but more efficient
        for rng in hard_constraints.get(name, []):
            start, end = rng
            # Direct date iteration instead of pd.date_range
            current = start if hasattr(start, 'date') else pd.to_datetime(start).date()
            end_date = end if hasattr(end, 'date') else pd.to_datetime(end).date()
            while current <= end_date:
                hard_lookup[name].add(current)
                current += pd.Timedelta(days=1).to_pytimedelta()
        
        # Soft constraints - only track "Non-call request" (high priority)
        for sc in soft_constraints.get(name, []):
            if len(sc) == 2:
                start, end = sc
                priority = "Rotation/Lecture"
            else:
                start, end, priority = sc
            
            # Only process high-priority soft constraints
            if priority == "Non-call request":
                current = start if hasattr(start, 'date') else pd.to_datetime(start).date()
                end_date = end if hasattr(end, 'date') else pd.to_datetime(end).date()
                while current <= end_date:
                    soft_violations_lookup[name].add(current)
                    current += pd.Timedelta(days=1).to_pytimedelta()
    
    # Create OR-Tools model
    model = cp_model.CpModel()
    n_intern_days = len(intern_days)
    n_interns = len(intern_names)
    
    # Create assignment variables
    intern_assigned = {}
    for d_idx, day_idx in enumerate(intern_days):
        intern_assigned[d_idx] = {}
        for i, intern_name in enumerate(intern_names):
            date = pd.to_datetime(schedule_df.iloc[day_idx]['Date']).date()
            if date in hard_lookup[intern_name]:
                intern_assigned[d_idx][i] = model.NewConstant(0)
            else:
                intern_assigned[d_idx][i] = model.NewBoolVar(f'intern_{d_idx}_{i}')
    
    # Constraint: Each intern day must have exactly one intern assigned
    for d_idx in range(n_intern_days):
        day_idx = intern_days[d_idx]
        date = pd.to_datetime(schedule_df.iloc[day_idx]['Date']).date()
        eligible_interns = []
        for i, intern_name in enumerate(intern_names):
            if date not in hard_lookup[intern_name]:
                eligible_interns.append(intern_assigned[d_idx][i])
        
        if eligible_interns:
            model.Add(sum(eligible_interns) == 1)
        else:
            # Log warning if no eligible interns for a day
            logging.warning(f"No eligible interns for date {date} - all interns have hard constraints")
    
    # OPTIMIZED: Simplified constraints for better performance
    
    # Prevent 3+ consecutive intern slot assignments (allow up to 2)
    # Also add soft penalty for any consecutive assignments to discourage when possible
    consecutive_hard_constraints = 0
    consecutive_soft_penalties = []
    
    for i, intern_name in enumerate(intern_names):
        # Hard constraint: prevent 3+ consecutive intern slots
        for d1_idx in range(n_intern_days - 2):  # Check triplets
            d2_idx = d1_idx + 1
            d3_idx = d1_idx + 2
            
            if (isinstance(intern_assigned[d1_idx][i], cp_model.IntVar) and 
                isinstance(intern_assigned[d2_idx][i], cp_model.IntVar) and
                isinstance(intern_assigned[d3_idx][i], cp_model.IntVar)):
                # Prevent all 3 consecutive slots being assigned to same intern
                model.Add(intern_assigned[d1_idx][i] + intern_assigned[d2_idx][i] + intern_assigned[d3_idx][i] <= 2)
                consecutive_hard_constraints += 1
        
        # Soft penalty: discourage any 2 consecutive assignments when possible
        for d1_idx in range(n_intern_days - 1):
            d2_idx = d1_idx + 1
            
            if (isinstance(intern_assigned[d1_idx][i], cp_model.IntVar) and 
                isinstance(intern_assigned[d2_idx][i], cp_model.IntVar)):
                # Create penalty variable for consecutive assignments
                consecutive_penalty = model.NewBoolVar(f'consecutive_penalty_{i}_{d1_idx}')
                model.AddBoolAnd([intern_assigned[d1_idx][i], intern_assigned[d2_idx][i]]).OnlyEnforceIf(consecutive_penalty)
                model.AddBoolOr([intern_assigned[d1_idx][i].Not(), intern_assigned[d2_idx][i].Not()]).OnlyEnforceIf(consecutive_penalty.Not())
                consecutive_soft_penalties.append(consecutive_penalty)

    logging.info(f"Added {consecutive_hard_constraints} hard constraints (prevent 3+ consecutive) and {len(consecutive_soft_penalties)} soft penalties (discourage 2 consecutive)")

    # Track assignments for fairness (total, weekday, Saturday)
    intern_total_counts = []
    intern_weekday_counts = []
    intern_saturday_counts = []

    for i in range(n_interns):
        total_assignments = []
        weekday_assignments = []
        saturday_assignments = []

        for d_idx, day_idx in enumerate(intern_days):
            date = pd.to_datetime(schedule_df.iloc[day_idx]['Date'])
            weekday = date.weekday()
            
            assignment_var = intern_assigned[d_idx].get(i)
            if isinstance(assignment_var, cp_model.IntVar):
                total_assignments.append(assignment_var)
                if weekday < 5:  # Weekday (Mon-Fri)
                    weekday_assignments.append(assignment_var)
                elif weekday == 5:  # Saturday
                    saturday_assignments.append(assignment_var)

        if total_assignments:
            intern_total_counts.append(sum(total_assignments))
        if weekday_assignments:
            intern_weekday_counts.append(sum(weekday_assignments))
        if saturday_assignments:
            intern_saturday_counts.append(sum(saturday_assignments))
            
    # SIMPLIFIED: Only track high-priority soft constraint violations
    soft_violations = []
    for d_idx, day_idx in enumerate(intern_days):
        date = pd.to_datetime(schedule_df.iloc[day_idx]['Date']).date()
        for i, intern_name in enumerate(intern_names):
            if isinstance(intern_assigned[d_idx][i], cp_model.IntVar):
                if date in soft_violations_lookup[intern_name]:
                    soft_violations.append(intern_assigned[d_idx][i])
    
    logging.info(f"Tracking {len(soft_violations)} high-priority soft constraint violations")
    
    # Intern cap constraint: Maximum assignments per intern per 4-week period
    if intern_cap is not None and intern_cap > 0:
        cap_constraints = 0
        window_size_days = 28  # 4 weeks = 28 days
        
        for i, intern_name in enumerate(intern_names):
            # For each possible 4-week window, ensure intern doesn't exceed cap
            for start_idx in range(n_intern_days):
                # Define the 4-week window starting from this day
                start_day_idx = intern_days[start_idx]
                start_date = pd.to_datetime(schedule_df.iloc[start_day_idx]['Date']).date()
                end_date = start_date + pd.Timedelta(days=window_size_days - 1).to_pytimedelta()
                
                # Find all intern days within this 4-week window
                window_assignments = []
                for check_idx in range(start_idx, n_intern_days):
                    check_day_idx = intern_days[check_idx]
                    check_date = pd.to_datetime(schedule_df.iloc[check_day_idx]['Date']).date()
                    
                    if start_date <= check_date <= end_date:
                        if isinstance(intern_assigned[check_idx][i], cp_model.IntVar):
                            window_assignments.append(intern_assigned[check_idx][i])
                    elif check_date > end_date:
                        break  # Days are sorted, so we can break early
                
                # Apply cap constraint to this 4-week window
                if len(window_assignments) > intern_cap:
                    model.Add(sum(window_assignments) <= intern_cap)
                    cap_constraints += 1
        
        logging.info(f"Applied intern cap of {intern_cap} assignments per 4-week period, created {cap_constraints} window constraints")
    else:
        logging.info("No intern cap specified - unlimited assignments per intern")
    
    # Saturday cap constraint: Maximum 2 Saturday assignments per intern per 4-week period
    saturday_cap_constraints = 0
    window_size_days = 28  # 4 weeks = 28 days
    
    for i, intern_name in enumerate(intern_names):
        # For each possible 4-week window, ensure intern doesn't exceed 2 Saturday assignments
        for start_idx in range(n_intern_days):
            # Define the 4-week window starting from this day
            start_day_idx = intern_days[start_idx]
            start_date = pd.to_datetime(schedule_df.iloc[start_day_idx]['Date']).date()
            end_date = start_date + pd.Timedelta(days=window_size_days - 1).to_pytimedelta()
            
            # Find all Saturday intern days within this 4-week window
            saturday_assignments = []
            for check_idx in range(start_idx, n_intern_days):
                check_day_idx = intern_days[check_idx]
                check_date = pd.to_datetime(schedule_df.iloc[check_day_idx]['Date']).date()
                
                if start_date <= check_date <= end_date:
                    # Check if this is a Saturday
                    if check_date.weekday() == 5:  # Saturday
                        if isinstance(intern_assigned[check_idx][i], cp_model.IntVar):
                            saturday_assignments.append(intern_assigned[check_idx][i])
                elif check_date > end_date:
                    break  # Days are sorted, so we can break early
            
            # Apply Saturday cap constraint to this 4-week window (max 2 Saturdays)
            if len(saturday_assignments) > 2:
                model.Add(sum(saturday_assignments) <= 2)
                saturday_cap_constraints += 1
    
    logging.info(f"Applied Saturday cap of 2 per 4-week period, created {saturday_cap_constraints} window constraints")
    
    # Fairness optimization with day-type specificity
    soft_obj = sum(soft_violations) if soft_violations else 0
    
    # Total count fairness
    if len(intern_total_counts) > 1:
        max_total = model.NewIntVar(0, n_intern_days, 'max_total')
        min_total = model.NewIntVar(0, n_intern_days, 'min_total')
        model.AddMaxEquality(max_total, intern_total_counts)
        model.AddMinEquality(min_total, intern_total_counts)
        total_fairness_obj = max_total - min_total
    else:
        total_fairness_obj = 0

    # Weekday count fairness
    if len(intern_weekday_counts) > 1:
        max_weekday = model.NewIntVar(0, n_intern_days, 'max_weekday')
        min_weekday = model.NewIntVar(0, n_intern_days, 'min_weekday')
        model.AddMaxEquality(max_weekday, intern_weekday_counts)
        model.AddMinEquality(min_weekday, intern_weekday_counts)
        weekday_fairness_obj = max_weekday - min_weekday
    else:
        weekday_fairness_obj = 0

    # Saturday count fairness
    if len(intern_saturday_counts) > 1:
        max_saturday = model.NewIntVar(0, n_intern_days, 'max_saturday')
        min_saturday = model.NewIntVar(0, n_intern_days, 'min_saturday')
        model.AddMaxEquality(max_saturday, intern_saturday_counts)
        model.AddMinEquality(min_saturday, intern_saturday_counts)
        saturday_fairness_obj = max_saturday - min_saturday
    else:
        saturday_fairness_obj = 0

    # Balanced objective function with day-type fairness
    consecutive_penalty_obj = sum(consecutive_soft_penalties) if consecutive_soft_penalties else 0
    
    if any([total_fairness_obj != 0, weekday_fairness_obj != 0, saturday_fairness_obj != 0, soft_obj != 0, consecutive_penalty_obj != 0]):
        model.Minimize(
            5 * total_fairness_obj +     # Total fairness
            3 * weekday_fairness_obj +   # Weekday fairness
            3 * saturday_fairness_obj +  # Saturday fairness  
            20 * soft_obj +              # High-priority soft constraints
            2 * consecutive_penalty_obj  # Consecutive assignment penalty (moderate weight)
        )
    
    logging.info(f"Objective: 5*total + 3*weekday + 3*saturday + 20*soft_violations + 2*consecutive_penalties")
    
    # Solve
    solver = cp_model.CpSolver()
    status = solver.Solve(model)
    
    # Log solver status
    if status == cp_model.OPTIMAL:
        logging.info("Intern assignment optimization: OPTIMAL solution found")
    elif status == cp_model.FEASIBLE:
        logging.info("Intern assignment optimization: FEASIBLE solution found")
    else:
        logging.warning(f"Intern assignment optimization failed with status: {status}")
    
    # Extract solution
    schedule_df = schedule_df.copy()
    schedule_df['Intern'] = None
    
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for d_idx, day_idx in enumerate(intern_days):
            for i, intern_name in enumerate(intern_names):
                if isinstance(intern_assigned[d_idx][i], cp_model.IntVar):
                    if solver.Value(intern_assigned[d_idx][i]):
                        schedule_df.at[day_idx, 'Intern'] = intern_name
                        break
    
    # SIMPLIFIED: Build basic fairness summary
    intern_counts = {name: 0 for name in intern_names}
    intern_weekday = {name: 0 for name in intern_names}  
    intern_saturday = {name: 0 for name in intern_names}
    
    for idx, row in schedule_df.iterrows():
        intern = row.get('Intern')
        if intern in intern_names:
            date = pd.to_datetime(row['Date'])
            weekday = date.weekday()
            intern_counts[intern] += 1
            if weekday < 5:
                intern_weekday[intern] += 1
            elif weekday == 5:
                intern_saturday[intern] += 1
    
    fairness_data = []
    for name in intern_names:
        fairness_data.append({
            'Resident': name,
            'Total': intern_counts[name],
            'Weekday': intern_weekday[name],
            'Saturday': intern_saturday[name]
        })
    
    intern_fairness_df = pd.DataFrame(fairness_data)
    objective_value = solver.ObjectiveValue() if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None
    
    return schedule_df, intern_fairness_df, objective_value

def assign_interns(schedule_df, residents, pgy_levels, hard_constraints, soft_constraints):
    """
    DEPRECATED: Use optimize_intern_assignments instead for better optimization.
    This function uses a simple greedy algorithm.
    """
    return optimize_intern_assignments(schedule_df, residents, pgy_levels, hard_constraints, soft_constraints, None, None)

def assign_supervisors(schedule_df, residents, pgy_levels, hard_constraints, soft_constraints, holidays=None):
    """
    Assign supervisors (PGY3/4) to each day a PGY2 is on call (except Sundays and holidays).
    - Saturday call resident is the Friday supervisor.
    - No one can be supervisor the day after being on call.
    - Apply hard constraints and only 'Non-call request' soft constraints.
    - Spread assignments fairly.
    - Adds a 'Supervisor' column to schedule_df.
    - Returns updated DataFrame.
    """
    schedule_df = schedule_df.copy()
    schedule_df['Supervisor'] = None
    if holidays is None:
        holidays = []
    holiday_dates = set(pd.to_datetime(h['date']).date() for h in holidays if 'date' in h)
    # Identify eligible supervisors
    supervisor_indices = [i for i, pgy in enumerate(pgy_levels) if pgy in [3, 4]]
    supervisor_names = [residents[i] for i in supervisor_indices]
    # Build hard constraint lookup
    hard_lookup = defaultdict(set)
    for name in supervisor_names:
        for rng in hard_constraints.get(name, []):
            start, end = rng
            days = pd.date_range(start, end)
            for d in days:
                hard_lookup[name].add(pd.to_datetime(d).date())
    # Build soft constraint lookup (only Non-call request)
    soft_lookup = defaultdict(set)
    for name in supervisor_names:
        for sc in soft_constraints.get(name, []):
            if len(sc) == 2:
                start, end = sc
                priority = "Rotation/Lecture"
            else:
                start, end, priority = sc
            if priority != "Non-call request":
                continue
            days = pd.date_range(start, end)
            for d in days:
                soft_lookup[name].add(pd.to_datetime(d).date())
    # Track supervisor assignments
    supervisor_counts = {name: 0 for name in supervisor_names}
    # Track who was on call the previous day
    prev_call = None
    prev_supervisor = None
    for idx, row in schedule_df.iterrows():
        date = pd.to_datetime(row['Date']).date()
        weekday = pd.to_datetime(row['Date']).weekday()
        call = row['Call']
        call_pgy = None
        if call in residents:
            call_pgy = pgy_levels[residents.index(call)]
        # Skip Sundays and holidays
        if weekday == 6 or date in holiday_dates:
            prev_call = call
            prev_supervisor = None
            continue
        # Only assign supervisor if PGY2 is on call
        if call_pgy != 2:
            prev_call = call
            prev_supervisor = None
            continue
        # Friday: assign Saturday call resident as supervisor
        if weekday == 4:
            # Find Saturday row
            sat_idx = idx + 1 if idx + 1 < len(schedule_df) else None
            if sat_idx is not None:
                sat_row = schedule_df.iloc[sat_idx]
                sat_call = sat_row['Call']
                sat_call_pgy = None
                if sat_call in residents:
                    sat_call_pgy = pgy_levels[residents.index(sat_call)]
                if sat_call in supervisor_names and sat_call_pgy in [3, 4]:
                    # Check hard/soft constraints for Friday
                    if date not in hard_lookup[sat_call] and date not in soft_lookup[sat_call]:
                        schedule_df.at[idx, 'Supervisor'] = sat_call
                        supervisor_counts[sat_call] += 1
                        prev_supervisor = sat_call
                        prev_call = call
                        continue
            # If not eligible, fall through to normal assignment
        # Build eligible supervisors
        eligible = [name for name in supervisor_names if name != call and name != prev_call and date not in hard_lookup[name]]
        # Prefer those not violating soft constraints
        eligible_no_soft = [name for name in eligible if date not in soft_lookup[name]]
        pool = eligible_no_soft if eligible_no_soft else eligible
        if pool:
            # Sort by fewest supervisor assignments
            pool.sort(key=lambda n: supervisor_counts[n])
            chosen = pool[0]
            schedule_df.at[idx, 'Supervisor'] = chosen
            supervisor_counts[chosen] += 1
            prev_supervisor = chosen
        else:
            prev_supervisor = None
        prev_call = call
    return schedule_df 