python - <<'PY'
from google.cloud import bigquery

SOURCE = "momentum-trader-479809"
DEST = "momentum-trader-2026"

client = bigquery.Client(project=DEST)

print("Listing source datasets...")

for ds in client.list_datasets(project=SOURCE):
    dataset_id = ds.dataset_id
    print(f"\n📦 DATASET: {dataset_id}")

    dest_ds = f"{DEST}.{dataset_id}"
    client.create_dataset(dest_ds, exists_ok=True)

    print("  Listing tables...")

    for table in client.list_tables(f"{SOURCE}.{dataset_id}"):
        if table.table_type != "TABLE":
            print(f"  ⏭️ Skipping {table.table_id} ({table.table_type})")
            continue

        src = f"{SOURCE}.{dataset_id}.{table.table_id}"
        dst = f"{DEST}.{dataset_id}.{table.table_id}"

        print(f"  ➜ Copying {table.table_id}")

        job = client.copy_table(src, dst)
        job.result(timeout=300)

        print("     ✅ Done")

print("\n🎉 All copies finished.")
PY
