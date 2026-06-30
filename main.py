"""
main.py
HR Assistant Agent — main entry point.
Starts a Flask web server with a simple UI for interacting with the agent.

Usage:
    python main.py

Then open http://localhost:5000 in your browser.

Prerequisites:
    1. pip install -r requirements.txt
    2. Docker container running: docker start hr-mcp
    3. .env file configured (see .env.example)
    4. Ollama running with Mistral pulled, OR Anthropic API key set
"""

import os
from datetime import datetime
from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

from agents.coordinator_agent import CoordinatorAgent
from agents.model_client import provider_info
from tools.mcp_client import MCPClient
from tools.vector_store import VectorStore

app = Flask(__name__)
CORS(app)

coordinator = CoordinatorAgent()

# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────

UI_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HR Assistant</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: #f4f5f7;
            color: #1a1a2e;
            min-height: 100vh;
        }
        header {
            background: #1a1a2e;
            color: white;
            padding: 16px 32px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        header h1 { font-size: 20px; font-weight: 600; }
        .header-right { display: flex; align-items: center; gap: 20px; }
        #model-badge {
            font-size: 11px;
            font-weight: 600;
            padding: 4px 10px;
            border-radius: 20px;
            background: rgba(255,255,255,0.15);
            color: #ccc;
        }
        #status-indicator { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #aaa; }
        #status-dot { width: 8px; height: 8px; border-radius: 50%; background: #666; }
        #status-dot.online { background: #4caf50; }
        #status-dot.offline { background: #f44336; }
        .container { max-width: 900px; margin: 0 auto; padding: 32px 16px; }
        .quick-actions {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px;
            margin-bottom: 28px;
        }
        .quick-btn {
            background: white;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            padding: 14px 16px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 500;
            color: #1a1a2e;
            text-align: left;
            transition: border-color 0.2s, box-shadow 0.2s;
        }
        .quick-btn:hover { border-color: #1a1a2e; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
        .quick-btn .icon { font-size: 20px; margin-bottom: 6px; display: block; }
        .input-area {
            background: white;
            border: 1px solid #e0e0e0;
            border-radius: 10px;
            padding: 16px;
            margin-bottom: 24px;
        }
        textarea {
            width: 100%;
            border: none;
            outline: none;
            font-size: 15px;
            font-family: inherit;
            resize: none;
            color: #1a1a2e;
            line-height: 1.5;
        }
        .input-footer {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-top: 12px;
            padding-top: 12px;
            border-top: 1px solid #f0f0f0;
        }
        .input-hint { font-size: 12px; color: #999; }
        #send-btn {
            background: #1a1a2e;
            color: white;
            border: none;
            border-radius: 6px;
            padding: 10px 24px;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: background 0.2s;
        }
        #send-btn:hover { background: #2d2d4e; }
        #send-btn:disabled { background: #999; cursor: not-allowed; }
        .response-card {
            background: white;
            border: 1px solid #e0e0e0;
            border-radius: 10px;
            padding: 24px;
            margin-bottom: 16px;
        }
        .response-card.review-required { border-left: 4px solid #f59e0b; }
        .response-card.info { border-left: 4px solid #3b82f6; }
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 16px;
        }
        .card-title { font-size: 16px; font-weight: 600; }
        .badge {
            font-size: 11px;
            font-weight: 600;
            padding: 4px 10px;
            border-radius: 20px;
            white-space: nowrap;
        }
        .badge.review { background: #fef3c7; color: #92400e; }
        .badge.ready { background: #d1fae5; color: #065f46; }
        .review-notice {
            background: #fffbeb;
            border: 1px solid #fcd34d;
            border-radius: 6px;
            padding: 12px 16px;
            font-size: 13px;
            color: #92400e;
            margin-bottom: 16px;
        }
        .draft-content {
            background: #f9fafb;
            border: 1px solid #e5e7eb;
            border-radius: 6px;
            padding: 16px;
            font-size: 13px;
            line-height: 1.7;
            white-space: pre-wrap;
            max-height: 400px;
            overflow-y: auto;
            margin-bottom: 16px;
        }
        .section-label {
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #6b7280;
            margin-bottom: 8px;
        }
        .missing-fields { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }
        .field-tag {
            background: #fef2f2;
            border: 1px solid #fecaca;
            color: #991b1b;
            font-size: 11px;
            padding: 3px 8px;
            border-radius: 4px;
            font-family: monospace;
        }
        .compliance-list { font-size: 12px; color: #6b7280; list-style: none; }
        .compliance-list li::before { content: "📋 "; }
        .actions-row { display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
        .action-btn {
            font-size: 13px;
            font-weight: 500;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            border: 1px solid;
            transition: all 0.2s;
        }
        .action-btn.primary { background: #1a1a2e; color: white; border-color: #1a1a2e; }
        .action-btn.secondary { background: white; color: #1a1a2e; border-color: #d1d5db; }
        .action-btn:hover { opacity: 0.85; }
        .score-row { display: flex; gap: 16px; font-size: 12px; color: #6b7280; margin-top: 12px; }
        .score-item { display: flex; align-items: center; gap: 4px; }
        .loading { text-align: center; padding: 40px; color: #6b7280; font-size: 14px; }
        .spinner {
            display: inline-block;
            width: 20px; height: 20px;
            border: 2px solid #e0e0e0;
            border-top-color: #1a1a2e;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin-right: 8px;
            vertical-align: middle;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        .warning-banner {
            background: #fef2f2;
            border: 1px solid #fecaca;
            border-radius: 6px;
            padding: 10px 14px;
            font-size: 13px;
            color: #991b1b;
            margin-bottom: 12px;
        }
    </style>
</head>
<body>

<header>
    <h1>HR Assistant</h1>
    <div class="header-right">
        <span id="model-badge">Loading...</span>
        <div id="status-indicator">
            <div id="status-dot"></div>
            <span id="status-text">Checking...</span>
        </div>
    </div>
</header>

<div class="container">
    <div class="quick-actions">
        <button class="quick-btn" onclick="fillPrompt('Draft an offer letter for a software engineer named Alex Johnson, salary $95,000, starting February 1, based in Maryland.')">
            <span class="icon">📄</span>Offer Letter
        </button>
        <button class="quick-btn" onclick="fillPrompt('Create an independent contractor agreement for Jordan Smith to provide marketing consulting services at $150/hour for a 3-month project in Virginia.')">
            <span class="icon">📝</span>Contractor Agreement
        </button>
        <button class="quick-btn" onclick="fillPrompt('Generate an onboarding checklist for a new project manager named Sarah Lee starting January 20, reporting to the CEO.')">
            <span class="icon">✅</span>Onboarding Checklist
        </button>
        <button class="quick-btn" onclick="fillPrompt('Write a job description for a Senior Account Manager position, remote-friendly, salary range $80,000-$100,000.')">
            <span class="icon">📋</span>Job Description
        </button>
        <button class="quick-btn" onclick="fillPrompt('Draft a professional email to a candidate named Marcus Williams to schedule a final interview next week.')">
            <span class="icon">✉️</span>Candidate Email
        </button>
        <button class="quick-btn" onclick="fillPrompt('What are the key differences between classifying someone as an employee versus an independent contractor in Maryland?')">
            <span class="icon">⚖️</span>Compliance Question
        </button>
    </div>

    <div class="input-area">
        <textarea id="user-input" rows="4"
            placeholder="Describe what you need — an offer letter, contractor agreement, onboarding checklist, job description, candidate email, or HR question...">
        </textarea>
        <div class="input-footer">
            <span class="input-hint">Ctrl+Enter to submit. All documents require your review before sending.</span>
            <button id="send-btn" onclick="submitRequest()">Send</button>
        </div>
    </div>

    <div id="results"></div>
</div>

<script>
    async function checkStatus() {
        try {
            const res = await fetch('/api/status');
            const data = await res.json();
            const dot = document.getElementById('status-dot');
            const text = document.getElementById('status-text');
            const badge = document.getElementById('model-badge');

            dot.className = data.mcp_available ? 'online' : 'offline';
            text.textContent = data.mcp_available ? 'M365 Connected' : 'M365 Offline';

            if (data.model_info) {
                badge.textContent = data.model_info.local
                    ? `${data.model_info.model} (local)`
                    : `${data.model_info.model} (API)`;
            }
        } catch {
            document.getElementById('status-text').textContent = 'Status unknown';
        }
    }

    function fillPrompt(text) {
        document.getElementById('user-input').value = text;
        document.getElementById('user-input').focus();
    }

    async function submitRequest() {
        const input = document.getElementById('user-input').value.trim();
        if (!input) return;
        const btn = document.getElementById('send-btn');
        btn.disabled = true;
        btn.textContent = 'Working...';
        document.getElementById('results').innerHTML =
            '<div class="loading"><span class="spinner"></span>The agent is working on your request...</div>';
        try {
            const res = await fetch('/api/request', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ input })
            });
            const data = await res.json();
            renderResult(data);
        } catch (err) {
            document.getElementById('results').innerHTML =
                `<div class="response-card"><p style="color:#991b1b;">Error: ${err.message}</p></div>`;
        } finally {
            btn.disabled = false;
            btn.textContent = 'Send';
        }
    }

    function renderResult(data) {
        const reviewClass = data.requires_review ? 'review-required' : 'info';
        const badgeClass = data.requires_review ? 'review' : 'ready';
        const badgeText = data.requires_review ? '⚠ Review Required' : '✓ Ready';

        let html = `<div class="response-card ${reviewClass}">
            <div class="card-header">
                <div class="card-title">${esc(data.summary)}</div>
                <span class="badge ${badgeClass}">${badgeText}</span>
            </div>`;

        if (data.warning) html += `<div class="warning-banner">⚠ ${esc(data.warning)}</div>`;
        if (data.requires_review && data.review_reason)
            html += `<div class="review-notice">📋 ${esc(data.review_reason)}</div>`;

        if (data.draft) {
            html += `<div class="section-label">Draft</div>
                <div class="draft-content">${esc(data.draft)}</div>`;
        }

        if (data.missing_fields && data.missing_fields.length > 0) {
            html += `<div class="section-label">Missing Information</div><div class="missing-fields">`;
            data.missing_fields.forEach(f => { html += `<span class="field-tag">{{${f}}}</span>`; });
            html += `</div>`;
        }

        if (data.compliance_notes && data.compliance_notes.length > 0) {
            html += `<div class="section-label">Compliance References Used</div><ul class="compliance-list">`;
            data.compliance_notes.forEach(n => { html += `<li>${esc(n)}</li>`; });
            html += `</ul>`;
        }

        if (data.score_info && data.score_info.length > 0) {
            html += `<div class="score-row">`;
            data.score_info.forEach(s => {
                const icon = s.pruned ? '✗' : '✓';
                const color = s.pruned ? '#991b1b' : '#065f46';
                html += `<span class="score-item" style="color:${color}">${icon} Branch ${s.branch}: ${s.total}/12</span>`;
            });
            html += `</div>`;
        }

        html += `<div class="actions-row">`;
        if (data.document_path) {
            html += `<button class="action-btn primary" data-path="${esc(data.document_path)}" onclick="downloadDoc(this.dataset.path)">Download .docx</button>`;
            html += `<button class="action-btn secondary" data-path="${esc(data.document_path)}" onclick="saveToOneDrive(this.dataset.path)">Save to OneDrive</button>`;
        }
        html += `<button class="action-btn secondary" onclick="document.getElementById('results').innerHTML=''">Clear</button>`;
        html += `</div></div>`;

        document.getElementById('results').innerHTML = html;
    }

    async function downloadDoc(path) {
        const res = await fetch('/api/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path })
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            alert('Download failed: ' + (err.error || res.statusText));
            return;
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = path.split(/[\\/]/).pop();
        a.click();
    }

    async function saveToOneDrive(path) {
        const docType = prompt('Enter folder name for OneDrive (e.g. Offer Letters, Onboarding):');
        if (!docType) return;
        const res = await fetch('/api/save-onedrive', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, doc_type: docType })
        });
        const data = await res.json();
        alert(data.success ? `Saved to OneDrive: ${data.onedrive_path}` : `Error: ${data.error}`);
    }

    function esc(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    document.addEventListener('DOMContentLoaded', () => {
        checkStatus();
        document.getElementById('user-input').addEventListener('keydown', e => {
            if (e.key === 'Enter' && e.ctrlKey) submitRequest();
        });
    });
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────
# API Routes
# ─────────────────────────────────────────────

""" @app.route("/")
def index():
    return render_template_string(UI_HTML) """
@app.route("/")
def index():
    from flask import Response
    return Response(UI_HTML, mimetype="text/html")


@app.route("/api/status")
def status():
    mcp = MCPClient()
    return jsonify({
        "mcp_available": mcp.is_available(),
        "model_info": provider_info(),
        "timestamp": datetime.now().isoformat()
    })


@app.route("/api/request", methods=["POST"])
def handle_request():
    data = request.get_json()
    user_input = data.get("input", "").strip()
    if not user_input:
        return jsonify({"error": "No input provided"}), 400
    try:
        result = coordinator.handle_request(user_input)
        return jsonify(result)
    except Exception as e:
        return jsonify({
            "summary": "An error occurred while processing your request.",
            "draft": str(e),
            "requires_review": False,
            "error": True
        }), 500


@app.route("/api/download", methods=["POST"])
def download_file():
    data = request.get_json()
    path = data.get("path", "")
    if not path or not os.path.exists(path):
        return jsonify({"error": "File not found"}), 404
    return send_file(path, as_attachment=True)


@app.route("/api/save-onedrive", methods=["POST"])
def save_to_onedrive():
    data = request.get_json()
    local_path = data.get("path", "")
    doc_type = data.get("doc_type", "Documents")
    if not local_path or not os.path.exists(local_path):
        return jsonify({"success": False, "error": "File not found"}), 404
    try:
        mcp = MCPClient()
        filename = os.path.basename(local_path)
        onedrive_path = f"/HR Documents/{doc_type}/{filename}"
        mcp.upload_file(local_path, onedrive_path)
        return jsonify({"success": True, "onedrive_path": onedrive_path})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/index-templates", methods=["POST"])
def index_templates():
    try:
        store = VectorStore()
        results = store.index_all_templates()
        return jsonify({"success": True, "indexed": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ─────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("HR Assistant Agent")
    print("=" * 50)

    info = provider_info()
    print(f"Model: {info['model']} via {info['provider']} ({'local' if info['local'] else 'API'})")

    try:
        store = VectorStore()
        if store.templates.count() == 0:
            print("Indexing templates into vector store...")
            results = store.index_all_templates()
            print(f"Indexed: {results}")
        else:
            print(f"Vector store ready ({store.templates.count()} template chunks)")
    except Exception as e:
        print(f"Warning: Could not initialize vector store: {e}")

    mcp = MCPClient()
    if mcp.is_available():
        print("MCP server: Connected")
    else:
        print("MCP server: Not reachable (start with: docker start hr-mcp)")

    print("\nStarting web server at http://localhost:5000")
    print("Press Ctrl+C to stop\n")

    app.run(debug=False, host="0.0.0.0", port=5000)