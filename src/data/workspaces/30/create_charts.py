import pandas as pd
import re
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from collections import Counter

hospitals = ['Sheffield', 'Royal Brompton', 'Newcastle', 'Imperial']
all_rows = []

for hospital in hospitals:
    filepath = f'/workspace/GenReport/backend/data/workspaces/30/NAPH LAP 10AR v1.0 {hospital} for web.md'
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if '| Pg | Standard' in line:
            continue
        if not line.strip().startswith('|'):
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        if len(cells) >= 2 and cells[0].isdigit():
            pg = int(cells[0])
            standard_text = cells[1]
            rec_text = cells[2] if len(cells) > 2 else ''
            action_text = cells[3] if len(cells) > 3 else ''
            
            std_match = re.search(r'(\d+)\s+per\s+cent', standard_text)
            std_num = int(std_match.group(1)) if std_match else None
            
            all_rows.append({
                'Hospital': hospital,
                'Page': pg,
                'Standard_Number_Percent': std_num,
                'Standard': standard_text,
                'Recommendation': rec_text,
                'Planned_Action': action_text
            })

df = pd.DataFrame(all_rows)

# =============================================
# Chart 1: Standards addressed per hospital
# =============================================
fig, axes = plt.subplots(2, 2, figsize=(12, 9))
fig.suptitle('NAPH Local Action Plans: Standards Addressed by Hospital', fontsize=14, fontweight='bold', y=0.98)

page_std_map = {}
for _, row in df.iterrows():
    key = (row['Page'], row['Standard'])
    page_std_map[row['Page']] = row['Standard'][:80]

# Distribution of page numbers across hospitals
ax1 = axes[0, 0]
page_counts = df['Page'].value_counts().sort_index()
colors = ['#2196F3', '#FF9800', '#4CAF50']
bars = ax1.bar(page_counts.index.astype(str), page_counts.values, color=colors[:len(page_counts)], edgecolor='white', linewidth=1.5)
ax1.set_xlabel('Page Number')
ax1.set_ylabel('Number of Standards Addressed')
ax1.set_title('Frequency by Page Number')
ax1.grid(axis='y', alpha=0.3)
for bar, val in zip(bars, page_counts.values):
    ax1.text(bar.get_x() + bar.get_width()/2., bar.get_height() + 0.05, str(val), ha='center', va='bottom', fontweight='bold')
plt.xticks(range(19, 36, 3), [str(p) for p in range(19, 36, 3)])

# Standards breakdown by type
ax2 = axes[0, 1]
standard_categories = ['Drug Therapy\n(Std 23)', 'Endarterectomy Wait\n(Std 35)', 'Diagnosis Recording\n(Std 19)', 'Cardiac Cath\n(Std 23)']
cat_counts = [0, 0, 0, 0]
for _, row in df.iterrows():
    s = row['Standard'].lower()
    if 'endarterectomy' in s or 'chronic thromboembolic' in s:
        cat_counts[1] += 1
    elif 'drug therapy' in s or 'pha drug therapy' in s or 'commence pa' in s:
        cat_counts[0] += 1
    elif 'diagnosis recorded' in s.lower() or 'referral letter' in s.lower():
        cat_counts[2] += 1
    elif 'cardiac catheterization' in s or 'cardiac catheter' in s:
        cat_counts[3] += 1

bar_colors = ['#FFCDD2', '#BBDEFB', '#C8E6C9', '#FFF9C4']
bars = ax2.bar(range(len(standard_categories)), cat_counts, color=bar_colors, edgecolor='white', linewidth=1.5)
ax2.set_xticks(range(len(standard_categories)))
ax2.set_xticklabels(standard_categories, fontsize=8)
ax2.set_ylabel('Count')
ax2.set_title('Standards by Category')
ax2.grid(axis='y', alpha=0.3)
for bar, val in zip(bars, cat_counts):
    ax2.text(bar.get_x() + bar.get_width()/2., bar.get_height() + 0.05, str(val), ha='center', va='bottom', fontweight='bold')

# Hospital vs Page heatmap
ax3 = axes[1, 0]
hospital_list = ['Sheffield', 'Royal\nBrompton', 'Newcastle', 'Imperial']
pages = sorted(df['Page'].unique())
heatmap_data = pd.DataFrame(0, index=hospital_list, columns=[f'Pg.{p}' for p in pages])
for _, row in df.iterrows():
    heatmap_data.loc[row['Hospital'], f'Pg.{row["Page"]}'] = 1

im = ax3.imshow(heatmap_data.values, cmap='YlOrRd', aspect='auto')
ax3.set_yticks(range(len(hospital_list)))
ax3.set_yticklabels(hospital_list, fontsize=9)
ax3.set_xticks(range(len(pages)))
ax3.set_xticklabels([f'Pg.{p}' for p in pages], fontsize=9)
ax3.set_title('Hospital × Page Coverage')
for i in range(len(hospital_list)):
    for j in range(len(pages)):
        ax3.text(j, i, str(heatmap_data.iloc[i, j]), ha='center', va='center', fontweight='bold', fontsize=14,
                color='white' if heatmap_data.iloc[i, j] == 1 else 'gray')
plt.colorbar(im, ax=ax3, shrink=0.7, label='Records')

# Shortest action length bar chart
ax4 = axes[1, 1]
action_lengths = []
actions_short = []
for _, row in df.iterrows():
    action_text = row['Planned_Action'].replace('<p>', '').replace('</p>', '').strip()
    word_count = len(action_text.split())
    action_lengths.append(word_count)
    actions_short.append(f"{row['Hospital']} (Pg.{row['Page']})")

colors_word = ['#FF5252' if w < 40 else '#FFB74D' if w < 80 else '#4CAF50' for w in action_lengths]
bars = ax4.barh(range(len(actions_short)), action_lengths, color=colors_word, edgecolor='white', height=0.6)
ax4.set_yticks(range(len(actions_short)))
ax4.set_yticklabels(actions_short, fontsize=8)
ax4.set_xlabel('Word Count of Planned Action')
ax4.set_title('Action Plan Detail Level')
ax4.grid(axis='x', alpha=0.3)
ax4.axvline(x=40, color='#FF5252', linestyle='--', alpha=0.5, label='< 40 words')
ax4.axvline(x=80, color='#4CAF50', linestyle='--', alpha=0.5, label='≥ 80 words')
ax4.legend(loc='lower right')

plt.tight_layout(rect=[0, 0, 1, 0.95])
plt.savefig('naph_standards_by_hospital.png', dpi=150, bbox_inches='tight')
print("Chart 1 saved.")

# =============================================
# Chart 2: Key insights dashboard
# =============================================
fig, ax = plt.subplots(figsize=(10, 6))
fig.suptitle('NAPH Local Action Plan Analysis: Key Metrics', fontsize=14, fontweight='bold', y=0.97)

# Bar showing how many hospitals cite each page
page_citation = df['Page'].value_counts().sort_values(ascending=True)
colors_dash = ['#1565C0' if v == page_citation.max() else '#90CAF9' for v in page_citation.values]
bars = ax.barh(range(len(page_citation)), page_citation.values, color=colors_dash, edgecolor='white', height=0.5)
ax.set_yticks(range(len(page_citation)))
ax.set_yticklabels([f"Page {p}" for p in page_citation.index[::-1]], fontsize=10)
ax.set_xlabel('Number of Hospitals Addressing This Standard')
ax.set_title('Page-by-Page Coverage Across All Centres')
ax.grid(axis='x', alpha=0.3)
for bar, val in zip(bars, page_citation.values):
    ax.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height()/2, str(val), ha='left', va='center', fontweight='bold')

# Add annotation boxes
annotation_text = """
• Page 35 is universal — every centre addresses pulmonary endarterectomy waits
• Sheffield uniquely tracks performance decline: 87% → 79% over 3 years  
• Newcastle references Freeman Hospital specifically
• Imperial identifies documentation error as root cause of non-compliance
• Royal Brompton proposes virtual MDT with Royal Papworth
"""
props = dict(boxstyle='round,pad=0.5', facecolor='lightyellow', edgecolor='#FFB300', linewidth=2)
ax.text(0.5, 0.02, annotation_text.strip(), transform=ax.transAxes, ha='center', va='bottom', fontsize=8,
        family='monospace', bbox=props)

plt.tight_layout(rect=[0, 0, 1, 0.94])
plt.savefig('naph_key_insights.png', dpi=150, bbox_inches='tight')
print("Chart 2 saved.")
