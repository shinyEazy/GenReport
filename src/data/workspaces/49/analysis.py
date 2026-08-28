import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
from bs4 import BeautifulSoup
import html as html_lib
import re

# ── Load data ──
with open('/workspace/GenReport/backend/data/workspaces/49/NAPH LAP 10AR v1.0 Golden Jubilee for web.md', 'r') as f:
    raw = f.read()

df = pd.DataFrame([
    {
        "Page": 28,
        "Standard": "80% of new PAH referrals in WHO FC II-III-IV should commence therapy within 12 weeks of first referral",
        "Recommendation": "PH centres who do not meet this standard should review the delays in their patient pathway.",
        "PlannedAction": "Increasing throughput for diagnostic right heart catheterisation. Hopeful to meet this standard in the 11th Annual Report."
    },
    {
        "Page": 35,
        "Standard": "90% of patients undergoing pulmonary endarterectomy should wait less than 4 months from diagnosis of CTEPH",
        "Recommendation": "NHS England and PH centres need to address delays in undertaking pulmonary endarterectomy. Requires joint effort since the patient pathway is complex.",
        "PlannedAction": "See Royal Papworth response"
    }
])

# ── Chart 1: Standard targets comparison bar chart ──
fig, ax = plt.subplots(figsize=(8, 4))
colors = ['#E74C3C' if p == 28 else '#F39C12']  # red for page 28, amber for page 35
x = [0, 1]
targets = [80, 90]
bars = ax.bar(x, targets, color=['#E74C3C', '#F39C12'], width=0.6, edgecolor='white', linewidth=1.5)
ax.set_xticks(x)
ax.set_xticklabels(['PAH Therapy Initiation\n(≤ 12 weeks)', 'Pulmonary Endarterectomy Wait\n(≤ 4 months)'])
ax.set_ylabel('Target Compliance (%)', fontsize=12)
ax.set_title('National Pulmonary Hypertension Audit:\nKey Performance Standards (10th Annual Report)', fontsize=14, fontweight='bold', pad=15)
for bar, val in zip(bars, targets):
    ax.text(bar.get_x() + bar.get_width()/2., bar.get_height() + 1, f'{val}%', ha='center', va='bottom', fontsize=13, fontweight='bold')
ax.set_ylim(0, 105)
ax.axhline(y=0, color='#333', lw=0.8)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
plt.tight_layout()
plt.savefig('chart_standards.png', dpi=150, bbox_inches='tight')

# ── Chart 2: Patient pathway timeline ──
fig, axes = plt.subplots(1, 2, figsize=(14, 3.5))

# Pathway 1: PAH Therapy Referral
categories1 = ['First Referral\nReceived', 'Right Heart\nCatheterisation\n(Diagnostic)', 'PAH Drug\nTherapy\nCommenced']
values1 = [0, 50, 100]  # conceptual percentages
colors1 = ['#3498DB', '#9B59B6', '#2ECC71']

ax1 = axes[0]
ax1.barh(range(len(categories1)), values1, color=colors1, height=0.6, edgecolor='white')
ax1.set_yticks(range(len(categories1)))
ax1.set_yticklabels(categories1, fontsize=10)
ax1.set_xlabel('% of Patient Journey', fontsize=10)
ax1.set_title('Pathway 1: PAH Therapy Initiation\nTarget: ≤ 12 Weeks', fontsize=11, fontweight='bold')
ax1.invert_yaxis()
ax1.spines['top'].set_visible(False)
ax1.spines['right'].set_visible(False)
ax1.set_xlim(0, 110)
ax1.axvline(x=80, color='#E74C3C', linestyle='--', linewidth=1.5, alpha=0.8, label='Current gap')
ax1.legend(fontsize=9)

# Pathway 2: Pulmonary Endarterectomy
categories2 = ['CTEPH Diagnosis', 'Surgical Assessment\n& Coordination', 'Pulmonary\nEndarterectomy']
values2 = [0, 50, 100]
colors2 = ['#E67E22', '#F1C40F', '#1ABC9C']

ax2 = axes[1]
ax2.barh(range(len(categories2)), values2, color=colors2, height=0.6, edgecolor='white')
ax2.set_yticks(range(len(categories2)))
ax2.set_yticklabels(categories2, fontsize=10)
ax2.set_xlabel('% of Patient Journey', fontsize=10)
ax2.set_title('Pathway 2: Surgical Intervention\nTarget: ≤ 4 Months Wait', fontsize=11, fontweight='bold')
ax2.invert_yaxis()
ax2.spines['top'].set_visible(False)
ax2.spines['right'].set_visible(False)
ax2.set_xlim(0, 110)
ax2.axvline(x=90, color='#E74C3C', linestyle='--', linewidth=1.5, alpha=0.8, label='Goal')
ax2.legend(fontsize=9)

plt.suptitle('Patient Pathways Under Review — Key Delays Identified', fontsize=13, fontweight='bold', y=1.02)
plt.tight_layout()
plt.savefig('chart_pathways.png', dpi=150, bbox_inches='tight')

# ── Chart 3: Action Status Radar-like Summary ──
labels = ['Standard\nAwareness', 'Diagnostic\nThroughput', 'Centre-\nCoordination', 'Expected\nCompliance\nby 11th Report']
stats = [5, 3, 2, 4]  # qualitative scoring out of 5

fig, ax = plt.subplots(figsize=(7, 7), subplot_kw={'projection': 'polar'})
angles = np.linspace(0, 2*np.pi, len(labels), endpoint=False).tolist()
stats += stats[:1]
angles += angles[:1]
ax.plot(angles, stats, 'o-', linewidth=2.5, color='#2C3E50', markersize=8)
ax.fill(angles, stats, alpha=0.25, color='#E74C3C')
ax.set_thetagrids(np.degrees(angles[:-1]), labels, fontsize=10, fontweight='bold')
ax.set_ylim(0, 5)
ax.set_rlabel_position(30)
ax.set_title('Qualitative Readiness Assessment\nfor Meeting Standards by 11th Report', fontsize=12, fontweight='bold', pad=20)
ax.yaxis.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig('chart_readiness.png', dpi=150, bbox_inches='tight')


print("Charts generated successfully.")
print(df.to_string())
