import pandas as pd
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
MS = BASE / 'doc_gen_scripts' / 'v9_document_mastersheet.xlsx'
if not MS.exists():
    print('MASTERSHEET NOT FOUND', MS)
    raise SystemExit(1)

xl = pd.ExcelFile(MS)
print('sheets:', xl.sheet_names)
for s in xl.sheet_names:
    print('\n---', s, '---')
    try:
        df = xl.parse(s)
        print('shape:', df.shape)
        print('columns:', list(df.columns))
        # show first 3 rows
        rows = df.head(3).to_dict(orient='records')
        for r in rows:
            print(r)
    except Exception as e:
        print('ERROR reading sheet', s, e)
