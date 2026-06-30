# HR Assistant — Architecture (C4 Model)

Four views following the [C4 model](https://c4model.com): Context → Containers → Components → Dynamic.

---

## Level 1 — System Context

Who uses the system and what external systems it touches.

```mermaid
C4Context
    title Level 1 — System Context

    Person(owner, "Business Owner", "Small business owner or HR manager. Submits requests, reviews generated documents, and approves outbound actions.")

    System(hrAssistant, "HR Assistant", "Generates HR documents (offer letters, contractor agreements, onboarding checklists, job descriptions), answers compliance questions for MD/DC/VA, and coordinates candidate communications.")

    System_Ext(m365, "Microsoft 365", "Outlook email, Exchange calendar, and OneDrive file storage.")
    System_Ext(ollama, "Ollama", "Local LLM runtime hosting Mistral. Fully offline — no data leaves the machine.")
    System_Ext(anthropic, "Anthropic API", "Cloud-hosted Claude models. Optional — used when MODEL_PROVIDER=anthropic.")

    Rel(owner, hrAssistant, "Submits requests, reviews documents, approves actions", "Browser / HTTP")
    Rel(hrAssistant, m365, "Saves documents, sends emails, creates calendar events", "JSON-RPC over HTTP")
    Rel(hrAssistant, ollama, "LLM inference (local mode)", "HTTP :11434")
    Rel(hrAssistant, anthropic, "LLM inference (API mode)", "HTTPS")

    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

---

## Level 2 — Container Diagram

The major deployable units, their responsibilities, and how they communicate.

```mermaid
C4Container
    title Level 2 — Container Diagram

    Person(owner, "Business Owner", "Small business owner or HR manager")

    Container_Boundary(sys, "HR Assistant") {
        Container(browser, "Browser UI", "HTML / JavaScript", "Served by Flask. Chat interface with quick-action buttons, draft preview, missing-field indicators, review gates, Download .docx, and Save to OneDrive controls.")
        Container(flaskApp, "Flask Web App", "Python 3.12 / Flask — Docker hr-app :5000", "Main application server. Hosts all agent logic and API routes: POST /api/request, GET /api/status, POST /api/download, POST /api/save-onedrive, POST /api/index-templates.")
        ContainerDb(chromaDB, "ChromaDB Vector Store", "ChromaDB — Docker volume chroma-data", "Two local collections: hr_templates (docx templates chunked by section heading) and compliance_sources (MD, DC, VA labor law references with retrieval date metadata).")
        ContainerDb(fileSystem, "File Storage", "Host filesystem — Docker bind mounts", "templates/ holds .docx templates with {{PLACEHOLDER}} tokens. output/ holds generated documents. business_config.json holds standing company details injected into every document.")
        Container(mcpServer, "M365 MCP Server", "Node.js — Docker hr-mcp :3000", "JSON-RPC 2.0 bridge to Microsoft Graph API. Handles Outlook, Calendar, and OneDrive on behalf of the Flask app. Stores OAuth tokens in a named Docker volume.")
    }

    System_Ext(ollama, "Ollama", "Local LLM runtime (Mistral) on host machine :11434")
    System_Ext(anthropic, "Anthropic API", "Cloud LLM (Claude) — used when MODEL_PROVIDER=anthropic")
    System_Ext(m365cloud, "Microsoft 365", "Outlook, Exchange Calendar, OneDrive via Microsoft Graph API")

    Rel(owner, browser, "Uses", "HTTPS")
    Rel(browser, flaskApp, "API calls", "HTTP / JSON")
    Rel(flaskApp, chromaDB, "Semantic search and template indexing", "ChromaDB Python SDK")
    Rel(flaskApp, fileSystem, "Reads .docx templates, writes generated documents")
    Rel(flaskApp, mcpServer, "Email, calendar, and file operations", "JSON-RPC / HTTP :3000")
    Rel(flaskApp, ollama, "LLM inference (local mode)", "HTTP :11434")
    Rel(flaskApp, anthropic, "LLM inference (API mode)", "HTTPS")
    Rel(mcpServer, m365cloud, "Microsoft Graph API calls", "HTTPS")

    UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

---

## Level 3 — Component Diagram: Flask Web App

The internal components of the `hr-app` container and how they relate.

```mermaid
C4Component
    title Level 3 — Component Diagram: Flask Web App (hr-app)

    Container_Boundary(app, "Flask Web App — hr-app") {
        Component(routes, "API Routes", "Flask / main.py", "HTTP endpoints bridging the browser to the agent pipeline. POST /api/request delegates to CoordinatorAgent; GET /api/status checks MCP availability and active model; POST /api/download and /api/save-onedrive handle document delivery.")
        Component(coordinator, "CoordinatorAgent", "Python / agents/coordinator_agent.py", "ReAct orchestrator. Classifies intent, extracts field values, loads business_config.json as standing defaults, and routes to document, email, scheduling, or Q&A workflow. Merges business config with request fields before drafting.")
        Component(retrieval, "RetrievalAgent", "Python / agents/retrieval_agent.py", "Searches two ChromaDB collections. Filters hr_templates by document type and compliance_sources by state (MD, DC, VA). Returns formatted context for the drafting prompt and compliance citation strings for the UI.")
        Component(drafting, "DraftingAgent", "Python / agents/drafting_agent.py", "Tree-of-Thought generator. Produces 3 branches per request using distinct styles (formal / concise / balanced). Each branch is a PLACEHOLDER→value dict sized to fill the field's exact sentence slot — not full prose. Includes defensive trimming to remove echoed template text.")
        Component(critic, "CriticAgent", "Python / agents/critic_agent.py", "Scores all 3 branches on four 0–3 criteria: compliance, template alignment, clarity, completeness (12 pts total). Prunes branches with a compliance score of 0 or two or more zero scores. Returns the highest-scoring viable branch with full scoring metadata.")
        Component(communication, "CommunicationAgent", "Python / agents/communication_agent.py", "Formats the pipeline result for the UI. Enforces review gates: offer letters and contractor agreements always require owner approval. Executes approved M365 actions (send email, create calendar event, save to OneDrive) via MCPClient.")
        Component(modelClient, "ModelClient", "Python / agents/model_client.py", "Single chat() function used by all agents. Routes to Ollama (Mistral, local) or Anthropic API (Claude) based on MODEL_PROVIDER env var. Returns plain text.")
        Component(vectorStore, "VectorStore", "Python / tools/vector_store.py", "Wraps two ChromaDB collections using the default sentence-transformer embedding function (fully local). Indexes .docx templates chunked by section heading. Exposes retrieve_templates() and retrieve_compliance() for semantic search.")
        Component(docCreator, "DocumentCreator", "Python / tools/document_creator.py / python-docx", "Fills {{PLACEHOLDER}} tokens in .docx templates while preserving Word run formatting. Also: extracts placeholder-in-sentence context maps for DraftingAgent, renders plain-text UI previews from the same field values used for .docx generation, and lists unfilled placeholders for missing-field detection.")
        Component(mcpClient, "MCPClient", "Python / tools/mcp_client.py / httpx", "JSON-RPC 2.0 client for the M365 MCP sidecar at port 3000. Operations: send_email, create_draft_email, create_calendar_event, upload_file (OneDrive), download_file, list_onedrive_files. Exposes is_available() for the /api/status probe.")
    }

    ContainerDb(chromaDB, "ChromaDB Vector Store", "Persistent local embeddings")
    ContainerDb(fileStore, "File Storage", "templates/ and output/")
    Container_Ext(mcpServer, "M365 MCP Server", "Node.js Docker container :3000")
    System_Ext(llm, "LLM Provider", "Ollama (local) or Anthropic API")

    Rel(routes, coordinator, "handle_request(user_input)")
    Rel(coordinator, retrieval, "retrieve(query, state, doc_type)")
    Rel(coordinator, drafting, "generate_branches(task, context, field_contexts, known_fields)")
    Rel(coordinator, critic, "evaluate_and_select(branches, task, compliance_context)")
    Rel(coordinator, communication, "format_response() / should_require_review(doc_type)")
    Rel(coordinator, docCreator, "get_placeholder_contexts(), create_*(), render_text_preview(), list_unfilled_placeholders()")
    Rel(coordinator, modelClient, "chat() — classify and extract fields")
    Rel(retrieval, vectorStore, "retrieve_templates(), retrieve_compliance()")
    Rel(drafting, modelClient, "chat() — once per branch (3× per request)")
    Rel(critic, modelClient, "chat() — once per branch (3× per request)")
    Rel(communication, mcpClient, "upload_file(), send_email(), create_calendar_event()")
    Rel(vectorStore, chromaDB, "Read and write embeddings")
    Rel(docCreator, fileStore, "Read .docx templates, write .docx output")
    Rel(modelClient, llm, "Text generation requests")
    Rel(mcpClient, mcpServer, "JSON-RPC 2.0 calls")
```

---

## Dynamic View — Document Request Flow

Runtime sequence for a document request (e.g., an offer letter). This is the full Tree-of-Thought pipeline used for all four document types.

```mermaid
sequenceDiagram
    actor Owner as Business Owner
    participant UI as Browser UI
    participant Coord as CoordinatorAgent
    participant Ret as RetrievalAgent
    participant VS as VectorStore
    participant Draft as DraftingAgent
    participant Critic as CriticAgent
    participant DC as DocumentCreator
    participant Comm as CommunicationAgent
    participant LLM as ModelClient

    Owner->>UI: Submit prompt ("Draft offer letter for Alex Johnson, $95k, starting Feb 1, Maryland")
    UI->>Coord: POST /api/request { input: "..." }

    Note over Coord,LLM: Step 1 — Classify intent
    Coord->>LLM: Classify request into workflow category
    LLM-->>Coord: "offer_letter"

    Note over Coord,LLM: Step 2 — Extract field values
    Coord->>LLM: Extract named fields from request text
    LLM-->>Coord: {candidate_name: "Alex Johnson", salary: "$95,000", state: "Maryland", start_date: "February 1"}

    Note over Coord,VS: Step 3 — Retrieve template and compliance context
    Coord->>Ret: retrieve(query, state="Maryland", doc_type="offer_letter")
    Ret->>VS: Query hr_templates (filter: offer_letter_template)
    Ret->>VS: Query compliance_sources (filter: state=Maryland)
    VS-->>Ret: Matching template sections + MD labor law references
    Ret-->>Coord: Formatted context string + compliance_notes[]

    Note over Coord,DC: Step 4 — Get placeholder sentence context map
    Coord->>DC: get_placeholder_contexts(offer_letter_template.docx)
    DC-->>Coord: {CANDIDATE_NAME: "Dear {{CANDIDATE_NAME}},", SALARY: "annual salary of {{SALARY}}", ...}

    Note over Coord,Draft: Step 5 — Tree-of-Thought drafting (3 parallel branches)
    loop Branch 1 (formal), Branch 2 (concise), Branch 3 (balanced)
        Coord->>Draft: generate_branch(task, context, field_contexts, known_fields, style)
        Draft->>LLM: Draft a value fragment for each unfilled placeholder
        LLM-->>Draft: {START_DATE: "February 1, 2026", BENEFITS_SUMMARY: "standard benefits package", ...}
        Draft-->>Coord: Branch dict — PLACEHOLDER → drafted value
    end

    Note over Coord,Critic: Step 6 — Score and select best branch
    loop Branch 1, 2, 3
        Coord->>Critic: evaluate_and_select(branch, task, compliance_context)
        Critic->>LLM: Score on compliance / template alignment / clarity / completeness (0–3 each)
        LLM-->>Critic: {scores: {compliance:3, template_alignment:2, clarity:3, completeness:2}, pruned:false}
        Critic-->>Coord: Scored branch (total /12)
    end
    Coord->>Coord: Prune weak branches, select highest-scoring viable branch

    Note over Coord,DC: Step 7 — Generate .docx and UI preview
    Coord->>DC: create_offer_letter(merged_fields)
    DC-->>Coord: output/alex_johnson_offer_letter_2026-06-29.docx
    Coord->>DC: render_text_preview(template, merged_fields)
    DC-->>Coord: Plain-text preview (same field values as the .docx)

    Note over Coord,Comm: Step 8 — Apply review gate and format response
    Coord->>Comm: should_require_review("offer_letter")
    Comm-->>Coord: (requires_review=true, "Contains compensation/legal terms")
    Coord->>Comm: format_response(result)
    Comm-->>Coord: {summary, draft, document_path, requires_review, review_reason, missing_fields, compliance_notes, score_info}

    Coord-->>UI: JSON response
    UI-->>Owner: Draft preview + "⚠ Review Required" badge + Download .docx button
```

---

### Notes

- **Email, scheduling, and Q&A workflows** skip steps 4–6 (no ToT pipeline). The coordinator calls the LLM directly for a single draft and routes through `CommunicationAgent` with `requires_review=true`.
- **Model provider is runtime-configurable** via `MODEL_PROVIDER` in `.env`. All agents call the same `chat()` function; the switch between Ollama (local) and Anthropic (API) is transparent to the pipeline.
- **Review gates are non-bypassable in the pipeline** — `CommunicationAgent.should_require_review()` is always called for document workflows; the UI download and OneDrive save buttons are only enabled after the owner sees the review badge.