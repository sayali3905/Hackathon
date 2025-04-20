from flask import Flask, jsonify, request
from pymongo import MongoClient
import certifi
import os
import pandas as pd
from cerebras.cloud.sdk import Cerebras
os.environ["CEREBRAS_API_KEY"] = "csk-f3nh9y24fwrkp22t3hvvh6kc6w9yk8y88969vpt2r9nx4e9f"

cerebras_client = Cerebras()  # No need to pass api_key if env var is set

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

@app.route("/api/ping")
def ping_mongo():
    try:
        client.admin.command("ping")
        return "✅ MongoDB Atlas is reachable!"
    except Exception as e:
        return f"❌ MongoDB error: {str(e)}", 500

@app.route("/api/burnout-analysis", methods=["POST"])
def burnout_analysis():
    data = request.get_json()
    email = data.get("email")
    view = data.get("view", "Week")
    selected_date = data.get("date")

    assignments = list(assignments_col.find({"user_email": email}))
    if not assignments:
        return jsonify({"error": "No assignments found"}), 404

    df = pd.DataFrame(assignments)
    df['due_at'] = pd.to_datetime(df['due_at'], errors='coerce', utc=True)
    selected_date = pd.to_datetime(selected_date, utc=True)

    # Get date range
    def get_date_range(view_type, selected_date):
        if view_type == "Day":
            return selected_date, selected_date
        elif view_type == "Week":
            start = selected_date - pd.Timedelta(days=selected_date.weekday())
            return start, start + pd.Timedelta(days=6)
        elif view_type == "Month":
            start = pd.to_datetime(f"{selected_date.year}-{selected_date.month:02d}-01", utc=True)
            end = start + pd.offsets.MonthEnd(1)
            return start, end

    start, end = get_date_range(view, selected_date)
    summary_df = df[(df['due_at'] >= start) & (df['due_at'] <= end)]
    total_points = summary_df['points'].fillna(0).sum()
    num_assignments = len(summary_df)
    num_quizzes = summary_df['title'].str.lower().str.contains('quiz|exam', na=False).sum()
    overlapping_tasks = summary_df['due_at'].dt.date.value_counts()
    multiple_deadlines = overlapping_tasks[overlapping_tasks > 1].index.astype(str).tolist()

    # Format prompt
    table = "| Due Date | Course | Title | Points |\n|----------|--------|-------|--------|\n"
    for _, row in summary_df.iterrows():
        table += f"| {row['due_at'].strftime('%Y-%m-%d %H:%M')} | {row['course_name']} | {row['title']} | {int(row['points'])} |\n"

    prompt = f"""
You are my AI wellness assistant.

This is *my workload* for the {view.lower()} period ({start.date()} to {end.date()}):

- 📚 Assignments due: *{num_assignments}*
- 🧪 Quizzes or Exams: *{num_quizzes}*
- 🎯 Total Points: *{total_points}*
- 🔁 Overlapping deadlines: *{len(overlapping_tasks)}*
- 📆 Days with multiple deadlines: *{', '.join(multiple_deadlines) or 'None'}*
- 🕰️ Earliest to latest due: *{summary_df['due_at'].min()}* → *{summary_df['due_at'].max()}*

Here is a table of my upcoming tasks:

{table}

Now, please help me with the following:

1. What is *my burnout risk* (0–100%)?
2. List *3 reasons* why my workload might be stressful.
3. Suggest *3 ways I can manage my time/stress* better.
4. Recommend *3 daily wellness habits*.
5. Identify *the most stressful day* and why.
"""

    # Cerebras API Call
    response = cerebras_client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-4-scout-17b-16e-instruct"
    )

    reply = response.choices[0].message.content

    # Extract burnout %
    import re
    match = re.search(r'(\d{1,3})\s?%', reply)
    burnout = int(match.group(1)) if match else 64
    stress_level = "High" if burnout > 75 else "Moderate" if burnout > 50 else "Low"

    # Weekly map
    week_map = {}
    for d in summary_df['due_at']:
        day_letter = d.strftime('%a')[0]  # e.g., 'M', 'T'
        week_map[day_letter] = "bg-red-500" if stress_level == "High" else "bg-orange-400" if stress_level == "Moderate" else "bg-green-500"

    return jsonify({
        "burnout": burnout,
        "stressLevel": stress_level,
        "summary": reply,
        "weeklyStressMap": week_map
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # Render sets PORT dynamically
    app.run(host="0.0.0.0", port=port)

