
import os
import re

from flask import Flask, request, jsonify
from pymongo import MongoClient
from dotenv import load_dotenv

# ---------------------------------
# MongoDB Connection
# ---------------------------------

load_dotenv(".env")

app = Flask(__name__)

client = MongoClient(
    os.environ["MONGO_URI"],
    serverSelectionTimeoutMS=10000
)

db = client["sample_restaurants"]

restaurants_col = db["restaurants"]
neighborhoods_col = db["neighborhoods"]

BOROUGHS = [
    "Bronx",
    "Brooklyn",
    "Manhattan",
    "Queens",
    "Staten Island"
]

VALID_GRADES = [
    "A",
    "B",
    "C",
    "Z",
    "P",
    "Not Yet Graded"
]


# ---------------------------------
# Helper: Validate result limit
# ---------------------------------

def get_limit(default=10, maximum=30):
    limit = request.args.get("limit", default, type=int)
    return max(1, min(limit, maximum))


# ---------------------------------
# Endpoint 1: Search Restaurants
# ---------------------------------

@app.route("/restaurants", methods=["GET"])
def search_restaurants():

    name = request.args.get("name", "").strip()
    cuisine = request.args.get("cuisine", "").strip()
    borough = request.args.get("borough", "").strip()
    limit = get_limit(default=10, maximum=20)

    query = {}

    if name:
        query["name"] = {
            "$regex": re.escape(name),
            "$options": "i"
        }

    if cuisine:
        query["cuisine"] = {
            "$regex": re.escape(cuisine),
            "$options": "i"
        }

    if borough:
        query["borough"] = {
            "$regex": f"^{re.escape(borough)}$",
            "$options": "i"
        }

    fields = {
        "_id": 0,
        "restaurant_id": 1,
        "name": 1,
        "cuisine": 1,
        "borough": 1,
        "address.building": 1,
        "address.street": 1,
        "address.zipcode": 1,
        "grades": {"$slice": 1}
    }

    results = list(
        restaurants_col.find(
            query,
            fields
        ).limit(limit)
    )

    return jsonify(results)


# ---------------------------------
# Endpoint 2: Search Neighborhoods
# ---------------------------------

@app.route("/neighborhoods", methods=["GET"])
def search_neighborhoods():

    name = request.args.get("name", "").strip()
    limit = get_limit(default=10, maximum=20)

    query = {}

    if name:
        query["name"] = {
            "$regex": re.escape(name),
            "$options": "i"
        }

    results = list(
        neighborhoods_col.find(
            query,
            {"_id": 0, "name": 1}
        ).limit(limit)
    )

    return jsonify(results)


# ---------------------------------
# Endpoint 3: Most Common Cuisines
# ---------------------------------

@app.route("/analytics/cuisines", methods=["GET"])
def top_cuisines():

    borough = request.args.get("borough", "").strip()
    limit = get_limit(default=10, maximum=30)

    pipeline = []

    if borough:
        pipeline.append({
            "$match": {
                "borough": {
                    "$regex": f"^{re.escape(borough)}$",
                    "$options": "i"
                }
            }
        })

    pipeline += [
        {
            "$group": {
                "_id": "$cuisine",
                "count": {"$sum": 1}
            }
        },
        {
            "$sort": {
                "count": -1,
                "_id": 1
            }
        },
        {"$limit": limit},
        {
            "$project": {
                "_id": 0,
                "cuisine": "$_id",
                "count": 1
            }
        }
    ]

    results = list(
        restaurants_col.aggregate(pipeline)
    )

    return jsonify(results)


# ---------------------------------
# Helper: Inspection Grade Analysis
# ---------------------------------

def build_grade_pipeline(allowed_grades, target_grade):
    """
    Analyze the latest dated inspection per restaurant.

    The denominator includes restaurants in the five
    NYC boroughs whose latest inspection has a grade
    in allowed_grades.
    """

    return [
        # Identify each restaurant's latest inspection
        {
            "$project": {
                "borough": 1,
                "latest": {
                    "$arrayElemAt": [
                        {
                            "$sortArray": {
                                "input": {
                                    "$filter": {
                                        "input": {
                                            "$ifNull": ["$grades", []]
                                        },
                                        "as": "g",
                                        "cond": {
                                            "$eq": [
                                                {"$type": "$$g.date"},
                                                "date"
                                            ]
                                        }
                                    }
                                },
                                "sortBy": {"date": -1}
                            }
                        },
                        0
                    ]
                }
            }
        },

        # Keep valid boroughs and grade codes
        {
            "$match": {
                "borough": {"$in": BOROUGHS},
                "latest.grade": {
                    "$in": allowed_grades
                }
            }
        },

        # Count matching grades by borough
        {
            "$group": {
                "_id": "$borough",
                "total": {"$sum": 1},
                "matching": {
                    "$sum": {
                        "$cond": [
                            {
                                "$eq": [
                                    "$latest.grade",
                                    target_grade
                                ]
                            },
                            1,
                            0
                        ]
                    }
                }
            }
        },

        # Calculate percentages
        {
            "$project": {
                "_id": 0,
                "borough": "$_id",
                "total": 1,
                "matching": 1,
                "percentage": {
                    "$round": [
                        {
                            "$multiply": [
                                {
                                    "$divide": [
                                        "$matching",
                                        "$total"
                                    ]
                                },
                                100
                            ]
                        },
                        2
                    ]
                }
            }
        },

        {
            "$sort": {
                "percentage": -1,
                "borough": 1
            }
        }
    ]


# ---------------------------------
# Endpoint 4: Original A-Grade Analysis
# ---------------------------------

@app.route("/analytics/grades", methods=["GET"])
def inspection_grades():
    """
    Compare A-grade percentages using only restaurants
    whose latest inspection grade is A, B, or C.

    Kept for compatibility with existing MCP tools.
    """

    pipeline = build_grade_pipeline(
        ["A", "B", "C"],
        "A"
    )

    data = list(
        restaurants_col.aggregate(pipeline)
    )

    results = [
        {
            "borough": item["borough"],
            "graded_restaurants": item["total"],
            "a_grades": item["matching"],
            "a_percentage": item["percentage"]
        }
        for item in data
    ]

    return jsonify(results)


# ---------------------------------
# Endpoint 5: Flexible Grade Ranking
# ---------------------------------

@app.route("/analytics/grade-ranking", methods=["GET"])
def grade_ranking():

    requested = (
        request.args.get("grade", "A")
        .strip()
        .upper()
    )

    # Accept N as an alias
    grade = (
        "Not Yet Graded"
        if requested in ("N", "NOT YET GRADED")
        else requested
    )

    if grade not in VALID_GRADES:
        return jsonify({
            "error": "Unsupported inspection grade",
            "supported_grades": VALID_GRADES
        }), 400

    # Optional ordering:
    # percentage, matching, or total
    sort_by = (
        request.args.get("sort_by", "percentage")
        .strip()
        .lower()
    )

    if sort_by not in ("percentage", "matching", "total"):
        return jsonify({
            "error": "Invalid sort_by value",
            "allowed": [
                "percentage",
                "matching",
                "total"
            ]
        }), 400

    pipeline = build_grade_pipeline(
        VALID_GRADES,
        grade
    )

    results = list(
        restaurants_col.aggregate(pipeline)
    )

    # Sort by the requested metric, highest first
    results.sort(
        key=lambda x: (-x[sort_by], x["borough"])
    )

    meanings = {
        "A": "Grade A",
        "B": "Grade B",
        "C": "Grade C",
        "Z": "Grade Pending",
        "P": "Grade Pending after reopening",
        "Not Yet Graded": "Not Yet Graded"
    }

    total_matching = sum(
        item["matching"]
        for item in results
    )

    # Highest percentage is independent of output ordering
    highest = (
        max(results, key=lambda x: x["percentage"])
        if total_matching > 0 and results
        else None
    )

    return jsonify({
        "grade": grade,
        "meaning": meanings[grade],
        "sort_by": sort_by,
        "basis": (
            "Latest dated inspection per restaurant. "
            "Percentages use restaurants whose latest "
            "inspection has one of the six recognized "
            "grade values."
        ),
        "total_matching": total_matching,
        "highest_borough": (
            highest["borough"] if highest else None
        ),
        "boroughs": results
    })


# ---------------------------------
# Endpoint 6: Restaurant Count
# ---------------------------------

@app.route("/count", methods=["GET"])
def count_restaurants():

    count = restaurants_col.count_documents({})

    return jsonify({
        "database": "sample_restaurants",
        "collection": "restaurants",
        "total_restaurants": count
    })


# ---------------------------------
# Start Flask Server
# ---------------------------------

if __name__ == "__main__":

    client.admin.command("ping")

    print("Connected to MongoDB Atlas!")
    print("Database: sample_restaurants")

    app.run(
        host="127.0.0.1",
        port=5001
    )

