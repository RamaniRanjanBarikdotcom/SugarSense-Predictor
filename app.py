import numpy as np
from flask import Flask, request, render_template, jsonify, send_from_directory
import pickle

app = Flask(__name__, template_folder="template")
sc = pickle.load(open("sc1.pkl", "rb"))
model = pickle.load(open("classifier1.pkl", "rb"))

FEATURE_CONFIG = [
    ("pregnancies", "Pregnancies", float, 0, 25),
    ("glucose", "Glucose", float, 1, 300),
    ("blood_pressure", "BloodPressure", float, 20, 200),
    ("skin_thickness", "SkinThickness", float, 0, 100),
    ("insulin", "Insulin", float, 0, 900),
    ("bmi", "BMI", float, 10, 70),
    ("diabetes_pf", "DiabetesPedigreeFunction", float, 0.05, 3.0),
    ("age", "Age", float, 1, 120),
]


def parse_and_validate_form(form_data):
    """Validate user input and return a feature row mapped to model columns."""
    feature_row = {}
    for form_name, model_column, cast, min_value, max_value in FEATURE_CONFIG:
        raw_value = form_data.get(form_name, "").strip()
        if not raw_value:
            return None, f"{form_name.replace('_', ' ').title()} is required."
        try:
            value = cast(raw_value)
        except ValueError:
            return None, f"{form_name.replace('_', ' ').title()} must be a number."
        if value < min_value or value > max_value:
            return (
                None,
                f"{form_name.replace('_', ' ').title()} must be between {min_value} and {max_value}.",
            )
        feature_row[model_column] = value

    return feature_row, None


def generate_chatbot_reply(message):
    text = message.strip().lower()
    if not text:
        return "Please type a question so I can help."

    if any(word in text for word in ["hi", "hello", "hey"]):
        return "Hello. I can help with diabetes basics, lifestyle tips, and understanding your prediction form."
    if any(word in text for word in ["symptom", "sign"]):
        return "Common symptoms include frequent urination, unusual thirst, fatigue, blurred vision, and slow-healing wounds. Please consult a doctor for diagnosis."
    if any(word in text for word in ["prevent", "prevention", "avoid"]):
        return "Prevention steps include healthy weight management, regular exercise, balanced meals, better sleep, and routine checkups."
    if any(word in text for word in ["diet", "food", "meal", "eat"]):
        return "Focus on vegetables, whole grains, lean protein, and high-fiber foods. Limit sugary drinks and highly processed snacks."
    if any(word in text for word in ["exercise", "workout", "walk"]):
        return "A good target is at least 150 minutes of moderate activity weekly, like brisk walking, plus strength training."
    if any(word in text for word in ["result", "predict", "form", "model"]):
        return "This app gives a risk estimate from the form values. It is not a medical diagnosis. For certainty, please consult a healthcare professional."
    if any(word in text for word in ["emergency", "severe", "urgent"]):
        return "If symptoms are severe or sudden, seek urgent medical care immediately."
    if any(word in text for word in ["bye", "goodbye"]):
        return "Take care. I am here whenever you want to ask another question."

    return "I can help with diabetes symptoms, prevention, diet, exercise, and how to use the SugarSense Predictor app. Ask me one of those topics."


@app.route("/")
def home():
    # serve directly to avoid Jinja2 parsing JSX double-brace syntax
    return send_from_directory(app.template_folder, "index.html")


@app.route("/chatbot")
def chatbot():
    return render_template("chatbot.html")


@app.route("/chat", methods=["POST"])
def chat():
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()

    if not message:
        return jsonify({"reply": "Please enter a message."}), 400
    if len(message) > 500:
        return jsonify({"reply": "Please keep the message under 500 characters."}), 400

    return jsonify({"reply": generate_chatbot_reply(message)})


@app.route("/predict", methods=["POST"])
def predict():
    feature_row, error = parse_and_validate_form(request.form)
    if error:
        return render_template("index.html", error=error, values=request.form), 400

    model_input = np.array([[feature_row[cfg[1]] for cfg in FEATURE_CONFIG]], dtype=float)
    scaled_input = sc.transform(model_input)
    prediction = int(model.predict(np.array(scaled_input))[0])
    return render_template("result.html", prediction=prediction)


# JSON API used by the React SPA
_JSON_KEY_TO_MODEL_COL = {
    "pregnancies": "Pregnancies",
    "glucose": "Glucose",
    "bloodPressure": "BloodPressure",
    "skinThickness": "SkinThickness",
    "insulin": "Insulin",
    "bmi": "BMI",
    "dpf": "DiabetesPedigreeFunction",
    "age": "Age",
}
_JSON_KEY_BOUNDS = {
    "pregnancies": (0, 25),
    "glucose": (1, 300),
    "bloodPressure": (20, 200),
    "skinThickness": (0, 100),
    "insulin": (0, 900),
    "bmi": (10, 70),
    "dpf": (0.05, 3.0),
    "age": (1, 120),
}


@app.route("/predict_api", methods=["POST"])
def predict_api():
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "JSON body required"}), 400

    features = []
    for key, col in _JSON_KEY_TO_MODEL_COL.items():
        val = payload.get(key)
        if val is None:
            return jsonify({"error": f"Missing field: {key}"}), 400
        try:
            val = float(val)
        except (TypeError, ValueError):
            return jsonify({"error": f"{key} must be a number"}), 400
        lo, hi = _JSON_KEY_BOUNDS[key]
        if not (lo <= val <= hi):
            return jsonify({"error": f"{key} must be between {lo} and {hi}"}), 400
        features.append(val)

    model_input = np.array([features], dtype=float)
    prediction = int(model.predict(sc.transform(model_input))[0])
    return jsonify({"prediction": prediction})


if __name__ == "__main__":
    app.run(debug=False)
