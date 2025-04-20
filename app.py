from flask import Flask, jsonify, request
from pymongo import MongoClient
import certifi
import os
import pandas as pd
import re
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
    
# def format_task_table(df):
#     rows = []
#     for _, row in df.iterrows():
#         rows.append(f"| {row['due_at'].strftime('%Y-%m-%d %H:%M'):<19} | {row['course_name']:<23} | {row['title']:<28} | {str(int(row['points'])):^6} |")
#     header = (
#         "+---------------------+-------------------------+------------------------------+--------+\n"
#         "|      Due Date       |       Course Name       |            Title             | Points |\n"
#         "+---------------------+-------------------------+------------------------------+--------+"
#     )
#     table = "\n".join([header] + rows + ["+---------------------+-------------------------+------------------------------+--------+"])
#     return table    

@app.route("/api/burnout-analysis", methods=["POST"])
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

    # Build ASCII-style table directly here
    header = (
        "+---------------------+-------------------------+------------------------------+--------+\n"
        "|      Due Date       |       Course Name       |            Title             | Points |\n"
        "+---------------------+-------------------------+------------------------------+--------+"
    )
    rows = []
    for _, row in summary_df.iterrows():
        due_at_str = row['due_at'].strftime('%Y-%m-%d %H:%M')
        course_name = str(row['course_name']) if pd.notnull(row['course_name']) else "N/A"
        title = str(row['title']) if pd.notnull(row['title']) else "N/A"
        points = str(int(row['points'])) if pd.notnull(row['points']) else "0"
        rows.append(f"| {due_at_str:<19} | {course_name:<23} | {title:<28} | {points:^6} |")
    ascii_table = "\n".join([header] + rows + ["+---------------------+-------------------------+------------------------------+--------+"])

    prompt = f"""
You're my wellness assistant.

This is my academic workload for the {view.lower()} period ({start.date()} to {end.date()}):

Assignments due: {num_assignments}
Quizzes/Exams: {num_quizzes}
Total Points: {total_points}
Overlapping Deadlines: {len(overlapping_tasks)}
Days with Multiple Deadlines: {', '.join(multiple_deadlines) or 'None'}
Earliest Due: {summary_df['due_at'].min()}
Latest Due: {summary_df['due_at'].max()}

Upcoming Tasks Table:
Format: [Due Date] - [Course Name] - [Title] - [Points]

{ascii_table}

Please return your response in *plain text* only, following this exact format:

BURNOUT RISK: <percent from 0-100> %
Reasons:
1. <reason 1>
2. <reason 2>
3. <reason 3>
Strategies:
1. <strategy 1>
2. <strategy 2>
3. <strategy 3>
Wellness Habits:
1. <habit 1>
2. <habit 2>
3. <habit 3>
Most Stressful Day: <day and why>

Then, reprint the same table again under this heading:

Formatted Table of Tasks:
{ascii_table}

Align all rows neatly. Keep spacing and alignment consistent. Do not add any extra commentary or explanation.
"""

    response = cerebras_client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-4-scout-17b-16e-instruct"
    )

    reply = response.choices[0].message.content

    match = re.search(r'(\d{1,3})\s?%', reply)
    burnout = int(match.group(1)) if match else 64
    stress_level = "High" if burnout > 75 else "Moderate" if burnout > 50 else "Low"

    week_map = {}
    for d in summary_df['due_at']:
        day_letter = d.strftime('%a')[0]
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

