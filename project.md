Project Overview: Antigravity
AI-Powered Cross-Channel Ad Intelligence

1. Executive Summary
Antigravity is a decoupled web application designed to eliminate the manual, time-intensive process of cross-platform digital advertising reporting. By combining a robust Python-based ETL (Extract, Transform, Load) pipeline with Google’s advanced Gemini LLM, Antigravity ingests disparate raw data from major ad networks, normalizes it, and generates production-ready dashboards featuring both quantitative charts and qualitative, AI-driven strategic recommendations.

2. The Problem
Digital marketers and agencies run campaigns simultaneously across Google Ads, Meta (Facebook/Instagram), LinkedIn, and others.

Fragmented Data: Each platform exports performance data in entirely different CSV schemas, date formats, and naming conventions.

Manual Bottlenecks: Marketers spend hours each week manually downloading CSVs, mapping columns in Excel, and building pivot tables just to get a baseline view of cross-channel performance (Blended CPA, Total ROAS).

Analysis Paralysis: By the time the data is formatted, there is little time left for actual analysis and strategic pivot recommendations.

3. The Antigravity Solution
Antigravity acts as an automated data analyst. It transforms raw, chaotic data dumps into a unified, actionable narrative in seconds.

Core Features:

Universal Ingestion Engine: Users drag and drop raw CSVs directly from any ad platform. The system automatically detects the source and maps the data to a Universal Schema.

Automated Aggregation: Calculates complex cross-channel metrics automatically, aggregating data by date, campaign, and platform.

LLM-Powered Insights: Feeds the normalized quantitative data to the Gemini API to generate a qualitative, written narrative. It highlights hidden trends, compares platform efficiency, and outputs concrete budget-shift recommendations.

Interactive Dashboard: Presents the findings in a beautiful, highly scannable UI featuring interactive charts, KPI scorecards, and a formatted narrative report.

4. Technical Architecture
Antigravity utilizes a modern, decoupled Client-Server architecture designed for speed and scalability:

Frontend (Presentation): Built with React/Next.js and styled with Tailwind CSS. It handles user file uploads, renders interactive data visualizations (via Recharts), and formats the AI-generated markdown reports.

Backend (Data & AI Logic): Powered by Python and FastAPI. It acts as the orchestration layer.

Data Processing: Pandas is used to clean, map, and aggregate the raw CSV data.

AI Integration: The Google GenAI SDK (Gemini API) analyzes the aggregated data structures to generate human-readable insights.

5. Value Proposition
Time Savings: Reduces weekly reporting workflows from hours to seconds.

Enhanced Decision Making: Removes human bias and fatigue, allowing AI to spot cross-platform efficiencies that might otherwise be missed.

Scalability: Easily extensible to include new ad platforms (TikTok, Bing, Pinterest) by simply writing new Pandas mapping functions on the backend.

### 1. The Decoupled Tech Stack

#### The Frontend (Presentation Layer)

* **Framework:** **Next.js** (recommended for built-in routing and SEO if needed) or **Vite + React** (if you just want a blazing-fast Single Page Application).
* **Styling:** **Tailwind CSS** paired with a component library like **shadcn/ui** or **MUI** to build beautiful upload zones and dashboards quickly.
* **Data Visualization:** **Recharts** or **Nivo**. Both are native React charting libraries that are highly customizable and responsive. (Alternatively, you can use **Plotly.js** for React if you want to stick with the Plotly ecosystem).
* **Markdown Rendering:** `react-markdown`. Since Gemini will return its analysis in Markdown format, you'll need this to render the bolding, lists, and headers perfectly in your UI.

#### The Backend (Processing & AI Layer)

* **Framework:** **FastAPI**. It is currently the gold standard for building Python APIs. It's incredibly fast, handles asynchronous operations beautifully (which you need for calling LLM APIs), and auto-generates documentation.
* **Data Processing:** **Pandas** (or Polars) remains your ETL workhorse.
* **AI Engine:** **Gemini API** via the Google GenAI Python SDK.

---

### 2. The New Workflow (Client-Server Request Cycle)

Because you are splitting the app in two, the way data moves is a bit different. Here is the step-by-step lifecycle of a single user report generation:

#### Step 1: The UI Upload (React)

The user drags and drops their CSV files (Google Ads, Facebook Ads, etc.) into a dropzone on your React app. The frontend packages these files into a `FormData` object and makes a `POST` request to your FastAPI backend.

#### Step 2: Ingestion & ETL (FastAPI + Pandas)

Your FastAPI endpoint receives the files.

1. It loops through them, identifying the source platform.
2. It uses Pandas to rename the platform-specific columns to your Universal Schema.
3. It cleans the data (fixing date formats, handling currency, removing nulls).
4. It aggregates the data into two formats: one for the frontend charts, and one for Gemini.

#### Step 3: Generating the AI Insights (FastAPI + Gemini)

The backend takes the aggregated Pandas summaries, converts them to JSON, and injects them into a carefully crafted prompt for Gemini.

#### Step 4: The JSON Response payload

Your backend responds to the frontend's initial `POST` request with a structured JSON object. It should look something like this:

```json
{
  "status": "success",
  "chartData": [
    {"date": "2023-10-01", "google_cpa": 12.50, "fb_cpa": 15.20},
    {"date": "2023-10-08", "google_cpa": 11.00, "fb_cpa": 14.80}
  ],
  "scorecards": {
    "totalSpend": 45000,
    "blendedCPA": 13.40
  },
  "geminiAnalysis": "### Weekly Performance\nOverall spend increased by 10%, but **Google Ads** drove the most efficient CPA..."
}

```

#### Step 5: Rendering the Dashboard (React)

Your frontend receives this JSON and maps the data to your components:

* `scorecards` feed into your top-level KPI widgets.
* `chartData` is passed as the `data` prop into your `<Recharts>` components.
* `geminiAnalysis` is passed into your `<ReactMarkdown>` component to display the written report.

---

### 3. Key Design Considerations for this Architecture

* **Handling API Timeouts:** LLM calls can take 10–30 seconds to generate a full report. Browser HTTP requests might time out. You'll want to implement a loading state (spinners or skeleton loaders) on the React side. For production, you might even consider WebSockets or a polling mechanism (e.g., returning a `job_id` and having the frontend check back every 5 seconds until the report is ready).
* **CORS (Cross-Origin Resource Sharing):** Since your React app will likely run on `localhost:3000` and FastAPI on `localhost:8000` during development, you must configure CORS middleware in FastAPI to accept requests from your frontend.
* **Security:** Never put your Gemini API key in your React frontend code. It must live in your FastAPI backend environment variables (`.env` file) to keep it secure.

---

Would you like me to map out how the JSON payload should be structured for a library like Recharts, or would you prefer to see a basic FastAPI endpoint that handles a multi-file upload?