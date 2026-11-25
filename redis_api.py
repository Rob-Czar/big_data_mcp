
import os
import json
import redis

from flask import Flask, jsonify
from dotenv import load_dotenv

load_dotenv(".env")

app = Flask(__name__)

r = redis.Redis(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]),
    username=os.getenv("REDIS_USERNAME", "default"),
    password=os.environ["REDIS_PASSWORD"],
    ssl=os.getenv("REDIS_TLS", "false").lower() == "true",
    decode_responses=True
)


# Tool 4: Find a restaurant by ID
@app.route("/restaurants/<restaurant_id>", methods=["GET"])
def get_restaurant(restaurant_id):
    key = f"restaurant:{restaurant_id}"

    data = r.get(key)

    if data is None:
        return jsonify({
            "error": "Restaurant not found",
            "restaurant_id": restaurant_id
        }), 404

    return jsonify(json.loads(data))


if __name__ == "__main__":
    r.ping()
    print("Connected to Redis Cloud!")
    app.run(host="127.0.0.1", port=5002)

