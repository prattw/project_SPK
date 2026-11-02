# Review Desk

A separate app from Project SPK. Project SPK stays the online document app on Railway. Review Desk runs on the Mac mini and helps with ProjNet DrChecks comments, evaluations, and backchecks.

ProjNet remains the system of record. This app does not log into ProjNet and does not file anything. You export a review as XML, draft the text here, and paste it back.

Comments in a real review are CUI. The model is Ollama on this Mac. The server refuses any model URL that is not localhost, including `api.openai.com`.

## What it does

- Import a DrChecks XML export and list the comments.
- Draft a comment, an evaluation, or a backcheck from the comment that is already there, plus a note you type.
- Start a new comment from notes. A clash description pasted from Navisworks can be that note. Finding the clash is still Navisworks; this app writes the comment.
- Check publication numbers in the text. ER, EM, EP, EC, ETL, and AR numbers are looked up in the local USACE publications list. UFC and UFGS are not in that list, so the app tells you to confirm them on WBDG. It will not declare a UFC inactive on its own.

Drawing compliance and geometric clash detection are not this app. A local model can draft from what you read on a sheet. It should not be treated as a check of dimensions.

## Setup on the Mac mini

While online:

```bash
chmod +x drchecks/setup_mac.sh drchecks/start.sh
./drchecks/setup_mac.sh
./drchecks/start.sh
```

Open <http://127.0.0.1:8010>. A sample export is in `drchecks/samples/sample_review.xml`.

The app listens only on this Mac. Do not set `DRCHECKS_HOST` to `0.0.0.0`, and do not set `OLLAMA_HOST` to `0.0.0.0`.

Try `drchecks/samples/sample_review.xml` before a real export. Real XML element names vary by report; if an export fails to import, the message says no comments were found.
