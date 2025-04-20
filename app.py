from flask import Flask, jsonify, request
from pymongo import MongoClient
import certifi
import os

# ✅ Use the Mongo URI from environment variable
MONGO_URI = os.environ.get("MONGO_URI")

client = MongoClient(
    MONGO_URI,
    tls=True,
    tlsCAFile=certifi.where()
)


db = client["burnout_detector"]
users_col = db["users"]
assignments_col = db["assignments"]

app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Burnout Detector API is running!"

@app.route("/api/user/<email>")
def get_user(email):
    user = users_col.find_one({"email": email})
    if user:
        user["_id"] = str(user["_id"])
        return jsonify(user)
    else:
        return jsonify({"error": "User not found"}), 404

@app.route("/api/assignments/<email>")
def get_assignments(email):
    assignments = list(assignments_col.find({"user_email": email}))
    for a in assignments:
        a["_id"] = str(a["_id"])
    return jsonify(assignments)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # Render sets PORT dynamically
    app.run(host="0.0.0.0", port=port)
