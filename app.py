import os
import re
import json
import base64
import anthropic
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/generate", methods=["POST"])
def generate():
    try:
        pdf_file = request.files.get("pdf")
        page_from = request.form.get("page_from")
        page_to = request.form.get("page_to")
        topic = request.form.get("topic", "Chemistry")
        class_num = request.form.get("class_num", "11")
        count = request.form.get("count", "10")

        if not pdf_file:
            return jsonify({"error": "No PDF uploaded"}), 400
        if not page_from or not page_to:
            return jsonify({"error": "Page range required"}), 400

        pdf_bytes = pdf_file.read()
        pdf_base64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

        prompt = f"""You are an expert chemistry teacher creating a Daily Practice Problem (DPP) sheet for Class {class_num} students.

The PDF attached is a chemistry textbook. The student was taught from page {page_from} to page {page_to} today on the topic: "{topic}".

Your task:
1. Read the content from pages {page_from} to {page_to} carefully.
2. Generate EXACTLY {count} multiple choice questions (MCQs) based ONLY on the content of those pages.
3. Each question must have exactly 4 options: (A), (B), (C), (D).
4. Questions should test conceptual understanding, not just rote recall.
5. Mix question types: definition-based, application-based, reaction-based, numerical (if applicable).
6. Make distractors plausible but clearly wrong to an informed student.

Return ONLY a valid JSON object, no markdown, no explanation, no preamble, no extra text. Format:
{{
  "questions": [
    {{
      "q": "Question text here",
      "options": ["A) ...", "B) ...", "C) ...", "D) ..."],
      "answer": "A",
      "explanation": "Brief 1-line explanation of why this is correct"
    }}
  ]
}}"""

        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=4000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_base64
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }]
        )

        raw_text = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )

        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if not match:
            return jsonify({"error": "AI returned invalid format. Please try again."}), 500
        clean = match.group(0)

        parsed = json.loads(clean)

        return jsonify({
            "success": True,
            "questions": parsed["questions"],
            "topic": topic,
            "class_num": class_num,
            "page_from": page_from,
            "page_to": page_to
        })

    except json.JSONDecodeError:
        return jsonify({"error": "AI returned invalid format. Please try again."}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
