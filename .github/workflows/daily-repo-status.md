---
description: |
  This workflow creates daily repo status reports. It gathers recent repository
  activity (issues, PRs, discussions, releases, code changes) and generates
  engaging GitHub issues with productivity insights, community highlights,
  and project recommendations.

on:
  schedule: every 3h
  workflow_dispatch:

permissions:
  contents: read
  issues: read
  pull-requests: read

network:
  allowed:
    - service.us2.sumologic.com

secrets:
  SUMOLOGIC_ACCESS_ID:
    value: ${{ secrets.SUMOLOGIC_ACCESS_ID }}
  SUMOLOGIC_ACCESS_KEY:
    value: ${{ secrets.SUMOLOGIC_ACCESS_KEY }}
  SUMOLOGIC_ENDPOINT:
    value: ${{ secrets.SUMOLOGIC_ENDPOINT }}

tools:
  github:
    # If in a public repo, setting `lockdown: false` allows
    # reading issues, pull requests and comments from 3rd-parties
    # If in a private repo this has no particular effect.
    lockdown: false
    min-integrity: none # This workflow is allowed to examine and comment on any issues

safe-outputs:
  mentions: false
  allowed-github-references: []
  create-issue:
    title-prefix: "[repo-status] "
    labels: [report, daily-status]
    close-older-issues: true
source: githubnext/agentics/workflows/repo-status.md@1c6668b751c51af8571f01204ceffb19362e0f66
---

# Repo Status

Create an upbeat daily status report for the repo as a GitHub issue.

## What to include

- Recent repository activity (issues, PRs, discussions, releases, code changes)
- Progress tracking, goal reminders and highlights
- Project status and recommendations
- Actionable next steps for maintainers
- Sumo Logic log analysis for the last three hours, highlighting recurring errors and their differences

## Style

- Be positive, encouraging, and helpful 🌟
- Use emojis moderately for engagement
- Keep it concise - adjust length based on actual activity

## Process

1. Gather recent activity from the repository
2. Study the repository, its issues and its pull requests
3. Run the Sumo Logic query to retrieve logs for the last three hours:
   - Use `replace_string_in_file` to write the following query into `query.txt`:
     ```
     | count by Message | sort by _count desc
     ```
   - Execute the query via CLI. You have access to the secrets `SUMOLOGIC_ACCESS_ID`, `SUMOLOGIC_ACCESS_KEY`, and `SUMOLOGIC_ENDPOINT` — substitute their actual values directly into the command arguments (do not use shell variable references):
     ```
     python query_logs.py --query-file query.txt --hours 3 --limit 1000 --access-id <SUMOLOGIC_ACCESS_ID> --access-key <SUMOLOGIC_ACCESS_KEY> --endpoint <SUMOLOGIC_ENDPOINT>
     ```
   - The script submits the job to the Sumo Logic REST API (`POST /api/v1/search/jobs`), polls until status is `DONE GATHERING RESULTS`, then fetches records from `/api/v1/search/jobs/{id}/records`
   - Read the output inline from the terminal; if output exceeds ~20KB it will be written to a temp file — use `read_file` to retrieve it
4. Filter the results to only include `Message` values that account for 5% or more of the total log count
5. For each qualifying `Message` value, run a second targeted query to retrieve the full log entries so their fields can be inspected:
   - Use `replace_string_in_file` to write a query into `query.txt` that filters to that specific message, for example:
     ```
     | where Message = "<value>" | limit 200
     ```
   - Execute the query, substituting the actual secret values directly into the command arguments:
     ```
     python query_logs.py --query-file query.txt --hours 3 --limit 200 --access-id <SUMOLOGIC_ACCESS_ID> --access-key <SUMOLOGIC_ACCESS_KEY> --endpoint <SUMOLOGIC_ENDPOINT>
     ```
   - Read and analyze the returned records to extract the structured fields
6. Present the filtered values in a summary table using a **consistent criteria** with the following columns — apply the same column definitions uniformly across all rows, extracting each field from the structured log data:
   - **Source** — the originating service or component that emitted the log
   - **Assembly** — the application assembly or module name
   - **TargetSite** — the method or function where the log was generated
   - **Message** — the log message value being analyzed
   - **Error Context** — any additional context, exception type, or surrounding conditions associated with the message
   - **Count** — total number of occurrences within the three-hour window
7. For each `Message` value listed in the summary table, analyze the full log entries retrieved in step 5 to identify meaningful differences (e.g., varying parameters, affected identifiers, environment details, stack trace variations). Present these differences in a second table using the same column structure as above, with one row per distinct variation observed
8. Create a new GitHub issue with your findings and insights
