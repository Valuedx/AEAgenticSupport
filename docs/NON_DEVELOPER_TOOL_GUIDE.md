# Business Guide: Adding & Managing Tools (No-Code)

This guide is for **Operations Managers**, **Support Leads**, and **Business Owners** who want to add new capabilities to the AI Assistant without writing any Python code.

---

## Method 1: The Operations Control Center (easiest)

If a tool already exists but isn't working the way you want, or if it's hidden, you can manage it from your browser.

### 1. Access the Admin Portal
* **URL**: Open your browser and go to `[Your-App-URL]/admin` or `[Your-App-URL]/tools`.
* **Login**: Use your administrator credentials.

### 2. Manage the "Tools" Tab
Go to the **Tools** tab. Here you can:
* **Change Titles**: Give the tool a business-friendly name (e.g., change `ae.request.restart` to `Restart Failed Claim Process`).
* **Update Descriptions**: Explain what the tool does in simple language. This helps the AI understand when to use it better.
* **Control Visibility**: Use the **Active** toggle to turn a tool ON or OFF instantly.
* **Set Safety Tiers**: Change a tool from `read_only` to `medium_risk` if you want it to require a human approval before the AI runs it.

---

## Method 2: Mapping a Workflow as a Tool

If you have a workflow in the **AutomationEdge Platform** and you want the AI to be able to "run" it, you can map it as a tool.

### 1. In the AutomationEdge Portal
Navigate to your workflow's configuration page and find the **Metadata** or **Configuration Parameters** section.

### 2. Add the "Agentic Tool" Block
Copy and paste the following snippet into the configuration. You only need to change the parts in **bold**.

```json
{
  "AgenticToolConfiguration": {
    "toolName": "ProcessPerformanceReport",
    "tooldescription": "Generates a PDF performance report for a specific department.",
    "useWhen": "The user asks for a monthly performance report or a PDF summary of work.",
    "tier": "read_only",
    "active": true,
    "tags": ["report", "pdf", "performance", "analytics"]
  }
}
```

### 3. What happens next?
1. The AI Assistant will detect this change automatically (usually on the next sync or restart).
2. It will look at your **Workflow Parameters** in AutomationEdge.
3. It will automatically create a tool that asks the user for those exact parameters.

---

## Decision Matrix: Which method should I use?

| Goal | Use Method... |
| :--- | :--- |
| **Hide a tool** from the AI temporarily | **Control Center** (Toggle the "Active" switch) |
| **Make a tool safer** (Add an approval gate) | **Control Center** (Change Tier to "Medium Risk") |
| **Improve AI accuracy** (The AI keeps picking the wrong tool) | **Control Center** (Refine the "Use When" guidance) |
| **Add a brand new action** (Connect an AE Workflow) | **Workflow Mapping** (Add the JSON block in AE) |

---

## Pro-Tips for Business Owners
* **Description is Key**: The AI behaves like a human intern. If you give it a vague description, it will guess. If you give it a precise one, it will be accurate.
* **Use Tags**: Add synonyms to your tags (e.g., if it's a "ticket" tool, add tags like "case", "incident", "issue").
* **Dry Runs**: You can set a tool to "Dry Run" mode in some configurations to test how the AI handles it without actually performing the action.
