import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import calendar
import engine
import run_formatter
import io
import os
import time

# Set page config
st.set_page_config(
    page_title="Resident Call Schedule Generator",
    page_icon="🏥",
    layout="wide"
)

# Initialize session state for storing data
if 'residents_df' not in st.session_state:
    st.session_state.residents_df = pd.DataFrame(columns=['Name', 'PGY', 'Max Consecutive Days', 'Required Days Off'])
if 'schedule_df' not in st.session_state:
    st.session_state.schedule_df = pd.DataFrame(columns=['Date', 'Call', 'Backup', 'Intern', 'Supervisor'])

# --- Block Date Selection at the Top ---
st.markdown("<h1 style='text-align: left;'>Kall Scheduler Kuhnel (KSK)</h1>", unsafe_allow_html=True)

col1, col2 = st.columns(2)
with col1:
    block_start = st.date_input("Block Start Date", datetime.now())
with col2:
    block_end = st.date_input("Block End Date", datetime.now() + timedelta(days=120))

# Add PGY-4 call cap input
pgy4_call_cap = st.number_input("Desired PGY-4 call cap for this block:", min_value=0, max_value=10, value=2)

# Add intern call cap input
intern_call_cap = st.number_input("Maximum intern assignments per intern per rotation period:", min_value=0, max_value=20, value=4, help="Prevents any single intern from being assigned more than this number of calls per rotation period (will use 4-week periods if no rotation periods are defined)")

# --- Tabs as per user screenshot ---
tab_names = [
    "Residents",
    "Holiday Assignments",
    "Hard Constraints",
    "Soft Constraints",
    "Previous Block",
    "Block Transition",
    "Rotation Periods",
    "Constraint Analysis",
    "Developer Settings",
    "Generate & Review"
]
tabs = st.tabs(tab_names)

# Tab 1: Resident Information
with tabs[0]:
    st.header("Resident Information")
    
    # File upload for resident information
    uploaded_file = st.file_uploader("Upload Resident Information (CSV)", type=['csv'])
    
    if uploaded_file is not None:
        st.session_state.residents_df = pd.read_csv(uploaded_file)

    # Editable residents table
    edited_df = st.data_editor(
        st.session_state.residents_df,
        num_rows="dynamic",
        use_container_width=True,
        key="residents_editor"
    )
    st.session_state.residents_df = edited_df

    # Remove resident functionality
    if not st.session_state.residents_df.empty:
        remove_name = st.selectbox(
            "Select a resident to remove:",
            options=st.session_state.residents_df['Name'].tolist()
        )
        if st.button("Remove Selected Resident"):
            st.session_state.residents_df = st.session_state.residents_df[st.session_state.residents_df['Name'] != remove_name].reset_index(drop=True)
            st.success("Resident removed.")

    # Manual resident entry
    st.subheader("Add Resident Manually")
    col1, col2 = st.columns(2)
    
    with col1:
        name = st.text_input("Name")
    with col2:
        pgy = st.selectbox("PGY", [1, 2, 3, 4, 5])
    
    if st.button("Add Resident"):
        new_resident = pd.DataFrame({
            'Name': [name],
            'PGY': [pgy]
        })
        st.session_state.residents_df = pd.concat([st.session_state.residents_df, new_resident], ignore_index=True)
        st.success("Resident added successfully!")

# Tab 2: Holiday Assignments
with tabs[1]:
    st.header("Holiday Call Assignments")
    st.info("🔓 **Manual Override Mode:** Holiday assignments bypass ALL scheduling constraints including PGY level matching, hard constraints (PTO/rotations), and soft constraints. Use this to assign any call/backup combination as needed for special circumstances.")

    # Initialize holidays in session state
    if 'holidays' not in st.session_state:
        st.session_state.holidays = []
    if 'disable_holidays' not in st.session_state:
        st.session_state.disable_holidays = False
    if 'holidays_processed' not in st.session_state:
        st.session_state.holidays_processed = False

    # Upload CSV for holidays
    uploaded_holidays_csv = st.file_uploader("Upload Holidays CSV", type=['csv'], key="holidays_csv")
    if uploaded_holidays_csv is not None and not st.session_state.holidays_processed:
        df = pd.read_csv(uploaded_holidays_csv)
        # Create a copy of existing holidays
        existing_holidays = st.session_state.holidays.copy()
        # Merge CSV data with existing holidays
        for _, row in df.iterrows():
            holiday = {
                'name': str(row['Name']).strip(),
                'date': pd.to_datetime(row['Date']).date(),
                'call': str(row['Call']).strip(),
                'backup': str(row['Backup']).strip()
            }
            existing_holidays.append(holiday)
        # Update session state with merged holidays
        st.session_state.holidays = existing_holidays
        st.session_state.holidays_processed = True
        st.rerun()
    
    # Reset processed flag when no file is uploaded
    if uploaded_holidays_csv is None and st.session_state.holidays_processed:
        st.session_state.holidays_processed = False

    # Disable holidays checkbox
    st.session_state.disable_holidays = st.checkbox("Disable Holiday Assignments", value=st.session_state.disable_holidays)

    if not st.session_state.disable_holidays:
        # Get PGY2 residents for dropdowns
        all_residents = st.session_state.residents_df['Name'].tolist()

        # Add new holiday
        if st.button("Add Another Holiday") or len(st.session_state.holidays) == 0:
            if len(st.session_state.holidays) == 0 or st.session_state.holidays[-1].get('name', '') != '':
                st.session_state.holidays.append({'name': '', 'date': None, 'call': '', 'backup': ''})

        # Display each holiday
        to_remove = []
        for idx, holiday in enumerate(st.session_state.holidays):
            st.markdown(f"### Holiday #{idx+1}")
            cols = st.columns([2, 2, 2, 2, 1])
            with cols[0]:
                holiday['name'] = st.text_input(f"Holiday Name", value=holiday['name'], key=f"holiday_name_{idx}")
            with cols[1]:
                holiday['date'] = st.date_input(f"Date", value=holiday['date'] or None, key=f"holiday_date_{idx}")
            with cols[2]:
                holiday['call'] = st.selectbox(f"Call Assignment", options=[''] + all_residents, index=([''] + all_residents).index(holiday['call']) if holiday['call'] in all_residents else 0, key=f"holiday_call_{idx}")
            with cols[3]:
                holiday['backup'] = st.selectbox(f"Backup Assignment", options=[''] + all_residents, index=([''] + all_residents).index(holiday['backup']) if holiday['backup'] in all_residents else 0, key=f"holiday_backup_{idx}")
            with cols[4]:
                if st.button("❌", key=f"remove_holiday_{idx}"):
                    to_remove.append(idx)
            st.session_state.holidays[idx] = holiday

        # Remove holidays marked for deletion
        for idx in sorted(to_remove, reverse=True):
            st.session_state.holidays.pop(idx)

        # Download Holidays as CSV
        if st.button("Download Holidays as CSV"):
            rows = []
            for holiday in st.session_state.holidays:
                if holiday.get('name') and holiday.get('date'):
                    rows.append({
                        "Name": holiday['name'],
                        "Date": holiday['date'],
                        "Call": holiday.get('call', ''),
                        "Backup": holiday.get('backup', '')
                    })
            if rows:
                df = pd.DataFrame(rows)
                csv_buffer = io.StringIO()
                df.to_csv(csv_buffer, index=False)
                st.download_button(
                    label="Download CSV",
                    data=csv_buffer.getvalue(),
                    file_name="holidays.csv",
                    mime="text/csv"
                )
            else:
                st.warning("No holidays to download. Please add some holidays first.")

# Tab 3: Hard Constraints
with tabs[2]:
    st.header("Hard Constraints")
    st.info("Use this section to specify PTO requests and rotation blocks where residents cannot be assigned call. These are high-priority constraints that will be strictly enforced in the schedule.")

    # Initialize hard constraints in session state
    if 'hard_constraints' not in st.session_state:
        st.session_state.hard_constraints = {}
    if 'hard_constraints_processed' not in st.session_state:
        st.session_state.hard_constraints_processed = False

    # Upload CSV for hard constraints
    uploaded_hard_csv = st.file_uploader("Upload Hard Constraints CSV", type=['csv'], key="hard_constraints_csv")
    if uploaded_hard_csv is not None and not st.session_state.hard_constraints_processed:
        df = pd.read_csv(uploaded_hard_csv)
        # Create a copy of existing constraints
        existing_constraints = st.session_state.hard_constraints.copy()
        # Merge CSV data with existing constraints
        for _, row in df.iterrows():
            resident = str(row['Resident']).strip()
            start = pd.to_datetime(row['Start_Date']).date()
            end = pd.to_datetime(row['End_Date']).date()
            if resident not in existing_constraints:
                existing_constraints[resident] = []
            existing_constraints[resident].append((start, end))
        # Update session state with merged constraints
        st.session_state.hard_constraints = existing_constraints
        st.session_state.hard_constraints_processed = True
        st.rerun()
    
    # Reset processed flag when no file is uploaded
    if uploaded_hard_csv is None and st.session_state.hard_constraints_processed:
        st.session_state.hard_constraints_processed = False

    # Get all residents
    residents = [str(name).strip() for name in st.session_state.residents_df['Name'].tolist()]

    # For each resident, show their constraints and allow adding/removing
    for resident in residents:
        norm_resident = resident.replace(' ', '_').lower()
        st.subheader(f"{resident} Hard Constraints")
        constraints = st.session_state.hard_constraints.get(resident, [])
        
        # Display current constraints (editable)
        for idx, (start, end) in enumerate(constraints):
            cols = st.columns([2, 2, 1])
            with cols[0]:
                new_start = st.date_input("Start Date", value=start, key=f"hard_{norm_resident}_start_{idx}_{st.session_state.get('constraint_session_id', 'default')}")
            with cols[1]:
                new_end = st.date_input("End Date", value=end, key=f"hard_{norm_resident}_end_{idx}_{st.session_state.get('constraint_session_id', 'default')}")
            with cols[2]:
                if st.button("❌", key=f"remove_hard_{norm_resident}_{idx}_{st.session_state.get('constraint_session_id', 'default')}"):
                    st.session_state.hard_constraints[resident].pop(idx)
                    st.rerun()
            # Update session state if changed
            if (new_start, new_end) != (start, end):
                st.session_state.hard_constraints[resident][idx] = (new_start, new_end)

        # Add new constraint
        with st.expander(f"Add Another Hard Constraint for {resident}"):
            add_cols = st.columns([2, 2, 1])
            with add_cols[0]:
                new_start = st.date_input(f"New Start Date for {resident}", 
                    key=f"hard_new_start_{norm_resident}_{st.session_state.get('constraint_session_id', 'default')}", 
                    value=None)
            with add_cols[1]:
                new_end = st.date_input(f"New End Date for {resident}", 
                    key=f"hard_new_end_{norm_resident}_{st.session_state.get('constraint_session_id', 'default')}", 
                    value=None)
            with add_cols[2]:
                if st.button(f"Add", key=f"hard_add_{norm_resident}_{st.session_state.get('constraint_session_id', 'default')}"):
                    if new_start and new_end:
                        if resident not in st.session_state.hard_constraints:
                            st.session_state.hard_constraints[resident] = []
                        st.session_state.hard_constraints[resident].append((new_start, new_end))
                        # Generate new session ID to force UI refresh
                        st.session_state['constraint_session_id'] = str(time.time())
                        st.rerun()

    # Save button
    st.button("Save Hard Constraints")

    # Remove Duplicates button
    if st.button("🧹 Remove Duplicate Hard Constraints"):
        duplicates_removed = 0
        for resident in st.session_state.hard_constraints:
            original_count = len(st.session_state.hard_constraints[resident])
            # Convert to set to remove duplicates, then back to list
            unique_constraints = list(set(st.session_state.hard_constraints[resident]))
            st.session_state.hard_constraints[resident] = unique_constraints
            duplicates_removed += original_count - len(unique_constraints)
        
        if duplicates_removed > 0:
            st.success(f"✅ Removed {duplicates_removed} duplicate hard constraint(s)")
        else:
            st.info("ℹ️ No duplicate hard constraints found")
        st.rerun()

    # Download Hard Constraints as CSV
    if st.button("Download Hard Constraints as CSV"):
        rows = []
        for resident, constraints in st.session_state.hard_constraints.items():
            for constraint in constraints:
                start, end = constraint
                rows.append({
                    "Resident": resident,
                    "Start_Date": start,
                    "End_Date": end
                })
        df = pd.DataFrame(rows)
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        st.download_button(
            label="Download CSV",
            data=csv_buffer.getvalue(),
            file_name="hard_constraints.csv",
            mime="text/csv"
        )

# Tab 4: Soft Constraints
with tabs[3]:
    st.header("Soft Constraints")
    st.info("Use this section to specify soft constraints that may be considered during schedule generation.")

    # Initialize soft constraints in session state
    if 'soft_constraints' not in st.session_state:
        st.session_state.soft_constraints = {}
    if 'soft_constraints_processed' not in st.session_state:
        st.session_state.soft_constraints_processed = False

    # Upload CSV for soft constraints
    uploaded_soft_csv = st.file_uploader("Upload Soft Constraints CSV", type=['csv'], key="soft_constraints_csv")
    if uploaded_soft_csv is not None and not st.session_state.soft_constraints_processed:
        df = pd.read_csv(uploaded_soft_csv)
        # Create a copy of existing constraints
        existing_constraints = st.session_state.soft_constraints.copy()
        # Normalize column names for flexibility
        df.columns = [col.strip().lower() for col in df.columns]
        for _, row in df.iterrows():
            resident = str(row['resident']).strip()
            start = pd.to_datetime(row['start_date']).date()
            end = pd.to_datetime(row['end_date']).date()
            # Accept either 'priority' or 'type of request' (case-insensitive)
            priority = row.get('priority')
            if priority is None and 'type of request' in row:
                priority = row['type of request']
            if priority is None:
                priority = "Rotation/Lecture"
            priority = str(priority).strip()
            if resident not in existing_constraints:
                existing_constraints[resident] = []
            existing_constraints[resident].append((start, end, priority))
        # Update session state with merged constraints
        st.session_state.soft_constraints = existing_constraints
        st.session_state.soft_constraints_processed = True
        st.rerun()
    
    # Reset processed flag when no file is uploaded
    if uploaded_soft_csv is None and st.session_state.soft_constraints_processed:
        st.session_state.soft_constraints_processed = False

    # Get all residents
    residents = [str(name).strip() for name in st.session_state.residents_df['Name'].tolist()]

    # For each resident, show their constraints and allow adding/removing
    for resident in residents:
        norm_resident = resident.replace(' ', '_').lower()
        st.subheader(f"{resident} Soft Constraints")
        constraints = st.session_state.soft_constraints.get(resident, [])
        # Display current constraints (editable)
        for idx, constraint in enumerate(constraints):
            # Support both old (start, end) and new (start, end, priority) formats
            if len(constraint) == 2:
                start, end = constraint
                priority = "Rotation/Lecture"
            else:
                start, end, priority = constraint
            cols = st.columns([2, 2, 2, 1])
            with cols[0]:
                new_start = st.date_input("Start Date", value=start, key=f"soft_{norm_resident}_start_{idx}_{st.session_state.get('constraint_session_id', 'default')}")
            with cols[1]:
                new_end = st.date_input("End Date", value=end, key=f"soft_{norm_resident}_end_{idx}_{st.session_state.get('constraint_session_id', 'default')}")
            with cols[2]:
                new_priority = st.selectbox(
                    "Priority", ["Non-call request", "Rotation/Lecture"],
                    index=["Non-call request", "Rotation/Lecture"].index(priority),
                    key=f"soft_{norm_resident}_priority_{idx}_{st.session_state.get('constraint_session_id', 'default')}"
                )
            with cols[3]:
                if st.button("❌", key=f"remove_soft_{norm_resident}_{idx}_{st.session_state.get('constraint_session_id', 'default')}"):
                    st.session_state.soft_constraints[resident].pop(idx)
                    st.rerun()
            # Update session state if changed
            if (new_start, new_end, new_priority) != (start, end, priority):
                st.session_state.soft_constraints[resident][idx] = (new_start, new_end, new_priority)

        # Add new constraint
        with st.expander(f"Add Another Soft Constraint for {resident}"):
            add_cols = st.columns([2, 2, 2, 1])
            with add_cols[0]:
                new_start = st.date_input(f"New Start Date for {resident}", 
                    key=f"soft_new_start_{norm_resident}_{st.session_state.get('constraint_session_id', 'default')}", 
                    value=None)
            with add_cols[1]:
                new_end = st.date_input(f"New End Date for {resident}", 
                    key=f"soft_new_end_{norm_resident}_{st.session_state.get('constraint_session_id', 'default')}", 
                    value=None)
            with add_cols[2]:
                new_priority = st.selectbox(
                    "Priority", ["Non-call request", "Rotation/Lecture"],
                    index=1,
                    key=f"soft_new_priority_{norm_resident}_{st.session_state.get('constraint_session_id', 'default')}"
                )
            with add_cols[3]:
                if st.button(f"Add", key=f"soft_add_{norm_resident}_{st.session_state.get('constraint_session_id', 'default')}"):
                    if new_start and new_end:
                        if resident not in st.session_state.soft_constraints:
                            st.session_state.soft_constraints[resident] = []
                        st.session_state.soft_constraints[resident].append((new_start, new_end, new_priority))
                        # Generate new session ID to force UI refresh
                        st.session_state['constraint_session_id'] = str(time.time())
                        st.rerun()
        # Bulk add repeating days
        with st.expander(f"Bulk Add Repeating Days for {resident}"):
            bulk_cols = st.columns([2, 2, 2, 2, 1])
            with bulk_cols[0]:
                bulk_start = st.date_input(f"Bulk Start Date for {resident}", key=f"soft_bulk_start_{norm_resident}_{st.session_state.get('constraint_session_id', 'default')}", value=None)
            with bulk_cols[1]:
                bulk_end = st.date_input(f"Bulk End Date for {resident}", key=f"soft_bulk_end_{norm_resident}_{st.session_state.get('constraint_session_id', 'default')}", value=None)
            with bulk_cols[2]:
                bulk_dow = st.selectbox(
                    "Day of Week",
                    ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
                    key=f"soft_bulk_dow_{norm_resident}_{st.session_state.get('constraint_session_id', 'default')}"
                )
            with bulk_cols[3]:
                bulk_priority = st.selectbox(
                    "Priority",
                    ["Non-call request", "Rotation/Lecture"],
                    index=1,
                    key=f"soft_bulk_priority_{norm_resident}_{st.session_state.get('constraint_session_id', 'default')}"
                )
            with bulk_cols[4]:
                if st.button(f"Add Bulk", key=f"soft_bulk_add_{norm_resident}_{st.session_state.get('constraint_session_id', 'default')}"):
                    if bulk_start and bulk_end:
                        import pandas as pd
                        days = list(pd.date_range(start=bulk_start, end=bulk_end))
                        dow_idx = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].index(bulk_dow)
                        for d in days:
                            if d.weekday() == dow_idx:
                                if resident not in st.session_state.soft_constraints:
                                    st.session_state.soft_constraints[resident] = []
                                st.session_state.soft_constraints[resident].append((d.date(), d.date(), bulk_priority))
                        st.rerun()
    # Save button
    st.button("Save Soft Constraints")

    # Remove Duplicates button
    if st.button("🧹 Remove Duplicate Constraints"):
        duplicates_removed = 0
        for resident in st.session_state.soft_constraints:
            original_count = len(st.session_state.soft_constraints[resident])
            # Convert to set to remove duplicates, then back to list
            unique_constraints = list(set(st.session_state.soft_constraints[resident]))
            st.session_state.soft_constraints[resident] = unique_constraints
            duplicates_removed += original_count - len(unique_constraints)
        
        if duplicates_removed > 0:
            st.success(f"✅ Removed {duplicates_removed} duplicate constraint(s)")
        else:
            st.info("ℹ️ No duplicate constraints found")
        st.rerun()

    # Download Soft Constraints as CSV
    if st.button("Download Soft Constraints as CSV"):
        rows = []
        for resident, constraints in st.session_state.soft_constraints.items():
            for constraint in constraints:
                if len(constraint) == 2:
                    start, end = constraint
                    priority = "Rotation/Lecture"
                else:
                    start, end, priority = constraint
                rows.append({
                    "Resident": resident,
                    "Start_Date": start,
                    "End_Date": end,
                    "Priority": priority
                })
        df = pd.DataFrame(rows)
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        st.download_button(
            label="Download CSV",
            data=csv_buffer.getvalue(),
            file_name="soft_constraints.csv",
            mime="text/csv"
        )

# Tab 5: Previous Block
with tabs[4]:
    st.header("Previous Block")
    st.info("Upload the running total CSV from the previous block to enable inter-block fairness optimization. This will help balance out any imbalances from previous blocks.")
    
    # Show expected CSV format
    with st.expander("📋 Expected CSV Format"):
        st.markdown("""
        Upload the **running total CSV** from the previous block's download. This file already contains cumulative totals from all previous blocks.
        
        **Expected columns:**
        - `Resident`, `Call_Weekday`, `Call_Friday`, `Call_Saturday`, `Call_Sunday`, `Call_Total`
        - `Backup_Weekday`, `Backup_Friday`, `Backup_Saturday`, `Backup_Sunday`, `Backup_Total`
        - `Intern_Weekday`, `Intern_Saturday`, `Intern_Total`
        
        **How to get this data:**
        1. Generate a schedule for Block 1 → Download the Excel file
        2. For Block 2: Upload Block 1's running total CSV here
        3. For Block 3: Upload Block 2's running total CSV here (already includes Block 1 + Block 2)
        4. And so on...
        
        **Note:** Each block's download already contains the cumulative running totals, so you only need to upload one file.
        """)
    
    # Initialize previous block data in session state
    if 'previous_block_data' not in st.session_state:
        st.session_state.previous_block_data = None
    if 'previous_block_processed' not in st.session_state:
        st.session_state.previous_block_processed = False
    
    # File upload for previous block data
    uploaded_previous_csv = st.file_uploader("Upload Previous Block Running Totals (CSV)", type=['csv'], key="previous_block_csv")
    
    # Process uploaded CSV
    if uploaded_previous_csv is not None and not st.session_state.previous_block_processed:
        try:
            df = pd.read_csv(uploaded_previous_csv)
            
            # Expected columns for the new format
            expected_columns = [
                'Resident', 'Call_Weekday', 'Call_Friday', 'Call_Saturday', 'Call_Sunday', 'Call_Total',
                'Backup_Weekday', 'Backup_Friday', 'Backup_Saturday', 'Backup_Sunday', 'Backup_Total',
                'Intern_Weekday', 'Intern_Saturday', 'Intern_Total'
            ]
            
            # Check if all expected columns are present
            missing_columns = [col for col in expected_columns if col not in df.columns]
            
            if len(missing_columns) == 0:
                # All expected columns present - use data as-is
                # Add missing intern columns with 0 values for engine compatibility
                processed_df = df[expected_columns].copy()
                processed_df['Intern_Friday'] = 0
                processed_df['Intern_Sunday'] = 0
                
                st.session_state.previous_block_data = processed_df
                st.session_state.previous_block_processed = True
                st.success("Previous block running totals loaded successfully!")
                st.rerun()
            else:
                st.error(f"""
Invalid CSV format. Missing columns: {missing_columns}

Expected columns:
- Resident, Call_Weekday, Call_Friday, Call_Saturday, Call_Sunday, Call_Total
- Backup_Weekday, Backup_Friday, Backup_Saturday, Backup_Sunday, Backup_Total  
- Intern_Weekday, Intern_Saturday, Intern_Total

Your CSV has columns: {list(df.columns)}
""")
        except Exception as e:
            st.error(f"Error loading CSV: {str(e)}")
            st.error("Please ensure you're uploading a valid running totals CSV.")
    
    # Reset processed flag when no file is uploaded
    if uploaded_previous_csv is None and st.session_state.previous_block_processed:
        st.session_state.previous_block_processed = False
        st.session_state.previous_block_data = None
    
    # Display previous block data if loaded
    if st.session_state.previous_block_data is not None:
        st.subheader("Previous Block Summary")
        
        # Show summary statistics
        df = st.session_state.previous_block_data
        
        # Calculate totals by day type across all residents
        total_calls = {
            'Weekday': df['Call_Weekday'].sum(),
            'Friday': df['Call_Friday'].sum(),
            'Saturday': df['Call_Saturday'].sum(),
            'Sunday': df['Call_Sunday'].sum(),
            'Total': df['Call_Total'].sum()
        }
        
        total_backups = {
            'Weekday': df['Backup_Weekday'].sum(),
            'Friday': df['Backup_Friday'].sum(),
            'Saturday': df['Backup_Saturday'].sum(),
            'Sunday': df['Backup_Sunday'].sum(),
            'Total': df['Backup_Total'].sum()
        }
        
        total_interns = {
            'Weekday': df['Intern_Weekday'].sum(),
            'Friday': df['Intern_Friday'].sum(),
            'Saturday': df['Intern_Saturday'].sum(),
            'Sunday': df['Intern_Sunday'].sum(),
            'Total': df['Intern_Total'].sum()
        }
        
        # Display summary
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Call Assignments", total_calls['Total'])
            st.metric("Weekday Calls", total_calls['Weekday'])
            st.metric("Friday Calls", total_calls['Friday'])
        with col2:
            st.metric("Total Backup Assignments", total_backups['Total'])
            st.metric("Saturday Calls", total_calls['Saturday'])
            st.metric("Sunday Calls", total_calls['Sunday'])
        with col3:
            st.metric("Total Intern Assignments", total_interns['Total'])
            st.metric("Weekend Calls", total_calls['Saturday'] + total_calls['Sunday'])
            st.metric("Weekday vs Weekend Ratio", f"{total_calls['Weekday'] + total_calls['Friday']:.1f} : {total_calls['Saturday'] + total_calls['Sunday']:.1f}")
        
        # Show detailed breakdown by resident
        st.subheader("Previous Block Assignments by Resident")
        st.dataframe(df, use_container_width=True)
        
        # Download button for the loaded data
        if st.button("Download Previous Block Data as CSV"):
            csv_buffer = io.StringIO()
            df.to_csv(csv_buffer, index=False)
            st.download_button(
                label="Download CSV",
                data=csv_buffer.getvalue(),
                file_name="previous_block_data.csv",
                mime="text/csv"
            )
        
        # Clear button
        if st.button("Clear Previous Block Data"):
            st.session_state.previous_block_data = None
            st.session_state.previous_block_processed = False
            st.rerun()
    else:
        st.info("No previous block data loaded. Upload a call distribution CSV to enable inter-block fairness optimization.")
    
    # Summary section
    st.subheader("Inter-Block Fairness Status")
    if st.session_state.previous_block_data is not None:
        st.success("✅ **Inter-block fairness optimization is ENABLED**")
        st.markdown("""
        The scheduling engine will now optimize for fairness across all blocks, not just the current block.
        
        **What this means:**
        - Residents who had fewer calls of certain types in previous blocks will be prioritized
        - The engine will try to balance out any previous imbalances
        - By the end of all blocks, assignments should be more evenly distributed
        
        **Current optimization target:** Minimize the spread between residents' cumulative totals (previous blocks + current block)
        """)
    else:
        st.warning("⚠️ **Inter-block fairness optimization is DISABLED**")
        st.markdown("""
        The scheduling engine will only optimize for fairness within the current block.
        
        **What this means:**
        - Each block is optimized independently
        - Previous block imbalances are not considered
        - Total fairness across the academic year may not be optimal
        
        **To enable:** Upload previous block call distribution data above.
        """)

# Tab 6: Block Transition
with tabs[5]:
    st.header("Block Transition")
    st.info("Specify the last 4 call assignments from the previous block to ensure proper spacing constraints across block boundaries.")
    
    # Initialize block transition data in session state
    if 'block_transition' not in st.session_state:
        st.session_state.block_transition = {
            'day1': {'date': None, 'call': '', 'backup': ''},
            'day2': {'date': None, 'call': '', 'backup': ''},
            'day3': {'date': None, 'call': '', 'backup': ''},
            'day4': {'date': None, 'call': '', 'backup': ''}
        }
    if 'transition_processed' not in st.session_state:
        st.session_state.transition_processed = False
    
    # Get all residents for dropdowns
    all_residents = [''] + st.session_state.residents_df['Name'].tolist()
    
    # Show expected CSV format
    with st.expander("📋 Expected Block Transition CSV Format"):
        st.markdown("""
        The CSV should have exactly 4 rows with the following columns:
        
        **Required Columns:**
        - `Day` - Day identifier (e.g., "Day 1", "Day 2", etc.)
        - `Date` - Date in YYYY-MM-DD format (e.g., "2024-12-28")
        - `Call` - Name of resident on call (must match names in Residents tab)
        - `Backup` - Name of backup resident (must match names in Residents tab)
        
        **Example CSV:**
        ```
        Day,Date,Call,Backup
        Day 1,2024-12-28,Smith,Johnson
        Day 2,2024-12-29,Wilson,Brown
        Day 3,2024-12-30,Davis,Miller
        Day 4,2024-12-31,Taylor,Wilson
        ```
        
        **Notes:**
        - Day 1 = oldest day, Day 4 = most recent day (immediately before current block)
        - Resident names must exactly match those in your Residents tab
        - Dates should be consecutive and in chronological order
        """)
    
    # Upload CSV for block transition
    uploaded_transition_csv = st.file_uploader("Upload Block Transition CSV", type=['csv'], key="transition_csv")
    if uploaded_transition_csv is not None and not st.session_state.transition_processed:
        try:
            df = pd.read_csv(uploaded_transition_csv)
            
            # Validate CSV structure
            required_columns = ['Day', 'Date', 'Call', 'Backup']
            if not all(col in df.columns for col in required_columns):
                st.error(f"Invalid CSV format. Required columns: {', '.join(required_columns)}")
            elif len(df) != 4:
                st.error("CSV must contain exactly 4 rows (Day 1 through Day 4)")
            else:
                # Process and validate the data
                new_transition_data = {}
                valid_data = True
                
                for idx, row in df.iterrows():
                    day_key = f'day{idx + 1}'
                    
                    # Parse date
                    try:
                        date_val = pd.to_datetime(row['Date']).date()
                    except:
                        st.error(f"Invalid date format in row {idx + 1}: {row['Date']}")
                        valid_data = False
                        break
                    
                    # Validate resident names
                    call_name = str(row['Call']).strip()
                    backup_name = str(row['Backup']).strip()
                    
                    if call_name and call_name not in st.session_state.residents_df['Name'].tolist():
                        st.warning(f"Call resident '{call_name}' not found in current resident list")
                    
                    if backup_name and backup_name not in st.session_state.residents_df['Name'].tolist():
                        st.warning(f"Backup resident '{backup_name}' not found in current resident list")
                    
                    new_transition_data[day_key] = {
                        'date': date_val,
                        'call': call_name,
                        'backup': backup_name
                    }
                
                if valid_data:
                    st.session_state.block_transition = new_transition_data
                    st.session_state.transition_processed = True
                    st.success("Block transition data loaded successfully!")
                    st.rerun()
                    
        except Exception as e:
            st.error(f"Error loading CSV: {str(e)}")
    
    # Reset processed flag when no file is uploaded
    if uploaded_transition_csv is None and st.session_state.transition_processed:
        st.session_state.transition_processed = False
    
    st.markdown("### Last 4 Days of Previous Block")
    st.markdown("Enter the call and backup assignments for the last 4 days of the previous block. Day 4 should be the most recent (day immediately before current block starts).")
    
    # Create input rows for last 4 days
    for day_num in range(1, 5):
        day_key = f'day{day_num}'
        day_data = st.session_state.block_transition[day_key]
        
        cols = st.columns([2, 2, 2])
        with cols[0]:
            new_date = st.date_input(
                f"Day {day_num} Date", 
                value=day_data['date'], 
                key=f"transition_date_{day_num}",
                help=f"Date for day {day_num} of previous block (oldest to newest)"
            )
            day_data['date'] = new_date
        
        with cols[1]:
            new_call = st.selectbox(
                f"Day {day_num} Call", 
                options=all_residents,
                index=all_residents.index(day_data['call']) if day_data['call'] in all_residents else 0,
                key=f"transition_call_{day_num}"
            )
            day_data['call'] = new_call
        
        with cols[2]:
            new_backup = st.selectbox(
                f"Day {day_num} Backup", 
                options=all_residents,
                index=all_residents.index(day_data['backup']) if day_data['backup'] in all_residents else 0,
                key=f"transition_backup_{day_num}"
            )
            day_data['backup'] = new_backup
        
        st.session_state.block_transition[day_key] = day_data
    
    # Action buttons
    col1, col2 = st.columns(2)
    
    with col1:
        # Download CSV button
        if st.button("Download Block Transition CSV"):
            # Prepare data for CSV
            csv_data = []
            for day_num in range(1, 5):
                day_key = f'day{day_num}'
                day_data = st.session_state.block_transition[day_key]
                
                if day_data['date'] and day_data['call'] and day_data['backup']:
                    csv_data.append({
                        'Day': f'Day {day_num}',
                        'Date': day_data['date'],
                        'Call': day_data['call'],
                        'Backup': day_data['backup']
                    })
            
            if csv_data:
                csv_df = pd.DataFrame(csv_data)
                csv_buffer = io.StringIO()
                csv_df.to_csv(csv_buffer, index=False)
                st.download_button(
                    label="Download CSV",
                    data=csv_buffer.getvalue(),
                    file_name="block_transition.csv",
                    mime="text/csv"
                )
            else:
                st.warning("No complete transition data to download. Please fill in all fields for at least one day.")
    
    with col2:
        # Clear button
        if st.button("Clear Block Transition Data"):
            st.session_state.block_transition = {
                'day1': {'date': None, 'call': '', 'backup': ''},
                'day2': {'date': None, 'call': '', 'backup': ''},
                'day3': {'date': None, 'call': '', 'backup': ''},
                'day4': {'date': None, 'call': '', 'backup': ''}
            }
            st.rerun()
    
    # Show summary
    st.markdown("### Summary")
    transition_data = []
    for day_num in range(1, 5):
        day_key = f'day{day_num}'
        day_data = st.session_state.block_transition[day_key]
        if day_data['date'] and day_data['call'] and day_data['backup']:
            transition_data.append({
                'Day': f"Day {day_num}",
                'Date': day_data['date'],
                'Call': day_data['call'],
                'Backup': day_data['backup']
            })
    
    if transition_data:
        st.dataframe(pd.DataFrame(transition_data), use_container_width=True)
        st.success(f"✅ {len(transition_data)} transition days configured")
    else:
        st.info("ℹ️ No transition data configured. Spacing constraints will only apply within the current block.")

# Tab 7: Rotation Periods
with tabs[6]:
    st.header("Rotation Periods")
    st.info("Define rotation switch dates to ensure constraints (intern caps, golden weekends) are applied per rotation period rather than arbitrary 4-week windows.")
    
    # Initialize rotation periods in session state
    if 'rotation_periods' not in st.session_state:
        st.session_state.rotation_periods = []
    if 'rotation_processed' not in st.session_state:
        st.session_state.rotation_processed = False
    
    # Show expected CSV format
    with st.expander("📋 Expected Rotation Periods CSV Format"):
        st.markdown("""
        The CSV should contain rotation switch dates with the following columns:
        
        **Required Columns:**
        - `Switch_Date` - Date in YYYY-MM-DD format (e.g., "2025-01-06")
        - `Rotation_Name` - Optional name/identifier for the rotation (e.g., "Rotation 1", "Medicine", etc.)
        
        **Example CSV:**
        ```
        Switch_Date,Rotation_Name
        2025-01-06,Rotation 1
        2025-02-03,Rotation 2
        2025-03-03,Rotation 3
        2025-03-31,Rotation 4
        ```
        
        **Notes:**
        - Switch dates should be in chronological order
        - Switch dates define the START of each rotation period
        - The last rotation extends to the block end date
        - Rotation periods will typically be 4-5 weeks long
        """)
    
    # Upload CSV for rotation periods
    uploaded_rotation_csv = st.file_uploader("Upload Rotation Periods CSV", type=['csv'], key="rotation_csv")
    if uploaded_rotation_csv is not None and not st.session_state.rotation_processed:
        try:
            df = pd.read_csv(uploaded_rotation_csv)
            
            # Validate CSV structure
            required_columns = ['Switch_Date']
            if not all(col in df.columns for col in required_columns):
                st.error(f"Invalid CSV format. Required columns: {', '.join(required_columns)}")
            else:
                # Process and validate the data
                new_rotation_data = []
                valid_data = True
                
                for idx, row in df.iterrows():
                    # Parse date
                    try:
                        switch_date = pd.to_datetime(row['Switch_Date']).date()
                    except:
                        st.error(f"Invalid date format in row {idx + 1}: {row['Switch_Date']}")
                        valid_data = False
                        break
                    
                    # Get rotation name (optional)
                    rotation_name = str(row.get('Rotation_Name', f'Rotation {idx + 1}')).strip()
                    
                    new_rotation_data.append({
                        'switch_date': switch_date,
                        'rotation_name': rotation_name
                    })
                
                if valid_data:
                    # Sort by switch date
                    new_rotation_data.sort(key=lambda x: x['switch_date'])
                    st.session_state.rotation_periods = new_rotation_data
                    st.session_state.rotation_processed = True
                    st.success("Rotation periods loaded successfully!")
                    st.rerun()
                    
        except Exception as e:
            st.error(f"Error loading CSV: {str(e)}")
    
    # Reset processed flag when no file is uploaded
    if uploaded_rotation_csv is None and st.session_state.rotation_processed:
        st.session_state.rotation_processed = False
    
    st.markdown("### Rotation Switch Dates")
    st.markdown("Enter the dates when rotations switch. Each date marks the START of a new rotation period.")
    
    # Add new rotation button
    if st.button("Add Rotation Period") or len(st.session_state.rotation_periods) == 0:
        if len(st.session_state.rotation_periods) == 0 or st.session_state.rotation_periods[-1].get('switch_date') is not None:
            st.session_state.rotation_periods.append({'switch_date': None, 'rotation_name': f'Rotation {len(st.session_state.rotation_periods) + 1}'})
    
    # Display each rotation period
    to_remove = []
    for idx, rotation in enumerate(st.session_state.rotation_periods):
        st.markdown(f"### {rotation.get('rotation_name', f'Rotation {idx + 1}')}")
        cols = st.columns([2, 3, 1])
        
        with cols[0]:
            new_date = st.date_input(
                f"Switch Date", 
                value=rotation.get('switch_date'), 
                key=f"rotation_date_{idx}",
                help="Date when this rotation period starts"
            )
            rotation['switch_date'] = new_date
        
        with cols[1]:
            new_name = st.text_input(
                f"Rotation Name", 
                value=rotation.get('rotation_name', f'Rotation {idx + 1}'), 
                key=f"rotation_name_{idx}",
                help="Optional name for this rotation"
            )
            rotation['rotation_name'] = new_name
        
        with cols[2]:
            if st.button("❌", key=f"remove_rotation_{idx}"):
                to_remove.append(idx)
        
        st.session_state.rotation_periods[idx] = rotation
    
    # Remove rotations marked for deletion
    for idx in sorted(to_remove, reverse=True):
        st.session_state.rotation_periods.pop(idx)
    
    # Action buttons
    col1, col2 = st.columns(2)
    
    with col1:
        # Download CSV button
        if st.button("Download Rotation Periods CSV"):
            # Prepare data for CSV
            csv_data = []
            for rotation in st.session_state.rotation_periods:
                if rotation.get('switch_date'):
                    csv_data.append({
                        'Switch_Date': rotation['switch_date'],
                        'Rotation_Name': rotation.get('rotation_name', '')
                    })
            
            if csv_data:
                csv_df = pd.DataFrame(csv_data)
                csv_buffer = io.StringIO()
                csv_df.to_csv(csv_buffer, index=False)
                st.download_button(
                    label="Download CSV",
                    data=csv_buffer.getvalue(),
                    file_name="rotation_periods.csv",
                    mime="text/csv"
                )
            else:
                st.warning("No rotation periods to download. Please add some rotation periods first.")
    
    with col2:
        # Clear button
        if st.button("Clear All Rotation Periods"):
            st.session_state.rotation_periods = []
            st.rerun()
    
    # Show summary and validation
    st.markdown("### Summary")
    if st.session_state.rotation_periods:
        # Filter valid rotations
        valid_rotations = [r for r in st.session_state.rotation_periods if r.get('switch_date')]
        
        if valid_rotations:
            # Sort by date
            valid_rotations.sort(key=lambda x: x['switch_date'])
            
            # Calculate rotation lengths
            summary_data = []
            for i, rotation in enumerate(valid_rotations):
                start_date = rotation['switch_date']
                
                # End date is either the next rotation's start date or the block end date
                if i + 1 < len(valid_rotations):
                    end_date = valid_rotations[i + 1]['switch_date'] - timedelta(days=1)
                else:
                    end_date = block_end
                
                # Calculate length
                length_days = (end_date - start_date).days + 1
                length_weeks = round(length_days / 7, 1)
                
                summary_data.append({
                    'Rotation': rotation['rotation_name'],
                    'Start Date': start_date,
                    'End Date': end_date,
                    'Length (Days)': length_days,
                    'Length (Weeks)': length_weeks
                })
            
            summary_df = pd.DataFrame(summary_data)
            st.dataframe(summary_df, use_container_width=True)
            
            # Validation
            total_days = sum(summary_data[i]['Length (Days)'] for i in range(len(summary_data)))
            block_days = (block_end - block_start).days + 1
            
            if total_days == block_days:
                st.success(f"✅ {len(valid_rotations)} rotation periods configured covering all {block_days} days")
            else:
                st.warning(f"⚠️ Rotation periods cover {total_days} days, but block has {block_days} days")
            
            # Check for reasonable rotation lengths (4-6 weeks)
            unusual_lengths = [r for r in summary_data if r['Length (Weeks)'] < 3 or r['Length (Weeks)'] > 7]
            if unusual_lengths:
                st.warning(f"⚠️ Some rotations have unusual lengths (not 4-6 weeks): {', '.join([r['Rotation'] for r in unusual_lengths])}")
        else:
            st.info("ℹ️ No valid rotation periods configured. Please add rotation switch dates.")
    else:
        st.info("ℹ️ No rotation periods configured. Constraints will use 4-week rolling windows instead of rotation periods.")

# Tab 8: Constraint Analysis
with tabs[7]:
    st.header("Constraint Analysis")
    st.info("Visual analysis of hard and soft constraint distribution across the block to identify scheduling bottlenecks.")
    
    # Get hard and soft constraints
    hard_constraints = st.session_state.get('hard_constraints', {})
    soft_constraints = st.session_state.get('soft_constraints', {})
    
    if not hard_constraints and not soft_constraints:
        st.warning("⚠️ No constraints found. Add hard or soft constraints in the respective tabs to see the analysis.")
    else:
        # Create date range for the block
        date_range = pd.date_range(start=block_start, end=block_end, freq='D')
        
        # Initialize constraint counts for each day
        daily_counts = {
            'Date': [],
            'Hard_Constraints': [],
            'Soft_NonCall': [],
            'Soft_Rotation': []
        }
        
        # Process each day in the block
        for date in date_range:
            daily_counts['Date'].append(date.date())
            
            hard_count = 0
            soft_noncall_count = 0
            soft_rotation_count = 0
            
            # Count hard constraints for this date
            for resident, constraints in hard_constraints.items():
                for start_date, end_date in constraints:
                    # Parse dates if they're not already date objects
                    if hasattr(start_date, 'date'):
                        start_date = start_date.date()
                    elif isinstance(start_date, str):
                        start_date = pd.to_datetime(start_date).date()
                    
                    if hasattr(end_date, 'date'):
                        end_date = end_date.date()
                    elif isinstance(end_date, str):
                        end_date = pd.to_datetime(end_date).date()
                    
                    if start_date <= date.date() <= end_date:
                        hard_count += 1
            
            # Count soft constraints for this date
            for resident, constraints in soft_constraints.items():
                for constraint in constraints:
                    if len(constraint) == 2:
                        start_date, end_date = constraint
                        priority = "Rotation/Lecture"
                    else:
                        start_date, end_date, priority = constraint
                    
                    # Parse dates if they're not already date objects
                    if hasattr(start_date, 'date'):
                        start_date = start_date.date()
                    elif isinstance(start_date, str):
                        start_date = pd.to_datetime(start_date).date()
                    
                    if hasattr(end_date, 'date'):
                        end_date = end_date.date()
                    elif isinstance(end_date, str):
                        end_date = pd.to_datetime(end_date).date()
                    
                    if start_date <= date.date() <= end_date:
                        if priority == "Non-call request":
                            soft_noncall_count += 1
                        else:
                            soft_rotation_count += 1
            
            daily_counts['Hard_Constraints'].append(hard_count)
            daily_counts['Soft_NonCall'].append(soft_noncall_count)
            daily_counts['Soft_Rotation'].append(soft_rotation_count)
        
        # Create DataFrame for plotting
        chart_df = pd.DataFrame(daily_counts)
        
        # Display summary statistics
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            total_hard = sum(daily_counts['Hard_Constraints'])
            st.metric("Total Hard Constraints", total_hard)
        with col2:
            total_soft_noncall = sum(daily_counts['Soft_NonCall'])
            st.metric("Total Non-call Requests", total_soft_noncall)
        with col3:
            total_soft_rotation = sum(daily_counts['Soft_Rotation'])
            st.metric("Total Rotation/Lecture", total_soft_rotation)
        with col4:
            max_daily = max([h + sn + sr for h, sn, sr in zip(daily_counts['Hard_Constraints'], daily_counts['Soft_NonCall'], daily_counts['Soft_Rotation'])])
            st.metric("Peak Daily Constraints", max_daily)
        
        # Create stacked bar chart using Streamlit's built-in charting
        st.subheader("Daily Constraint Distribution Across Block")
        
        # Prepare data for Streamlit's bar chart (needs to be in the right format)
        chart_display_df = chart_df.set_index('Date')[['Hard_Constraints', 'Soft_NonCall', 'Soft_Rotation']]
        chart_display_df.columns = ['Hard Constraints', 'Non-call Requests', 'Rotation/Lecture']
        
        # Display the stacked bar chart
        st.bar_chart(chart_display_df, height=500)
        
        # Show top constraint days
        chart_df['Total_Constraints'] = chart_df['Hard_Constraints'] + chart_df['Soft_NonCall'] + chart_df['Soft_Rotation']
        top_days = chart_df[chart_df['Total_Constraints'] > 0].nlargest(10, 'Total_Constraints')
        
        if not top_days.empty:
            st.subheader("Top Constraint Days")
            st.info("These are the days with the highest number of constraints - consider avoiding major schedule assignments on these dates.")
            
            # Format the display
            display_df = top_days[['Date', 'Hard_Constraints', 'Soft_NonCall', 'Soft_Rotation', 'Total_Constraints']].copy()
            display_df.columns = ['Date', 'Hard', 'Non-call', 'Rotation', 'Total']
            st.dataframe(display_df, use_container_width=True)
        else:
            st.info("No constraint conflicts found - all days are clear for scheduling.")

# Tab 9: Developer Settings
with tabs[8]:
    st.header("Developer Settings")
    st.info("⚠️ **Advanced users only:** Adjust optimization weights to fine-tune schedule generation priorities.")
    
    # Initialize developer settings in session state
    if 'dev_settings' not in st.session_state:
        st.session_state.dev_settings = {
            'call_fairness_weight': 1.0,
            'backup_fairness_weight': 0.01,
            'non_call_request_weight': 10.0,
            'rotation_lecture_weight': 0.1,
            'golden_weekend_weight': 0.01,
            'rotation_fairness_weight': 0.5,
            'same_weekday_spacing_weight': 0.2,
            'pgy4_thursday_bonus': 0.1,
            'pgy2_wednesday_bonus': 0.05
        }
    
    st.subheader("Fairness Weights")
    col1, col2 = st.columns(2)
    with col1:
        st.session_state.dev_settings['call_fairness_weight'] = st.number_input(
            "Call Fairness Weight",
            min_value=0.0,
            max_value=20.0,
            value=st.session_state.dev_settings['call_fairness_weight'],
            step=0.1,
            help="Weight for call assignment fairness violations (higher = more important)"
        )
    with col2:
        st.session_state.dev_settings['backup_fairness_weight'] = st.number_input(
            "Backup Fairness Weight",
            min_value=0.0,
            max_value=10.0,
            value=st.session_state.dev_settings['backup_fairness_weight'],
            step=0.1,
            help="Weight for backup assignment fairness violations (higher = more important)"
        )
    
    st.subheader("Soft Constraint Weights")
    col1, col2 = st.columns(2)
    with col1:
        st.session_state.dev_settings['non_call_request_weight'] = st.number_input(
            "Non-call Request Weight",
            min_value=0.0,
            max_value=50.0,
            value=st.session_state.dev_settings['non_call_request_weight'],
            step=0.5,
            help="Weight for non-call request violations (higher = more important)"
        )
    with col2:
        st.session_state.dev_settings['rotation_lecture_weight'] = st.number_input(
            "Rotation/Lecture Weight",
            min_value=0.0,
            max_value=5.0,
            value=st.session_state.dev_settings['rotation_lecture_weight'],
            step=0.05,
            help="Weight for rotation/lecture violations (higher = more important)"
        )
    
    st.subheader("Other Penalties")
    col1, col2 = st.columns(2)
    with col1:
        st.session_state.dev_settings['golden_weekend_weight'] = st.number_input(
            "Golden Weekend Weight",
            min_value=0.0,
            max_value=1.0,
            value=st.session_state.dev_settings['golden_weekend_weight'],
            step=0.01,
            help="Weight for golden weekend violations (higher = more important)"
        )
    with col2:
        st.session_state.dev_settings['rotation_fairness_weight'] = st.number_input(
            "Rotation Fairness Weight",
            min_value=0.0,
            max_value=10.0,
            value=st.session_state.dev_settings.get('rotation_fairness_weight', 0.5),
            step=0.1,
            help="Weight for ensuring residents get at least 1 call/backup per rotation (higher = more important)"
        )
    
    col1, col2 = st.columns(2)
    with col1:
        st.session_state.dev_settings['same_weekday_spacing_weight'] = st.number_input(
            "Same-Weekday Spacing Weight",
            min_value=0.0,
            max_value=5.0,
            value=st.session_state.dev_settings.get('same_weekday_spacing_weight', 0.2),
            step=0.1,
            help="Weight for preventing same weekday assignments within 2 weeks (higher = more important)"
        )
    with col2:
        st.write("")  # Placeholder for spacing
    
    st.subheader("Preference Bonuses")
    col1, col2 = st.columns(2)
    with col1:
        st.session_state.dev_settings['pgy4_thursday_bonus'] = st.number_input(
            "PGY4 Thursday Bonus",
            min_value=0.0,
            max_value=1.0,
            value=st.session_state.dev_settings['pgy4_thursday_bonus'],
            step=0.01,
            help="Bonus for assigning PGY4s on Thursdays (higher = stronger preference)"
        )
    with col2:
        st.session_state.dev_settings['pgy2_wednesday_bonus'] = st.number_input(
            "PGY2 Wednesday Bonus",
            min_value=0.0,
            max_value=1.0,
            value=st.session_state.dev_settings['pgy2_wednesday_bonus'],
            step=0.01,
            help="Bonus for assigning PGY2s on Wednesdays (higher = stronger preference)"
        )
    
    # Display current priority table
    st.subheader("Current Priority Table")
    priority_data = {
        'Violation Type': [
            'Non-call request',
            'Call Fairness',
            'Rotation Fairness',
            'Same-Weekday Spacing',
            'Backup Fairness',
            'Rotation/Lecture',
            'Golden Weekend'
        ],
        'Weight': [
            st.session_state.dev_settings['non_call_request_weight'],
            st.session_state.dev_settings['call_fairness_weight'],
            st.session_state.dev_settings.get('rotation_fairness_weight', 0.5),
            st.session_state.dev_settings.get('same_weekday_spacing_weight', 0.2),
            st.session_state.dev_settings['backup_fairness_weight'],
            st.session_state.dev_settings['rotation_lecture_weight'],
            st.session_state.dev_settings['golden_weekend_weight']
        ]
    }
    priority_df = pd.DataFrame(priority_data)
    priority_df = priority_df.sort_values('Weight', ascending=False)
    priority_df['Relative to Call Fairness'] = priority_df['Weight'] / st.session_state.dev_settings['call_fairness_weight']
    st.dataframe(priority_df, use_container_width=True)
    
    # Reset to defaults button
    if st.button("Reset to Defaults"):
        st.session_state.dev_settings = {
            'call_fairness_weight': 1.0,
            'backup_fairness_weight': 0.01,
            'non_call_request_weight': 10.0,
            'rotation_lecture_weight': 0.1,
            'golden_weekend_weight': 0.01,
            'rotation_fairness_weight': 0.5,
            'same_weekday_spacing_weight': 0.2,
            'pgy4_thursday_bonus': 0.1,
            'pgy2_wednesday_bonus': 0.05
        }
        st.rerun()

# Tab 10: Generate & Review
with tabs[9]:
    st.header("Generate & Review")
    st.markdown("""
    <h2>Step 1: Upload all required files in the tabs above.</h2>
    <h2>Step 2: Click the button below to generate your schedule.</h2>
    """, unsafe_allow_html=True)
    generated = False
    if st.button("Generate Schedule"):
        # Get resident names and PGY levels
        residents = st.session_state.residents_df['Name'].tolist()
        pgy_levels = st.session_state.residents_df['PGY'].tolist()
        # Get block start and end dates
        start_date = block_start
        end_date = block_end
        # Prepare holidays for engine
        holidays = []
        for h in st.session_state.get('holidays', []):
            if h.get('name') and h.get('date') and h.get('call') and h.get('backup'):
                holidays.append({
                    'date': h['date'] if isinstance(h['date'], (str, pd.Timestamp)) else h['date'],
                    'call': h['call'],
                    'backup': h['backup']
                })
        # DEBUG: Show hard constraints
        hard_constraints = st.session_state.get('hard_constraints', {})
        # Get developer settings
        dev_settings = st.session_state.get('dev_settings', {})
        
        # Check if previous block data is available for inter-block fairness
        previous_block_data = st.session_state.get('previous_block_data')
        if previous_block_data is not None:
            st.info("🔄 **Inter-block fairness optimization enabled!** Using previous block data to balance assignments across blocks.")
            
            # Show which residents have previous block data
            st.subheader("Previous Block Data Available For:")
            available_residents = previous_block_data['Resident'].tolist()
            current_residents = st.session_state.residents_df['Name'].tolist()
            
            # Check which current residents have previous data
            with_previous = [r for r in current_residents if r in available_residents]
            without_previous = [r for r in current_residents if r not in available_residents]
            
            if with_previous:
                st.success(f"✅ **{len(with_previous)} residents** have previous block data: {', '.join(with_previous)}")
            if without_previous:
                st.warning(f"⚠️ **{len(without_previous)} residents** have no previous block data: {', '.join(without_previous)}")
        else:
            st.info("ℹ️ **Intra-block fairness only.** Upload previous block data to enable inter-block fairness optimization.")
        # Call the OR-Tools engine with custom weights, previous block data, and transition data
        schedule_df, golden_weekends_count, objective_value = engine.generate_ortools_schedule(
            residents, pgy_levels, start_date, end_date, holidays, pgy4_call_cap, 
            hard_constraints, st.session_state.get('soft_constraints', {}),
            dev_settings, st.session_state.get('previous_block_data'),
            st.session_state.get('block_transition', {}),
            st.session_state.get('rotation_periods', [])
        )
        # Assign interns using OR-Tools optimization with fixed weights
        schedule_df, intern_fairness_df, intern_objective_value = engine.optimize_intern_assignments(
            schedule_df, residents, pgy_levels, hard_constraints, 
            st.session_state.get('soft_constraints', {}),
            {'intern_fairness_weight': 1.0, 'intern_soft_constraint_weight': 5.0},  # Fixed weights
            intern_call_cap
        )
        # Assign supervisors
        schedule_df = engine.assign_supervisors(schedule_df, residents, pgy_levels, hard_constraints, st.session_state.get('soft_constraints', {}), holidays)
        st.session_state.schedule_df = schedule_df
        st.session_state.golden_weekends_count = golden_weekends_count
        st.session_state.intern_fairness_df = intern_fairness_df
        st.session_state.objective_value = objective_value
        st.session_state.intern_objective_value = intern_objective_value
        st.success("Schedule generated!")
        if objective_value is not None:
            st.info(f"📊 **Main Optimization Score:** {objective_value:.2f}")
        if intern_objective_value is not None:
            st.info(f"📊 **Intern Optimization Score:** {intern_objective_value:.2f}")
        generated = True
    if not st.session_state.schedule_df.empty:
        generated = True
    # Show sub-tabs if schedule is generated
    if generated:
        subtab_names = [
            "Call Distribution",
            "Running Total",
            "Soft Constraint Results",
            "Golden Weekends",
            "Download"
        ]
        subtabs = st.tabs(subtab_names)
        with subtabs[0]:
            st.header("Call & Backup Distribution (by PGY and Day Type)")
            df = st.session_state.schedule_df.copy()
            if not df.empty:
                df['Date'] = pd.to_datetime(df['Date'])
                # Merge PGY info
                pgy_map = dict(zip(st.session_state.residents_df['Name'], st.session_state.residents_df['PGY']))
                df['PGY'] = df['Call'].map(pgy_map)
                df['Backup_PGY'] = df['Backup'].map(pgy_map)
                df['Weekday'] = df['Date'].dt.weekday
                # Call breakdown
                for pgy in sorted(df['PGY'].dropna().unique()):
                    st.subheader(f"PGY-{int(pgy)}")
                    # Call table
                    call_counts = []
                    for name in st.session_state.residents_df[st.session_state.residents_df['PGY'] == pgy]['Name']:
                        sub = df[df['Call'] == name]
                        weekday = sub[sub['Weekday'].isin([0,1,2,3])].shape[0]
                        friday = sub[sub['Weekday'] == 4].shape[0]
                        saturday = sub[sub['Weekday'] == 5].shape[0]
                        sunday = sub[sub['Weekday'] == 6].shape[0]
                        total = sub.shape[0]
                        call_counts.append({
                            'Resident': name,
                            'Weekday': weekday,
                            'Friday': friday,
                            'Saturday': saturday,
                            'Sunday': sunday,
                            'Total': total
                        })
                    st.dataframe(pd.DataFrame(call_counts))
                    # Backup table
                    backup_counts = []
                    for name in st.session_state.residents_df[st.session_state.residents_df['PGY'] == pgy]['Name']:
                        sub = df[df['Backup'] == name]
                        weekday = sub[sub['Weekday'].isin([0,1,2,3])].shape[0]
                        friday = sub[sub['Weekday'] == 4].shape[0]
                        saturday = sub[sub['Weekday'] == 5].shape[0]
                        sunday = sub[sub['Weekday'] == 6].shape[0]
                        total = sub.shape[0]
                        backup_counts.append({
                            'Resident': name,
                            'Backup_Weekday': weekday,
                            'Backup_Friday': friday,
                            'Backup_Saturday': saturday,
                            'Backup_Sunday': sunday,
                            'Backup_Total': total
                        })
                    st.dataframe(pd.DataFrame(backup_counts))
                # Intern fairness table
                intern_fairness_df = st.session_state.get('intern_fairness_df', pd.DataFrame())
                if not intern_fairness_df.empty:
                    st.subheader("Intern Assignment Fairness")
                    st.dataframe(intern_fairness_df)
        with subtabs[1]:
            st.header("Running Total (All Blocks Combined)")
            st.info("This shows the cumulative assignments across all blocks, including the one just generated.")
            
            # Get current block data
            current_df = st.session_state.schedule_df.copy()
            if not current_df.empty:
                current_df['Date'] = pd.to_datetime(current_df['Date'])
                # Merge PGY info
                pgy_map = dict(zip(st.session_state.residents_df['Name'], st.session_state.residents_df['PGY']))
                current_df['PGY'] = current_df['Call'].map(pgy_map)
                current_df['Backup_PGY'] = current_df['Backup'].map(pgy_map)
                current_df['Weekday'] = current_df['Date'].dt.weekday
                
                # Get previous block data
                previous_df = st.session_state.get('previous_block_data', None)
                
                # Calculate running totals
                for pgy in sorted(current_df['PGY'].dropna().unique()):
                    st.subheader(f"PGY-{int(pgy)} - Running Total")
                    
                    # Initialize running totals
                    running_call_counts = []
                    running_backup_counts = []
                    
                    for name in st.session_state.residents_df[st.session_state.residents_df['PGY'] == pgy]['Name']:
                        # Current block counts
                        current_call = current_df[current_df['Call'] == name]
                        current_backup = current_df[current_df['Backup'] == name]
                        
                        current_weekday = current_call[current_call['Weekday'].isin([0,1,2,3])].shape[0]
                        current_friday = current_call[current_call['Weekday'] == 4].shape[0]
                        current_saturday = current_call[current_call['Weekday'] == 5].shape[0]
                        current_sunday = current_call[current_call['Weekday'] == 6].shape[0]
                        current_total = current_call.shape[0]
                        
                        current_bk_weekday = current_backup[current_backup['Weekday'].isin([0,1,2,3])].shape[0]
                        current_bk_friday = current_backup[current_backup['Weekday'] == 4].shape[0]
                        current_bk_saturday = current_backup[current_backup['Weekday'] == 5].shape[0]
                        current_bk_sunday = current_backup[current_backup['Weekday'] == 6].shape[0]
                        current_bk_total = current_backup.shape[0]
                        
                        # Previous block counts (if available)
                        prev_weekday = 0
                        prev_friday = 0
                        prev_saturday = 0
                        prev_sunday = 0
                        prev_total = 0
                        prev_bk_weekday = 0
                        prev_bk_friday = 0
                        prev_bk_saturday = 0
                        prev_bk_sunday = 0
                        prev_bk_total = 0
                        
                        if previous_df is not None and name in previous_df['Resident'].values:
                            prev_row = previous_df[previous_df['Resident'] == name].iloc[0]
                            prev_weekday = prev_row.get('Call_Weekday', 0)
                            prev_friday = prev_row.get('Call_Friday', 0)
                            prev_saturday = prev_row.get('Call_Saturday', 0)
                            prev_sunday = prev_row.get('Call_Sunday', 0)
                            prev_total = prev_row.get('Call_Total', 0)
                            prev_bk_weekday = prev_row.get('Backup_Weekday', 0)
                            prev_bk_friday = prev_row.get('Backup_Friday', 0)
                            prev_bk_saturday = prev_row.get('Backup_Saturday', 0)
                            prev_bk_sunday = prev_row.get('Backup_Sunday', 0)
                            prev_bk_total = prev_row.get('Backup_Total', 0)
                        
                        # Calculate running totals
                        running_weekday = current_weekday + prev_weekday
                        running_friday = current_friday + prev_friday
                        running_saturday = current_saturday + prev_saturday
                        running_sunday = current_sunday + prev_sunday
                        running_total = current_total + prev_total
                        
                        running_bk_weekday = current_bk_weekday + prev_bk_weekday
                        running_bk_friday = current_bk_friday + prev_bk_friday
                        running_bk_saturday = current_bk_saturday + prev_bk_saturday
                        running_bk_sunday = current_bk_sunday + prev_bk_sunday
                        running_bk_total = current_bk_total + prev_bk_total
                        
                        # Add to running totals
                        running_call_counts.append({
                            'Resident': name,
                            'Weekday': running_weekday,
                            'Friday': running_friday,
                            'Saturday': running_saturday,
                            'Sunday': running_sunday,
                            'Total': running_total,
                            'Current_Block': current_total,
                            'Previous_Blocks': prev_total
                        })
                        
                        running_backup_counts.append({
                            'Resident': name,
                            'Backup_Weekday': running_bk_weekday,
                            'Backup_Friday': running_bk_friday,
                            'Backup_Saturday': running_bk_saturday,
                            'Backup_Sunday': running_bk_sunday,
                            'Backup_Total': running_bk_total,
                            'Current_Block': current_bk_total,
                            'Previous_Blocks': prev_bk_total
                        })
                    
                    # Display running total tables
                    st.subheader("Call Distribution - Running Total")
                    st.dataframe(pd.DataFrame(running_call_counts))
                    
                    st.subheader("Backup Distribution - Running Total")
                    st.dataframe(pd.DataFrame(running_backup_counts))
                    
                    
            else:
                st.info("No schedule data available to calculate running totals.")
        with subtabs[2]:
            st.header("Soft Constraint Results")
            soft_constraints = st.session_state.get('soft_constraints', {})
            schedule_df = st.session_state.schedule_df.copy()
            if schedule_df.empty or not soft_constraints:
                st.info("Content for 'Soft Constraint Results' will be added here.")
            else:
                # Prepare for analysis
                total = 0
                total_high = 0
                total_low = 0
                fulfilled = 0
                fulfilled_high = 0
                fulfilled_low = 0
                unfulfilled = 0
                unfulfilled_high = 0
                unfulfilled_low = 0
                unfulfilled_rows = []
                # Build a lookup for assignments (strip whitespace from resident names)
                call_lookup = {(row['Date'], str(row['Call']).strip()): True for _, row in schedule_df.iterrows()}
                backup_lookup = {(row['Date'], str(row['Backup']).strip()): True for _, row in schedule_df.iterrows()}
                for resident, constraints in soft_constraints.items():
                    for constraint in constraints:
                        if len(constraint) == 2:
                            start, end = constraint
                            priority = "Rotation/Lecture"
                        else:
                            start, end, priority = constraint
                        # Normalize dates
                        if hasattr(start, 'to_pydatetime'):
                            start = start.to_pydatetime().date()
                        if hasattr(end, 'to_pydatetime'):
                            end = end.to_pydatetime().date()
                        # Count type
                        total += 1
                        if priority == "Non-call request":
                            total_high += 1
                        else:
                            total_low += 1
                        # Check for violations
                        violated_dates = []
                        d = start
                        while d <= end:
                            call_violation = (d, resident) in call_lookup
                            backup_violation = (d, resident) in backup_lookup
                            if priority == "Non-call request":
                                if call_violation or backup_violation:
                                    violated_dates.append((d, call_violation, backup_violation))
                            else:
                                if call_violation:
                                    violated_dates.append((d, True, False))
                            d += pd.Timedelta(days=1)
                        if not violated_dates:
                            fulfilled += 1
                            if priority == "Non-call request":
                                fulfilled_high += 1
                            else:
                                fulfilled_low += 1
                        else:
                            unfulfilled += 1
                            if priority == "Non-call request":
                                unfulfilled_high += 1
                            else:
                                unfulfilled_low += 1
                            unfulfilled_rows.append({
                                'Resident': resident,
                                'Start_Date': start,
                                'End_Date': end,
                                'Priority': priority,
                                'Violated_Dates': ", ".join([
                                    f"{vd[0]} (Call)" if vd[1] else (f"{vd[0]} (Backup)" if vd[2] else f"{vd[0]}") for vd in violated_dates
                                ])
                            })
                st.subheader("Soft Constraint Summary")
                st.markdown(f"**Total soft constraints:** {total}")
                st.markdown(f"- Non-call request: {total_high}")
                st.markdown(f"- Rotation/Lecture: {total_low}")
                st.markdown(f"**Fulfilled:** {fulfilled}  ")
                st.markdown(f"- Non-call request: {fulfilled_high}")
                st.markdown(f"- Rotation/Lecture: {fulfilled_low}")
                st.markdown(f"**Unfulfilled:** {unfulfilled}")
                st.markdown(f"- Non-call request: {unfulfilled_high}")
                st.markdown(f"- Rotation/Lecture: {unfulfilled_low}")
                if unfulfilled_rows:
                    st.subheader("Unfulfilled Soft Constraints")
                    st.dataframe(pd.DataFrame(unfulfilled_rows))
        with subtabs[3]:
            st.header("Golden Weekends")
            golden_weekends_count = st.session_state.get('golden_weekends_count', {})
            if not golden_weekends_count:
                st.info("No golden weekend data available.")
            else:
                # Check if data is rotation-based (nested dict) or total-based (flat dict)
                if golden_weekends_count and isinstance(list(golden_weekends_count.values())[0], dict):
                    # Rotation-based data
                    st.subheader("Golden Weekends by Rotation Period")
                    
                    for rotation_name, residents_data in golden_weekends_count.items():
                        st.write(f"**{rotation_name}**")
                        df_rotation = pd.DataFrame([
                            {'Resident': name, 'Golden Weekends': count}
                            for name, count in residents_data.items()
                        ])
                        st.dataframe(df_rotation, use_container_width=True)
                        st.write("")  # Add spacing between rotations
                else:
                    # Fallback to total counts
                    st.subheader("Total Golden Weekends")
                    df_gw = pd.DataFrame([
                        {'Resident': name, 'Golden Weekends': count}
                        for name, count in golden_weekends_count.items()
                    ])
                    st.dataframe(df_gw)
        with subtabs[4]:
            st.header("Download Schedule")
            if not st.session_state.schedule_df.empty:
                # Prepare all data for the comprehensive Excel file
                df_to_format = st.session_state.schedule_df.copy()
                df_to_format['Date'] = pd.to_datetime(df_to_format['Date'])
                
                # Prepare call distribution data
                df = st.session_state.schedule_df.copy()
                df['Date'] = pd.to_datetime(df['Date'])
                pgy_map = dict(zip(st.session_state.residents_df['Name'], st.session_state.residents_df['PGY']))
                df['PGY'] = df['Call'].map(pgy_map)
                df['Backup_PGY'] = df['Backup'].map(pgy_map)
                df['Weekday'] = df['Date'].dt.weekday
                call_counts = []
                for name in st.session_state.residents_df['Name']:
                    sub = df[df['Call'] == name]
                    weekday = sub[sub['Weekday'].isin([0,1,2,3])].shape[0]
                    friday = sub[sub['Weekday'] == 4].shape[0]
                    saturday = sub[sub['Weekday'] == 5].shape[0]
                    sunday = sub[sub['Weekday'] == 6].shape[0]
                    total = sub.shape[0]
                    # Backup
                    sub_bk = df[df['Backup'] == name]
                    bk_weekday = sub_bk[sub_bk['Weekday'].isin([0,1,2,3])].shape[0]
                    bk_friday = sub_bk[sub_bk['Weekday'] == 4].shape[0]
                    bk_saturday = sub_bk[sub_bk['Weekday'] == 5].shape[0]
                    bk_sunday = sub_bk[sub_bk['Weekday'] == 6].shape[0]
                    bk_total = sub_bk.shape[0]
                    # Intern assignments
                    sub_intern = df[df['Intern'] == name] if 'Intern' in df.columns else pd.DataFrame()
                    intern_weekday = sub_intern[sub_intern['Weekday'].isin([0,1,2,3])].shape[0]
                    intern_friday = sub_intern[sub_intern['Weekday'] == 4].shape[0]
                    intern_saturday = sub_intern[sub_intern['Weekday'] == 5].shape[0]
                    intern_sunday = sub_intern[sub_intern['Weekday'] == 6].shape[0]
                    intern_total = sub_intern.shape[0]
                    call_counts.append({
                        'Resident': name,
                        'Call_Weekday': weekday,
                        'Call_Friday': friday,
                        'Call_Saturday': saturday,
                        'Call_Sunday': sunday,
                        'Call_Total': total,
                        'Backup_Weekday': bk_weekday,
                        'Backup_Friday': bk_friday,
                        'Backup_Saturday': bk_saturday,
                        'Backup_Sunday': bk_sunday,
                        'Backup_Total': bk_total,
                        'Intern_Weekday': intern_weekday,
                        'Intern_Friday': intern_friday,
                        'Intern_Saturday': intern_saturday,
                        'Intern_Sunday': intern_sunday,
                        'Intern_Total': intern_total
                    })
                call_counts_df = pd.DataFrame(call_counts)
                
                # Calculate call shifts by rotation for PGY2 and PGY3
                call_by_rotation_data = []
                rotation_periods = st.session_state.get('rotation_periods', [])
                
                if rotation_periods:
                    # Sort rotation periods by switch date
                    sorted_rotations = sorted(rotation_periods, key=lambda x: x['switch_date'])
                    
                    # Create rotation ranges
                    rotation_ranges = []
                    schedule_start = pd.to_datetime(df_to_format['Date']).min().date()
                    schedule_end = pd.to_datetime(df_to_format['Date']).max().date()
                    
                    # Create rotations from switch dates (last switch date is just end marker)
                    for i in range(len(sorted_rotations) - 1):
                        rotation = sorted_rotations[i]
                        rotation_start = rotation['switch_date']
                        rotation_end = sorted_rotations[i + 1]['switch_date'] - pd.Timedelta(days=1)
                        
                        rotation_ranges.append({
                            'name': rotation.get('rotation_name', f'Rotation {i + 1}'),
                            'start_date': rotation_start,
                            'end_date': rotation_end
                        })
                    
                    # Count call shifts by rotation for PGY2 and PGY3
                    residents_df = st.session_state.residents_df
                    for _, resident_row in residents_df.iterrows():
                        resident_name = resident_row['Name']
                        pgy_level = resident_row['PGY']
                        if pgy_level in [2, 3]:  # Only PGY2 and PGY3
                            for rotation in rotation_ranges:
                                # Filter schedule for this rotation period
                                rotation_mask = (
                                    (pd.to_datetime(df_to_format['Date']).dt.date >= rotation['start_date']) &
                                    (pd.to_datetime(df_to_format['Date']).dt.date <= rotation['end_date'])
                                )
                                rotation_schedule = df_to_format[rotation_mask]
                                
                                # Count call shifts (not backup) for this resident in this rotation
                                call_shifts = rotation_schedule[rotation_schedule['Call'] == resident_name].shape[0]
                                
                                call_by_rotation_data.append({
                                    'Rotation': rotation['name'],
                                    'Resident': resident_name,
                                    'PGY_Level': f'PGY{pgy_level}',
                                    'Call_Shifts': call_shifts
                                })
                
                call_by_rotation_df = pd.DataFrame(call_by_rotation_data) if call_by_rotation_data else None
                
                # Get golden weekends data
                golden_weekends_data = st.session_state.get('golden_weekends_count', {})
                
                # Prepare soft constraint results (if available)
                soft_constraint_results = None
                soft_constraints = st.session_state.get('soft_constraints', {})
                schedule_df = st.session_state.schedule_df.copy()
                if soft_constraints and not schedule_df.empty:
                    # Build soft constraint results similar to subtabs[2]
                    unfulfilled_rows = []
                    call_lookup = {(row['Date'], str(row['Call']).strip()): True for _, row in schedule_df.iterrows()}
                    backup_lookup = {(row['Date'], str(row['Backup']).strip()): True for _, row in schedule_df.iterrows()}
                    
                    for resident, constraints in soft_constraints.items():
                        for constraint in constraints:
                            if len(constraint) == 2:
                                start, end = constraint
                                priority = "Rotation/Lecture"
                            else:
                                start, end, priority = constraint
                            
                            # Normalize dates
                            if hasattr(start, 'to_pydatetime'):
                                start = start.to_pydatetime().date()
                            if hasattr(end, 'to_pydatetime'):
                                end = end.to_pydatetime().date()
                            
                            # Check for violations
                            violated_dates = []
                            d = start
                            while d <= end:
                                call_violation = (d, resident) in call_lookup
                                backup_violation = (d, resident) in backup_lookup
                                if priority == "Non-call request":
                                    if call_violation or backup_violation:
                                        violated_dates.append((d, call_violation, backup_violation))
                                else:
                                    if call_violation:
                                        violated_dates.append((d, True, False))
                                d += pd.Timedelta(days=1)
                            
                            if violated_dates:
                                unfulfilled_rows.append({
                                    'Resident': resident,
                                    'Start_Date': start,
                                    'End_Date': end,
                                    'Priority': priority,
                                    'Violated_Dates': ", ".join([
                                        f"{vd[0]} (Call)" if vd[1] else (f"{vd[0]} (Backup)" if vd[2] else f"{vd[0]}") for vd in violated_dates
                                    ])
                                })
                    
                    if unfulfilled_rows:
                        soft_constraint_results = pd.DataFrame(unfulfilled_rows)
                
                # Prepare running totals (if previous block data exists)
                running_totals_df = None
                previous_df = st.session_state.get('previous_block_data', None)
                if previous_df is not None:
                    # Create a simplified running totals DataFrame
                    running_totals_data = []
                    for name in st.session_state.residents_df['Name']:
                        # Current block counts
                        current_call = df[df['Call'] == name]
                        current_backup = df[df['Backup'] == name]
                        
                        current_weekday = current_call[current_call['Weekday'].isin([0,1,2,3])].shape[0]
                        current_friday = current_call[current_call['Weekday'] == 4].shape[0]
                        current_saturday = current_call[current_call['Weekday'] == 5].shape[0]
                        current_sunday = current_call[current_call['Weekday'] == 6].shape[0]
                        current_total = current_call.shape[0]
                        
                        current_bk_weekday = current_backup[current_backup['Weekday'].isin([0,1,2,3])].shape[0]
                        current_bk_friday = current_backup[current_backup['Weekday'] == 4].shape[0]
                        current_bk_saturday = current_backup[current_backup['Weekday'] == 5].shape[0]
                        current_bk_sunday = current_backup[current_backup['Weekday'] == 6].shape[0]
                        current_bk_total = current_backup.shape[0]
                        
                        # Previous block counts
                        prev_weekday = 0
                        prev_friday = 0
                        prev_saturday = 0
                        prev_sunday = 0
                        prev_total = 0
                        prev_bk_weekday = 0
                        prev_bk_friday = 0
                        prev_bk_saturday = 0
                        prev_bk_sunday = 0
                        prev_bk_total = 0
                        
                        if name in previous_df['Resident'].values:
                            prev_row = previous_df[previous_df['Resident'] == name].iloc[0]
                            prev_weekday = prev_row.get('Call_Weekday', 0)
                            prev_friday = prev_row.get('Call_Friday', 0)
                            prev_saturday = prev_row.get('Call_Saturday', 0)
                            prev_sunday = prev_row.get('Call_Sunday', 0)
                            prev_total = prev_row.get('Call_Total', 0)
                            prev_bk_weekday = prev_row.get('Backup_Weekday', 0)
                            prev_bk_friday = prev_row.get('Backup_Friday', 0)
                            prev_bk_saturday = prev_row.get('Backup_Saturday', 0)
                            prev_bk_sunday = prev_row.get('Backup_Sunday', 0)
                            prev_bk_total = prev_row.get('Backup_Total', 0)
                        
                        running_totals_data.append({
                            'Resident': name,
                            'Call_Weekday_Total': current_weekday + prev_weekday,
                            'Call_Friday_Total': current_friday + prev_friday,
                            'Call_Saturday_Total': current_saturday + prev_saturday,
                            'Call_Sunday_Total': current_sunday + prev_sunday,
                            'Call_Grand_Total': current_total + prev_total,
                            'Backup_Weekday_Total': current_bk_weekday + prev_bk_weekday,
                            'Backup_Friday_Total': current_bk_friday + prev_bk_friday,
                            'Backup_Saturday_Total': current_bk_saturday + prev_bk_saturday,
                            'Backup_Sunday_Total': current_bk_sunday + prev_bk_sunday,
                            'Backup_Grand_Total': current_bk_total + prev_bk_total,
                            'Current_Block_Calls': current_total,
                            'Previous_Blocks_Calls': prev_total
                        })
                    
                    running_totals_df = pd.DataFrame(running_totals_data)
                
                # Create comprehensive Excel file
                wb = run_formatter.format_schedule(
                    schedule_df=df_to_format,
                    call_distribution_df=call_counts_df,
                    golden_weekends_data=golden_weekends_data,
                    soft_constraint_results=soft_constraint_results,
                    running_totals_df=running_totals_df,
                    rotation_periods=st.session_state.get('rotation_periods', []),
                    call_by_rotation_df=call_by_rotation_df
                )
                
                excel_bytes = io.BytesIO()
                wb.save(excel_bytes)
                excel_bytes.seek(0)
                
                st.download_button(
                    label="Download Complete Schedule (Excel)",
                    data=excel_bytes,
                    file_name="complete_schedule.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                
                st.info("📋 **Excel file includes:** Calendar views, Call Distribution, Golden Weekends, Call by Rotation (PGY2/PGY3), Raw Schedule data, Soft Constraint results, and Running Totals (if applicable)")
                
                st.subheader("Call Assignments Table")
                st.dataframe(st.session_state.schedule_df)
            else:
                st.info("No schedule to download.")