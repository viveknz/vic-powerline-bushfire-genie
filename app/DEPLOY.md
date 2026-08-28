# Deploying the App

Four files, all created directly in the Databricks workspace. No local build.

```
app.py               the Streamlit interface
genie_client.py      Genie Conversations API wrapper
app.yaml             Databricks Apps configuration
requirements.txt     dependencies
```

`test_genie_client.py` stays in the repository and is not deployed.

---

## Step 1 — Create the workspace folder

Workspace, then your user folder, then Create > Folder. Call it
`bushfire-app`.

Upload all four files into it, or create each with Create > File and paste.

**Keep `app.py` and `genie_client.py` in the same folder.** The import is flat,
so a subfolder will break it.

---

## Step 2 — Create the app

Switch to the **Databricks Apps** workspace from the top-right switcher, then
Create app, then Custom.

Name it `bushfire-exposure-console`. The name appears in the URL you will put in
the submission form, so pick something readable.

Point the source at the `bushfire-app` folder from step 1.

---

## Step 3 — Add resources

Two, and the first is the one that matters.

**Genie Agent.** Add your agent as a resource with key `genie-agent`.
Permission: CAN RUN.

**SQL warehouse.** Key `sql-warehouse`, permission CAN USE. Only used for the
four header statistics — the app still runs without it, the metrics just show
as dashes.

If resource binding by key gives trouble, edit `app.yaml` and set the value
directly:

```yaml
  - name: GENIE_SPACE_ID
    value: "01f1a2dfca9c105eab89635b24ad21ae"
```

Working beats elegant with a deadline on it.

---

## Step 4 — Grant the service principal access

Easy to forget, and the failure looks like a Genie bug rather than a
permissions one.

The app runs as its own service principal, which starts with no access to
anything. Grant it:

- `CAN RUN` on the Genie Agent (usually automatic via the resource)
- `SELECT` on `workspace.bushfire.v_segment_exposure`, `v_fire_history` and
  `v_segment_fire`
- `USE CATALOG` on `workspace` and `USE SCHEMA` on `bushfire`

Fastest route is SQL:

```sql
-- replace with the service principal id shown on the app page
GRANT USE CATALOG ON CATALOG workspace TO `<app-service-principal>`;
GRANT USE SCHEMA ON SCHEMA workspace.bushfire TO `<app-service-principal>`;
GRANT SELECT ON VIEW workspace.bushfire.v_segment_exposure TO `<app-service-principal>`;
GRANT SELECT ON VIEW workspace.bushfire.v_fire_history TO `<app-service-principal>`;
GRANT SELECT ON VIEW workspace.bushfire.v_segment_fire TO `<app-service-principal>`;
```

Views read underlying tables through the view owner, so granting on the views
is normally enough. If a query fails on permissions, grant SELECT on
`gold_segment_exposure`, `prep_fire` and `segment_fire_pairs` as well.

---

## Step 5 — Deploy

Click Deploy. First deployment installs dependencies and takes a few minutes.

Watch the logs. Streamlit prints its startup line when it is actually serving.

---

## Step 6 — Check it works

In this order, because each failure points somewhere different.

1. **Page loads.** If not, read the logs — usually a missing dependency or an
   import error.
2. **Header metrics show numbers, not dashes.** Dashes mean the warehouse
   resource or the SELECT grants are missing.
3. **Click a suggested question.** This is the real test. Expect status updates
   moving through "Thinking" and "Running the query", then an answer.
4. **Expand "Show the SQL Genie wrote".** Confirms the attachment parsing works.
5. **Ask a follow-up** such as "just the top three". Confirms conversation
   threading.
6. **Download CSV.**

---

## If something fails

**Blank page or import error** — check both `.py` files are in the same folder
and `requirements.txt` is present.

**"Genie is not configured"** — `GENIE_SPACE_ID` is empty. Check the resource
key matches `app.yaml`, or hardcode the value.

**Answers arrive but tables are empty** — this is the result-shape problem the
client already handles, so it is more likely a permissions issue on the
underlying views. Check the logs for "Could not fetch query result".

**Header dashes but chat works** — warehouse resource or grants. Not fatal.

**Timeouts** — a cold serverless warehouse can take a while on the first query.
The client waits 180 seconds. Ask a second question and it should be quick.

---

## Before recording the demo

- Run every suggested question once so the warehouse is warm. A cold start on
  camera looks like a broken app.
- Clear the conversation so the empty state with the question chips shows.
- Check the sidebar renders — that panel is what makes the semantic layer
  visible to a judge, and it is where most of the effort went.
