"""
diagnose_matching.py  -  ONE-OFF diagnostic. Run this directly (not through
Streamlit) to see exactly what the item-matching logic finds for a given
question against your REAL catalog and warehouse data. Helps distinguish
"code not reloaded" from "matching logic doesn't handle this item's real
name" as the cause of an incomplete answer.

Run:  python diagnose_matching.py "your question here"
"""
import sys
import orchestrator
import inventory_agent

question = sys.argv[1] if len(sys.argv) > 1 else "how much black velvet do we have in our warehouse"

catalog = inventory_agent.item_catalog()
warehouse = inventory_agent.warehouse_summary()
inv = inventory_agent.summary()

print(f"Question: {question!r}\n")

print("--- ALL catalog rows whose name contains 'black velvet' (case-insensitive substring, ground truth) ---")
for c in catalog:
    if "black velvet" in c["item_description"].lower():
        print(f"  item_number={c['item_number']:<6} item_description={c['item_description']!r:<30} "
              f"bottle_volume_ml={c.get('bottle_volume_ml')}")

print("\n--- What _match_items() actually returns for this question (catalog) ---")
matched_catalog = orchestrator._match_items(question, catalog, "item_description", limit=5)
for m in matched_catalog:
    print(f"  item_number={m['item_number']:<6} item_description={m['item_description']!r:<30} "
          f"bottle_volume_ml={m.get('bottle_volume_ml')}")

print("\n--- ALL warehouse rows whose name contains 'black velvet' (ground truth) ---")
for w in warehouse.get("items", []):
    if "black velvet" in w["item_name"].lower():
        print(f"  item_number={w['item_number']:<6} item_name={w['item_name']!r:<30} on_hand={w['on_hand']}")

print("\n--- What _match_items() actually returns for this question (warehouse) ---")
matched_warehouse = orchestrator._match_items(question, warehouse.get("items", []), "item_name", limit=5)
for m in matched_warehouse:
    print(f"  item_number={m['item_number']:<6} item_name={m['item_name']!r:<30} on_hand={m['on_hand']}")

print("\n--- ALL flagged store-items whose name contains 'black velvet' (ground truth) ---")
for f in inv["flags"]:
    if "black velvet" in f["item_description"].lower():
        print(f"  store={f['store_number']:<6} days_of_cover={f['days_of_cover']}")

if not matched_catalog and not matched_warehouse:
    print("\n>>> _match_items found NOTHING - the matching logic itself needs fixing.")
elif len(matched_catalog) < 2:
    print("\n>>> _match_items found FEWER items than the ground-truth list above - "
          "matching logic needs to be looser, or item_descriptions differ per size in a way "
          "that hurts word-overlap scoring.")
else:
    print("\n>>> Matching looks correct. If the chatbot's answer still doesn't reflect this, "
          "the code running in Streamlit is NOT this version - fully restart Streamlit "
          "(Ctrl+C, then `streamlit run app.py` again), don't just refresh the browser.")
