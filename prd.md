
# Product Requirements Document (PRD)

## 1. Project Overview

**Project Name:** ClaimVoice

**Tagline:** The AI First-Responder for Insurance Claims Intake.

**Elevator Pitch:** ClaimVoice eliminates call center hold times during high-stress situations. It is a real-time, voice-activated claims agent that speaks empathetically to distressed drivers, extracts structured incident data dynamically, and seamlessly orchestrates that data into an underwriting backend for immediate dispatch and risk assessment.

## 2. Core Problem & Solution

**The Problem:** When drivers get into an accident, they face high stress, long hold times, and repetitive manual data entry to file a First Notice of Loss (FNOL).
**The Solution:** An autonomous voice agent deployed via web or phone. Using AssemblyAI's Universal-3 model and HTTP Tool Calling, the agent conducts a natural conversation to extract policy numbers, locations, and drivability status, instantly firing a webhook to a Django backend that calculates risk and dispatches help.

## 3. System Architecture

This project utilizes a decoupled architecture optimized for rapid hackathon deployment.

* **Voice Client (Frontend):** Hosted on Vercel using AssemblyAI's official `voice-agent-starter-js`. This provides a ready-made browser UI for judges to test the microphone and speak to the agent.
* **The Brains (AssemblyAI):** The Agent is defined by a single JSON configuration file containing the system prompt and the `log_claim` HTTP tool.
* **The System of Record (Backend):** A monolithic Django application deployed on Render/Railway. It exposes an API webhook (`/api/log-claim/`) to receive AssemblyAI's tool-call payload, processes the data, and serves a Tailwind CSS dashboard (`/dashboard/`) for insurance dispatchers to view incoming claims in real-time.

## 4. Feature Scope (Hackathon MVP)

### Feature 1: Conversational Data Extraction

* **Requirement:** The agent must hold a natural, turn-by-turn conversation.
* **Logic:** It will prompt the user for their state of safety, policy number, incident type, location, and vehicle status.
* **Constraint:** It must *not* trigger the tool call until all required schema fields are confidently extracted from the audio.

### Feature 2: HTTP Tool Calling (Webhook Intercept)

* **Requirement:** Once data is gathered, AssemblyAI must execute a POST request to the Django backend.
* **Payload Schema:**
```json
{
  "policy_number": "string",
  "incident_type": "enum (collision | theft | weather)",
  "location": "string",
  "is_drivable": "boolean"
}

```


* **Response Handling:** Django must return a success string (e.g., *"Claim logged, dispatching tow to {location}"*), which the AssemblyAI agent will immediately read back to the user to close the loop.

### Feature 3: The Dispatcher Dashboard

* **Requirement:** A real-time visual interface for the insurance company.
* **Logic:** A Django view querying the local SQLite/PostgreSQL database, displaying claims as they arrive.
* **Bonus Logic:** Django will run a lightweight internal script on incoming webhooks to assign a `risk_score` (1-100) based on the incident parameters before saving it to the database.

## 5. Development Phases & Milestones

**Phase 1: Backend Setup (Django)**

* [ ] Initialize Django project and `claims` app.
* [ ] Create the `Claim` model (Policy Number, Type, Location, Drivable, Risk Score).
* [ ] Build the `@csrf_exempt` webhook view at `/api/log-claim/` to ingest JSON and save to the database.
* [ ] Build the `/dashboard/` HTML template using Tailwind CSS.
* [ ] Deploy Django to Render/Railway so the webhook URL is live on the internet.

**Phase 2: Agent Configuration**

* [ ] Create `agent.json` defining the persona, voice (e.g., "ivy"), and the HTTP tool pointing to the live Render webhook URL.
* [ ] Publish the agent via AssemblyAI API and save the generated `AGENT_ID`.

**Phase 3: Demo UI Setup**

* [ ] Fork `AssemblyAI/voice-agent-starter-js` on GitHub.
* [ ] Deploy to Vercel, injecting your `ASSEMBLYAI_API_KEY` and `AGENT_ID` into the environment variables.
* [ ] Test the end-to-end flow: Speak into Vercel frontend -> AssemblyAI extracts data -> hits Render Django webhook -> check Django Dashboard to see the new row.

## 6. Hackathon Submission Checklist

Before September 30th, ensure you have:

* [ ] **The Demo Link:** The Vercel URL where judges can talk to the agent.
* [ ] **The Dashboard Link:** The Render URL (e.g., `[yourapp.onrender.com/dashboard/](https://yourapp.onrender.com/dashboard/)`) where judges can see the database updating.
* [ ] **The Video Demo:** A crisp, 2-minute screen recording showing you speaking to the agent while having the Django dashboard open side-by-side, proving the database updates instantly when the tool is called.
* [ ] **The Codebase:** A clean GitHub repository containing your Django project and your `agent.json` file.

1. **Initialize Django:** Local development.
Run `django-admin startproject ClaimVoice` and `python manage.py startapp claims`.


2. **Write the Webhook:** In views.py.
Write the JSON-ingestion logic to catch the payload from AssemblyAI.


3. **Deploy Backend:** Render or Railway.
Push your Django code to GitHub and deploy it so AssemblyAI has a public URL to hit.