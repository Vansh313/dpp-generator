import os
import re
import json
import base64
import anthropic
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from supabase import create_client, Client

app = Flask(__name__)
CORS(app)

anthropic_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "vanshcraft123")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/admin")
def admin():
    return render_template("admin.html")

@app.route("/api/chapters", methods=["GET"])
def get_chapters():
    try:
        response = supabase.table("chapters").select("*").order("chapter_number").execute()
        return jsonify({"success": True, "chapters": response.data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/upload-chapter", methods=["POST"])
def upload_chapter():
    try:
        password = request.form.get("password")
        if password != ADMIN_PASSWORD:
            return jsonify({"error": "Invalid password"}), 401

        pdf_file = request.files.get("pdf")
        chapter_number = request.form.get("chapter_number")
        chapter_name = request.form.get("chapter_name")
        default_page_from = request.form.get("default_page_from")
        default_page_to = request.form.get("default_page_to")
        class_num = request.form.get("class_num", "11")

        if not pdf_file or not chapter_name:
            return jsonify({"error": "PDF and chapter name required"}), 400

        pdf_bytes = pdf_file.read()
        file_name = f"chapter_{chapter_number}_{chapter_name.replace(' ', '_')}.pdf"

        # Upload to Supabase Storage
        supabase.storage.from_("chapters").upload(
            file_name,
            pdf_bytes,
            {"content-type": "application/pdf", "upsert": "true"}
        )

        # Get public URL
        public_url = supabase.storage.from_("chapters").get_public_url(file_name)

        # Save metadata to database
        supabase.table("chapters").upsert({
            "chapter_number": int(chapter_number),
            "chapter_name": chapter_name,
            "file_name": file_name,
            "file_url": public_url,
            "default_page_from": int(default_page_from) if default_page_from else None,
            "default_page_to": int(default_page_to) if default_page_to else None,
            "class_num": class_num
        }).execute()

        return jsonify({"success": True, "message": f"Chapter '{chapter_name}' uploaded successfully!"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/delete-chapter/<int:chapter_id>", methods=["DELETE"])
def delete_chapter(chapter_id):
    try:
        password = request.args.get("password")
        if password != ADMIN_PASSWORD:
            return jsonify({"error": "Invalid password"}), 401

        # Get chapter info
        result = supabase.table("chapters").select("*").eq("id", chapter_id).execute()
        if not result.data:
            return jsonify({"error": "Chapter not found"}), 404

        chapter = result.data[0]

        # Delete from storage
        supabase.storage.from_("chapters").remove([chapter["file_name"]])

        # Delete from database
        supabase.table("chapters").delete().eq("id", chapter_id).execute()

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/generate", methods=["POST"])
def generate():
    try:
        chapter_id = request.form.get("chapter_id")
        page_from = request.form.get("page_from")
        page_to = request.form.get("page_to")
        count = request.form.get("count", "10")

        if not chapter_id:
            return jsonify({"error": "Please select a chapter"}), 400

        # Get chapter from DB
        result = supabase.table("chapters").select("*").eq("id", chapter_id).execute()
        if not result.data:
            return jsonify({"error": "Chapter not found"}), 404

        chapter = result.data[0]
        topic = chapter["chapter_name"]
        class_num = chapter["class_num"]

        # Use default pages if not provided
        if not page_from:
            page_from = chapter.get("default_page_from", 1)
        if not page_to:
            page_to = chapter.get("default_page_to", 50)

        # Download PDF from Supabase Storage
        pdf_bytes = supabase.storage.from_("chapters").download(chapter["file_name"])
        pdf_base64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

        prompt = f"""You are an expert chemistry teacher creating a Daily Practice Problem (DPP) sheet for Class {class_num} students.

The PDF attached is a chemistry textbook. The topic is: "{topic}", pages {page_from} to {page_to}.

Your task:
1. Read the content from pages {page_from} to {page_to} carefully.
2. Generate EXACTLY {count} multiple choice questions (MCQs) based ONLY on the content of those pages.
3. Each question must have exactly 4 options: (A), (B), (C), (D).
4. Questions should test conceptual understanding, not just rote recall.
5. Mix question types: definition-based, application-based, reaction-based, numerical (if applicable).
6. Make distractors plausible but clearly wrong to an informed student.

Return ONLY a valid JSON object, no markdown, no explanation, no preamble. Format:
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

        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
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
                    {"type": "text", "text": prompt}
                ]
            }]
        )

        raw_text = "".join(block.text for block in response.content if hasattr(block, "text"))
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
