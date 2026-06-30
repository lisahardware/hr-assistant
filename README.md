# HR Assistant

An AI-powered HR assistant for small businesses. Describe what you need in plain English and get a ready-to-review Word document back in seconds — offer letters, contractor agreements, onboarding checklists, and job descriptions, with built-in compliance context for Maryland, DC, and Virginia.

---

## Features

- **Document generation** — produces filled `.docx` files from structured templates
- **Tree-of-Thought drafting** — generates 3 candidate drafts per request and scores them, picking the best one
- **Compliance-aware** — retrieval agent pulls relevant labor law context before drafting
- **Owner review gates** — documents containing compensation or legal terms are always flagged before you can send them
- **Microsoft 365 integration** — save documents to OneDrive, send emails via Outlook, schedule interviews in Calendar (requires M365 account)
- **Local or API model** — runs fully offline with Ollama/Mistral, or against the Anthropic API

---

## Architecture

```
Browser
  │
  ▼
Flask Web UI (port 5000)          main.py
  │
  ▼
CoordinatorAgent                  agents/coordinator_agent.py
  │  Classify → Extract → Retrieve → Draft → Evaluate → Respond
  │
  ├── RetrievalAgent              agents/retrieval_agent.py
  │     ChromaDB vector store     tools/vector_store.py
  │
  ├── DraftingAgent               agents/drafting_agent.py
  │     Tree-of-Thought (3 branches)
  │
  ├── CriticAgent                 agents/critic_agent.py
  │     Scores & selects best branch
  │
  ├── CommunicationAgent          agents/communication_agent.py
  │     Formats response, applies review rules
  │
  ├── DocumentCreator             tools/document_creator.py
  │     Fills {{PLACEHOLDER}} tokens in .docx templates
  │
  └── MCPClient                   tools/mcp_client.py
        Microsoft 365 MCP server  (Docker container, port 3000)
```

### Request flow

1. You type a request ("Draft an offer letter for Alex Johnson, $95k, starting Feb 1 in Maryland")
2. **Coordinator** classifies the intent and extracts field values
3. **Retrieval** agent fetches the matching template and state-specific compliance context from ChromaDB
4. **Drafting** agent produces 3 candidate field-value sets (formal / concise / balanced)
5. **Critic** agent scores all 3 branches and selects the best one
6. **DocumentCreator** fills the winning field set into the `.docx` template and saves the file
7. **CommunicationAgent** formats the response — flagging the document for review if it contains salary or legal terms
8. The UI shows a draft preview with a **Download .docx** button and a list of any remaining unfilled fields

### Document types

| Request type | Pipeline |
|---|---|
| Offer letter | Full ToT pipeline |
| Contractor agreement | Full ToT pipeline |
| Onboarding checklist | Full ToT pipeline |
| Job description | Full ToT pipeline |
| Candidate email | Simplified — single draft, always flagged for review |
| Interview scheduling | Simplified — parses intent, surfaces missing details |
| General HR question | Simplified — retrieval + direct answer |

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Docker** | Required for the M365 MCP sidecar |
| **Python 3.12+** | Only needed when running locally (not via Docker Compose) |
| **Ollama** _or_ **Anthropic API key** | At least one model provider must be configured |
| **Microsoft 365 account** | Optional — only needed for OneDrive / email / calendar features |

---

## Configuration

### `.env` (root — Flask app)

```env
# Choose one model provider:

MODEL_PROVIDER=ollama        # fully local, no API key
OLLAMA_MODEL=mistral

# MODEL_PROVIDER=anthropic
# ANTHROPIC_MODEL=claude-sonnet-4-6
# ANTHROPIC_API_KEY=your_key_here
```

### `business_config.json` (company details)

```json
{
  "COMPANY_NAME": "Acme Consulting LLC",
  "COMPANY_ADDRESS": "123 Main Street, Suite 200, Arlington, VA 22201",
  "SIGNATORY_NAME": "Jane Doe",
  "SIGNATORY_TITLE": "Owner",
  "HR_CONTACT": "Jane Doe",
  "HR_EMAIL": "jane@acmeconsulting.com",
  "ROLE_SPECIFIC_TASK_1": "{{ROLE_SPECIFIC_TASK_1}}",
  "ROLE_SPECIFIC_TASK_2": "{{ROLE_SPECIFIC_TASK_2}}",
  "ROLE_SPECIFIC_TASK_3": "{{ROLE_SPECIFIC_TASK_3}}",
  "RESPONSIBILITY_1": "{{RESPONSIBILITY_1}}",
  "RESPONSIBILITY_2": "{{RESPONSIBILITY_2}}",
  "RESPONSIBILITY_3": "{{RESPONSIBILITY_3}}",
  "RESPONSIBILITY_4": "{{RESPONSIBILITY_4}}",
  "RESPONSIBILITY_5": "{{RESPONSIBILITY_5}}",
  "REQUIRED_QUALIFICATION_1": "{{REQUIRED_QUALIFICATION_1}}",
  "REQUIRED_QUALIFICATION_2": "{{REQUIRED_QUALIFICATION_2}}",
  "REQUIRED_QUALIFICATION_3": "{{REQUIRED_QUALIFICATION_3}}",
  "PREFERRED_QUALIFICATION_1": "{{PREFERRED_QUALIFICATION_1}}",
  "PREFERRED_QUALIFICATION_2": "{{PREFERRED_QUALIFICATION_2}}"
}
```

These values are automatically injected into every generated document, so you never have to type your company name or signatory details into a prompt. Edit this file with your own information before running the app.

### `docker/.env` (MCP container — M365 credentials)

```env
MS365_MCP_TENANT_ID=your-tenant-id
MS365_MCP_CLIENT_ID=your-client-id
```

These values come from an Azure App Registration with Mail, Calendar, and Files (OneDrive) API permissions. If you don't have M365, the app still works — document generation runs locally and the M365 status indicator will show "Offline".

---

## Running with Docker Compose (recommended)

This starts both the Flask app and the M365 MCP sidecar together.

### Windows

```powershell
docker compose up --build
```

### macOS

```bash
docker compose up --build
```

### Linux

```bash
docker compose up --build
```

> **Linux note:** On Linux, Docker Desktop's `host.docker.internal` DNS entry is not available by default. If you're running Ollama on the host and using `MODEL_PROVIDER=ollama`, add this to the `hr-app` service in `docker-compose.yml`:
> ```yaml
> extra_hosts:
>   - "host.docker.internal:host-gateway"
> ```

Once running, open **http://localhost:5000** in your browser.

To stop:

```bash
docker compose down
```

### After a code change

No need to remove containers — just rebuild:

```bash
docker compose up --build
```

---

## Running locally (without Docker)

Use this if you don't want to containerize the Flask app. The M365 MCP sidecar still needs Docker.

### 1. Start the MCP sidecar

```bash
docker compose up hr-mcp
```

### 2. Set up Python environment

**Windows (PowerShell)**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS / Linux**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure `.env`

Copy the example and fill in your values:

```bash
# .env is already present — edit MODEL_PROVIDER as needed
```

### 4. Start the Flask app

```powershell
python main.py
```

Open **http://localhost:5000** in your browser.

---

## Setting up Ollama (local model)

### Windows / macOS

Download and install from https://ollama.com, then pull the model:

```bash
ollama pull mistral
```

Ollama starts automatically as a background service after installation.

### Linux

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull mistral
```

---

## Project structure

```
hr-assistant/
├── main.py                         Flask app + web UI
├── requirements.txt
├── business_config.json            Company name, address, signatory details
├── Dockerfile                      Flask app container
├── docker-compose.yml              Orchestrates hr-app + hr-mcp
├── docker/
│   ├── Dockerfile                  M365 MCP server container
│   └── .env                        M365 tenant/client IDs
├── agents/
│   ├── coordinator_agent.py        Main pipeline orchestrator
│   ├── drafting_agent.py           Tree-of-Thought branch generation
│   ├── critic_agent.py             Branch scoring and selection
│   ├── retrieval_agent.py          Template + compliance retrieval
│   ├── communication_agent.py      Response formatting + review rules
│   └── model_client.py             Unified Ollama / Anthropic interface
├── tools/
│   ├── document_creator.py         .docx template filling
│   ├── mcp_client.py               Microsoft 365 MCP client
│   ├── vector_store.py             ChromaDB wrapper
│   └── compliance_refresh.py       Updates compliance reference data
├── templates/                      .docx templates (with {{PLACEHOLDERS}})
└── output/                         Generated documents (gitignored)
```

---

## Quick-start prompts

| What you want | Example prompt |
|---|---|
| Offer letter | `Draft an offer letter for Alex Johnson, salary $95,000, starting February 1, based in Maryland.` |
| Contractor agreement | `Create a contractor agreement for Jordan Smith to provide marketing consulting at $150/hour for 3 months in Virginia.` |
| Onboarding checklist | `Generate an onboarding checklist for a new project manager named Sarah Lee starting January 20, reporting to the CEO.` |
| Job description | `Write a job description for a Senior Account Manager, remote-friendly, salary $80,000–$100,000.` |
| Candidate email | `Draft a professional email to Marcus Williams to schedule a final interview next week.` |
| Compliance question | `What are the key differences between classifying someone as an employee versus an independent contractor in Maryland?` |

---

## Notes

- All documents are saved to the `output/` directory on the server (bind-mounted to the container when using Docker Compose, so files persist on your machine).
- Documents with salary figures or legal terms are always flagged **Review Required** before you can act on them.
- The UI draft preview and the downloaded `.docx` are generated from the same data — what you see is exactly what you get.
- Unfilled `{{PLACEHOLDER}}` fields are highlighted in the UI so you know exactly what still needs to be completed before sending.
