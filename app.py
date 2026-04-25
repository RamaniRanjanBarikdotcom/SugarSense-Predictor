import os
import json
import numpy as np
from flask import Flask, request, render_template, jsonify, send_from_directory
import pickle
import anthropic
import httpx
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, template_folder="template")
sc = pickle.load(open("sc1.pkl", "rb"))
model = pickle.load(open("classifier1.pkl", "rb"))

_anthropic = anthropic.Anthropic()
_OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
_OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
_HF_TOKEN = os.environ.get("HF_TOKEN", "")
_GOOGLE_KEY = os.environ.get("GOOGLE_API_KEY", "")
_CHAT_MODEL = os.environ.get("CHAT_MODEL", "claude")


def _load_knowledge_base(path="health_knowledge.json"):
    try:
        with open(path, "r") as f:
            kb = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return ""
    lines = ["\n\n## Health Knowledge Base\n"]
    for category, entries in kb.items():
        lines.append(f"\n### {category.replace('_', ' ').title()}")
        for entry in entries:
            # form_fields uses {field, qa: [{q, a}]} — other categories use {q, a} directly
            if "qa" in entry:
                lines.append(f"\n#### {entry.get('field', '')}")
                for qa in entry["qa"]:
                    lines.append(f"\nQ: {qa['q']}\nA: {qa['a']}")
            else:
                lines.append(f"\nQ: {entry['q']}\nA: {entry['a']}")
    return "\n".join(lines)


_SYSTEM_TEXT = (
    "You are a knowledgeable and empathetic Health Assistant for the SugarSense Predictor app — "
    "a diabetes risk screening tool powered by an SVM machine learning model trained on the Pima Indians Diabetes dataset.\n\n"
    "Your role:\n"
    "• Answer questions about diabetes — symptoms, prevention, diet, exercise, medications, blood sugar management, and general health\n"
    "• Help users understand the 8 prediction form fields:\n"
    "  - Pregnancies: number of times pregnant\n"
    "  - Glucose: plasma glucose concentration (mg/dL), normal 70–99\n"
    "  - Blood Pressure: diastolic BP (mm Hg), normal 60–80\n"
    "  - Skin Thickness: triceps skin fold (mm), normal 10–40\n"
    "  - Insulin: 2-hour serum insulin (μU/mL), normal 16–166\n"
    "  - BMI: body mass index (kg/m²), normal 18.5–24.9\n"
    "  - Diabetes Pedigree Function: genetic likelihood score (0.08–0.8 typical)\n"
    "  - Age: in years\n"
    "• Explain what Diabetic / Non-Diabetic prediction results mean and appropriate next steps\n"
    "• Provide accurate, evidence-based health information in clear, accessible language\n\n"
    "Important rules:\n"
    "• Always remind users that SugarSense gives a risk estimate, NOT a medical diagnosis\n"
    "• Encourage professional consultation for medical decisions\n"
    "• Be warm, supportive, and non-alarmist\n"
    "• For emergencies or severe symptoms, immediately direct to emergency services (call 911 or local equivalent)\n"
    "• Never prescribe specific medications or dosages\n"
    "• Keep responses concise and practical — use bullet points for clarity when listing multiple items\n"
    "• You may freely discuss nutrition, lifestyle, exercise, monitoring, and general diabetes management"
) + _load_knowledge_base()

# Claude-specific: system as a list block with prompt caching
_SYSTEM_CLAUDE = [{"type": "text", "text": _SYSTEM_TEXT, "cache_control": {"type": "ephemeral"}}]

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


def _build_messages(history):
    """Convert frontend history to [{role, content}] for any OpenAI-compatible API."""
    start = 0
    while start < len(history) and history[start]["role"] == "assistant":
        start += 1
    return [
        {"role": "user" if h["role"] == "user" else "assistant", "content": h["text"]}
        for h in history[start:]
    ]


def _call_claude(history):
    response = _anthropic.messages.create(
        model="claude-opus-4-7",
        max_tokens=1024,
        system=_SYSTEM_CLAUDE,
        messages=_build_messages(history),
    )
    return next(b.text for b in response.content if b.type == "text")


def _openai_compat_post(url, api_key, model_id, history, extra_headers=None):
    """Shared helper for any OpenAI-compatible chat completions endpoint."""
    messages = [{"role": "system", "content": _SYSTEM_TEXT}] + _build_messages(history)
    headers = {"Authorization": f"Bearer {api_key}"}
    if extra_headers:
        headers.update(extra_headers)
    resp = httpx.post(
        url,
        headers=headers,
        json={"model": model_id, "messages": messages, "max_tokens": 1024},
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _call_openrouter(history, or_model):
    if not _OPENROUTER_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return _openai_compat_post(
        "https://openrouter.ai/api/v1/chat/completions",
        _OPENROUTER_KEY,
        or_model,
        history,
        extra_headers={"X-Title": "SugarSense Predictor"},
    )


def _call_openai(history):
    if not _OPENAI_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return _openai_compat_post(
        "https://api.openai.com/v1/chat/completions",
        _OPENAI_KEY,
        _CHAT_MODEL,
        history,
    )


def _call_huggingface(history):
    if not _HF_TOKEN:
        raise RuntimeError("HF_TOKEN is not set")
    model_id = _CHAT_MODEL[3:] if _CHAT_MODEL.startswith("hf/") else _CHAT_MODEL
    # HF router requires model in the URL path and in the body
    return _openai_compat_post(
        f"https://api-inference.huggingface.co/models/{model_id}/v1/chat/completions",
        _HF_TOKEN,
        model_id,
        history,
    )


def _call_google(history):
    if not _GOOGLE_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not set")
    return _openai_compat_post(
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        _GOOGLE_KEY,
        _CHAT_MODEL,
        history,
    )


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

    # Accept either {history: [...]} (new multi-turn) or {message: str} (legacy)
    history = payload.get("history")
    if history is None:
        raw = str(payload.get("message", "")).strip()
        if not raw:
            return jsonify({"reply": "Please enter a message."}), 400
        history = [{"role": "user", "text": raw}]

    if not history or not isinstance(history, list):
        return jsonify({"reply": "Invalid request format."}), 400

    last = history[-1]
    if last.get("role") != "user" or not str(last.get("text", "")).strip():
        return jsonify({"reply": "Please enter a message."}), 400

    if len(str(last.get("text", ""))) > 2000:
        return jsonify({"reply": "Please keep your message under 2000 characters."}), 400

    try:
        m = _CHAT_MODEL
        if m == "claude" or m.startswith("claude-"):
            reply = _call_claude(history)
        elif m.startswith("gpt-") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
            reply = _call_openai(history)
        elif m.startswith("gemini-"):
            reply = _call_google(history)
        elif m.startswith("hf/"):
            reply = _call_huggingface(history)
        else:
            reply = _call_openrouter(history, m)
        return jsonify({"reply": reply})
    except RuntimeError as e:
        # Config errors (missing API key, etc.)
        return jsonify({"reply": str(e)}), 503
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        app.logger.error("Provider HTTP error %s: %s", status, e)
        if status == 429:
            reply = "Rate limit reached. Wait a moment and try again, or switch to a different model in your .env file."
        elif status == 401:
            reply = "Invalid API key. Check the key for your selected provider in your .env file."
        elif status == 403:
            reply = "Access denied. Make sure your API key has permission to use this model."
        else:
            reply = f"The AI provider returned an error ({status}). Check your API key or model name in .env."
        return jsonify({"reply": reply}), 502
    except anthropic.APIStatusError as e:
        app.logger.error("Anthropic API error: %s", e)
        return jsonify({"reply": "The AI assistant is temporarily unavailable. Please try again in a moment."}), 503
    except Exception as e:
        app.logger.error("Chat error: %s", e)
        return jsonify({"reply": "Something went wrong. Please try again."}), 500


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
