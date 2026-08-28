import pandas as pd
import re
from collections import Counter

# --- Read all files ---
hospitals = ['Sheffield', 'Royal Brompton', 'Newcastle', 'Imperial']
all_rows = []

for hospital in hospitals:
    filepath = f'/workspace/GenReport/backend/data/workspaces/30/NAPH LAP 10AR v1.0 {hospital} for web.md'
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Extract table rows - look for pipe-delimited rows after the header
        lines = content.split('\n')
        in_table = False
        hospital_rows = []
        for line in lines:
            if '| Pg | Standard' in line:
                in_table = True
                continue
            if in_table and line.strip().startswith('|'):
                cells = [c.strip() for c in line.strip('|').split('|')]
                if len(cells) >= 4:
                    pg = cells[0]
                    standard_text = cells[1]
                    rec_text = cells[2]
                    action_text = cells[3]
                    
                    # Extract standard number if possible
                    std_match = re.search(r'(\d+)\s+per\s+cent', standard_text)
                    std_num = int(std_match.group(1)) if std_match else None
                    
                    # Extract page number
                    pg_clean = pg.replace(',', '').strip()
                    try:
                        pg_int = int(pg_clean)
                    except:
                        pg_int = None
                    
                    hospital_rows.append({
                        'Hospital': hospital,
                        'Page': pg_int,
                        'Standard_Number_Percent': std_num,
                        'Standard': standard_text,
                        'Recommendation': rec_text,
                        'Planned_Action': action_text
                    })
    except Exception as e:
        print(f"Error reading {hospital}: {e}")

df = pd.DataFrame(all_rows)
print("=== DATA LOADED ===")
print(f"Total records: {len(df)}")
print(f"Hospitals: {df['Hospital'].unique().tolist()}")
print("\n=== RAW DATA ===")
print(df[['Hospital', 'Page', 'Standard_Number_Percent', 'Standard']].to_string())
print("\n=== STANDARDS SUMMARY ===")
std_counts = df['Standard_Number_Percent'].value_counts()
print(std_counts)
