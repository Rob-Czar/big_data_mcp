
import os
import sys
import redis

from pymongo import MongoClient
from bson.json_util import dumps, loads
from dotenv import load_dotenv

load_dotenv(".env")

# -------------------------------
# 1. Connect to MongoDB Atlas
# -------------------------------
mongo = MongoClient(
    os.environ["MONGO_URI"],
    serverSelectionTimeoutMS=10000
)

mongo.admin.command("ping")

collection = mongo["sample_restaurants"]["restaurants"]

print("Connected to MongoDB Atlas!")

# -------------------------------
# 2. Connect to Redis Cloud
# -------------------------------
r = redis.Redis(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]),
    username=os.getenv("REDIS_USERNAME", "default"),
    password=os.environ["REDIS_PASSWORD"],
    ssl=os.getenv("REDIS_TLS", "false").lower() == "true",
    decode_responses=True,
    socket_timeout=15
)

r.ping()

print("Connected to Redis Cloud!")

# -------------------------------
# 3. Transfer restaurant documents
# -------------------------------

total = collection.count_documents({})
print(f"MongoDB contains {total} restaurants.")

# Optional testing limit:
# python transfer_restaurants.py 100
# Without an argument, transfer everything.
limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0

cursor = collection.find({}).batch_size(100)

if limit > 0:
    cursor = cursor.limit(limit)

count = 0
sample_key = None
sample_document = None

pipe = r.pipeline(transaction=False)

for restaurant in cursor:

    restaurant_id = str(
        restaurant.get("restaurant_id") or restaurant["_id"]
    )

    key = f"restaurant:{restaurant_id}"

    # Preserve all fields, including MongoDB BSON types
    value = dumps(restaurant, separators=(",", ":"))

    pipe.set(key, value)

    if sample_key is None:
        sample_key = key
        sample_document = restaurant

    count += 1

    # Send batches of 100 records
    if count % 100 == 0:
        pipe.execute()
        print(f"Transferred {count} restaurants...")

# Send remaining records
pipe.execute()

print(f"\nTransferred {count} restaurants to Redis Cloud!")

# -------------------------------
# 4. Verify imported data
# -------------------------------

if sample_key:
    saved_value = r.get(sample_key)

    if saved_value is None:
        raise RuntimeError("Verification failed: sample key missing")

    restored = loads(saved_value)

    if restored != sample_document:
        raise RuntimeError("Verification failed: document mismatch")

    print("Sample document verified successfully.")
    print("Sample Redis key:", sample_key)

redis_count = sum(
    1 for _ in r.scan_iter(match="restaurant:*", count=1000)
)

print("MongoDB document count:", total)
print("Redis restaurant key count:", redis_count)

if limit == 0 and redis_count == total:
    print("Both databases contain the same number of restaurants!")
else:
    print("Check counts: partial import or existing keys may affect totals.")

mongo.close()
r.close()

