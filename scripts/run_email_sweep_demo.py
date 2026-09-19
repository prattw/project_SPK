#!/usr/bin/env python3
"""Run Project SPK locally with a stubbed model and sample email, for UI checks.

Fills a temporary folder with realistic USACE email, points the local_folder
connector at it, and replaces the model call with canned responses so the
Email Assistant can be exercised end to end without an API key or a mailbox.

    python scripts/run_email_sweep_demo.py [port]
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEMO_USER = "william.a.pratt@usace.army.mil"

# The roster stays on so the sign-in screen behaves as it does in production.
os.environ["ACCESS_ROSTER"] = DEMO_USER
os.environ["APP_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = "demo-key-not-used"
os.environ["OPENAI_BASE_URL"] = "http://localhost:11434/v1"  # look like a self-hosted model
os.environ["WARM_INDEX_ON_STARTUP"] = "false"

SAMPLES: list[tuple[str, str, str, int, str]] = [
    (
        "submittal.eml",
        "Submittal 03 30 00 — concrete mix design review period",
        "j.alvarez@westbaycontractors.com",
        5,
        "Good morning,\n\n"
        "We submitted the concrete mix design for 03 30 00 on 16 September. Our schedule "
        "assumes a 21-day review. Can you confirm the Government review period, and whether "
        "the compressive strength data we provided is sufficient?\n\n"
        "The placement is currently planned for 12 October and we cannot slip it without a "
        "time extension request.\n\n"
        "Respectfully,\nJ. Alvarez\nProject Manager, West Bay Contractors",
    ),
    (
        "coordination.eml",
        "Coordination call on the Merced levee tie-in",
        "dana.whitfield@usace.army.mil",
        11,
        "Hi,\n\n"
        "Can we get on a call Thursday at 10:00 to walk through the levee tie-in detail at "
        "station 42+50? Geotech has questions about the cutoff wall depth and I would like "
        "Design and Construction in the same room.\n\n"
        "I will send a Teams link once we settle the time.\n\nThanks,\nDana",
    ),
    (
        "mod.eml",
        "P00003 — differing site conditions, response due 24 September",
        "contracts@westbaycontractors.com",
        26,
        "Contracting Officer,\n\n"
        "Reference our letter dated 14 September regarding differing site conditions at the "
        "north abutment. Per FAR 52.236-2 we request a decision. Our position is that the "
        "rock elevation encountered differs materially from the geotechnical report.\n\n"
        "A response is requested by 24 September 2026.\n\n"
        "Regards,\nContracts Department",
    ),
    (
        "newsletter.eml",
        "SPK District weekly safety bulletin",
        "safety.bulletin@usace.army.mil",
        40,
        "This week's bulletin covers heat illness prevention, ladder inspection reminders, "
        "and the updated EM 385-1-1 activity hazard analysis template.\n\n"
        "No action required.",
    ),
    (
        "rfi.eml",
        "RFI 014 — conduit routing conflict in Building 3",
        "k.tanaka@westbaycontractors.com",
        62,
        "Team,\n\n"
        "RFI 014 flags a conflict between the electrical conduit routing on E-301 and the "
        "structural beam at grid C-4. We propose routing below the beam with a 2-inch "
        "clearance. Please confirm acceptability so we can release the conduit order.\n\n"
        "Thanks,\nK. Tanaka",
    ),
]

_ANALYSES: dict[str, str] = {
    "submittal": (
        '{"summary": "West Bay Contractors submitted the 03 30 00 concrete mix design on 16 September and '
        'asks the Government to confirm the review period and whether the compressive strength data is '
        'sufficient. They state a 12 October placement cannot slip without a time extension request.",'
        '"key_points": ["Mix design for 03 30 00 submitted 16 September.", "Contractor assumes a 21-day '
        'Government review.", "Placement planned for 12 October."],'
        '"action_items": [{"action": "Confirm the Government review period for submittal 03 30 00", '
        '"owner": "you", "due": "2026-09-24"}, {"action": "Determine whether the compressive strength data '
        'is complete", "owner": "Materials engineer", "due": "none stated"}],'
        '"open_questions": ["Is the submitted compressive strength data sufficient for approval?"],'
        '"deadlines": ["12 October 2026 concrete placement"],'
        '"priority": "high", "priority_reason": "A placement date and a possible time extension request '
        'turn on the answer.", "category": "submittal review", "reply_needed": true,'
        '"reply_needed_reason": "The contractor asked two direct questions and cited a schedule impact.",'
        '"suggested_next_step": "Confirm the review period in writing and state what the mix design still needs.",'
        '"note": {"title": "Submittal 03 30 00 review period inquiry", "body": "West Bay asked the Government '
        'to confirm the review period for the 03 30 00 concrete mix design submitted 16 September, and whether '
        'the compressive strength data is adequate. They tied the answer to a 12 October placement and raised '
        'the possibility of a time extension request.", "decisions": ["Contractor asserts a 21-day review '
        'period applies."], "followups": ["Confirm the review period in writing before 24 September."]},'
        '"meeting": {"needed": false}}'
    ),
    "coordination": (
        '{"summary": "Dana Whitfield proposes a call Thursday at 10:00 to resolve the Merced levee tie-in '
        'detail at station 42+50, with Design and Construction both present. Geotech has questions about '
        'cutoff wall depth.",'
        '"key_points": ["Tie-in detail at station 42+50 is unresolved.", "Geotech questions the cutoff wall depth."],'
        '"action_items": [{"action": "Accept or propose a time for the tie-in coordination call", "owner": "you", '
        '"due": "before Thursday"}],'
        '"open_questions": ["What cutoff wall depth does Geotech consider adequate?"],'
        '"deadlines": [],'
        '"priority": "medium", "priority_reason": "A design detail is blocked but no contractual date is at risk yet.",'
        '"category": "scheduling", "reply_needed": true,'
        '"reply_needed_reason": "A meeting time was proposed and needs confirming.",'
        '"suggested_next_step": "Confirm Thursday at 10:00 and ask Geotech to bring the cutoff wall analysis.",'
        '"note": {"title": "Merced levee tie-in coordination", "body": "Dana Whitfield requested a coordination '
        'call on the levee tie-in detail at station 42+50, with Design and Construction together, to resolve '
        'Geotech questions on cutoff wall depth.", "decisions": [], "followups": ["Confirm attendance and agenda."]},'
        '"meeting": {"needed": true, "title": "Merced levee tie-in coordination (Sta 42+50)", '
        '"start": "__NEXT_THURSDAY__T10:00", "duration_minutes": 60, "location": "Microsoft Teams", '
        '"agenda": ["Levee tie-in detail at station 42+50", "Cutoff wall depth questions from Geotech", '
        '"Design and Construction alignment"], "attendees": ["dana.whitfield@usace.army.mil"], '
        '"reason": "The thread proposes a specific time and names the participants needed."}}'
    ),
    "mod": (
        '{"summary": "West Bay Contractors requests a Contracting Officer decision on a differing site '
        'conditions claim at the north abutment, citing FAR 52.236-2 and asserting the rock elevation '
        'encountered differs materially from the geotechnical report. A response is requested by 24 September 2026.",'
        '"key_points": ["Claim concerns rock elevation at the north abutment.", "Contractor cites FAR 52.236-2.", '
        '"References their letter dated 14 September."],'
        '"action_items": [{"action": "Issue a Contracting Officer response on the differing site conditions claim", '
        '"owner": "Contracting Officer", "due": "2026-09-24"}, {"action": "Compare as-encountered rock elevation '
        'against the geotechnical report", "owner": "Geotech", "due": "before 24 September"}],'
        '"open_questions": ["Does the encountered rock elevation differ materially from the baseline report?"],'
        '"deadlines": ["24 September 2026 response requested"],'
        '"priority": "high", "priority_reason": "A contractual response date under FAR 52.236-2 is days away.",'
        '"category": "contract modification", "reply_needed": true,'
        '"reply_needed_reason": "A formal decision was requested with a stated due date.",'
        '"suggested_next_step": "Acknowledge receipt and state when the decision will issue.",'
        '"note": {"title": "P00003 differing site conditions — response due 24 Sep", "body": "West Bay requested '
        'a Contracting Officer decision on a differing site conditions claim at the north abutment under FAR '
        '52.236-2, asserting the encountered rock elevation differs materially from the geotechnical report. '
        'Response requested by 24 September 2026.", "decisions": [], "followups": ["Coordinate the Geotech '
        'comparison before the 24 September response."]},'
        '"meeting": {"needed": true, "title": "Block time: P00003 differing site conditions response", '
        '"start": "__TOMORROW__T13:00", "duration_minutes": 90, "location": "", '
        '"agenda": ["Review the 14 September letter", "Geotech comparison of rock elevation", '
        '"Draft the Contracting Officer response"], "attendees": [], '
        '"reason": "A contractual response is due 24 September and needs dedicated time."}}'
    ),
    "newsletter": (
        '{"summary": "The SPK District weekly safety bulletin covers heat illness prevention, ladder '
        'inspection reminders, and an updated EM 385-1-1 activity hazard analysis template. It states no '
        'action is required.",'
        '"key_points": ["Updated EM 385-1-1 activity hazard analysis template is available."],'
        '"action_items": [], "open_questions": [], "deadlines": [],'
        '"priority": "low", "priority_reason": "Informational bulletin that explicitly requires no action.",'
        '"category": "informational", "reply_needed": false,'
        '"reply_needed_reason": "No question was asked and the bulletin states no action is required.",'
        '"suggested_next_step": "Note the updated AHA template for the next submittal cycle.",'
        '"note": {"title": "Weekly safety bulletin — EM 385-1-1 AHA template updated", "body": "The district '
        'safety bulletin noted an updated EM 385-1-1 activity hazard analysis template along with heat illness '
        'and ladder inspection reminders.", "decisions": [], "followups": []},'
        '"meeting": {"needed": false}}'
    ),
    "rfi": (
        '{"summary": "RFI 014 reports a conflict between electrical conduit routing on E-301 and the '
        'structural beam at grid C-4. The contractor proposes routing below the beam with 2 inches of '
        'clearance and needs confirmation to release the conduit order.",'
        '"key_points": ["Conflict is at grid C-4 between E-301 conduit and a structural beam.", '
        '"Proposed resolution is routing below the beam with 2-inch clearance."],'
        '"action_items": [{"action": "Confirm acceptability of routing conduit below the beam at grid C-4", '
        '"owner": "Design", "due": "none stated"}],'
        '"open_questions": ["Does 2 inches of clearance satisfy the structural and electrical requirements?"],'
        '"deadlines": [],'
        '"priority": "medium", "priority_reason": "A material order is waiting but no date was stated.",'
        '"category": "RFI", "reply_needed": true,'
        '"reply_needed_reason": "The contractor asked for confirmation before releasing an order.",'
        '"suggested_next_step": "Route RFI 014 to structural and electrical for concurrence.",'
        '"note": {"title": "RFI 014 conduit routing conflict at grid C-4", "body": "RFI 014 identifies a '
        'conflict between conduit routing on E-301 and the structural beam at grid C-4. The contractor '
        'proposes routing below the beam with 2-inch clearance, pending Government confirmation to release '
        'the conduit order.", "decisions": [], "followups": ["Obtain structural and electrical concurrence."]},'
        '"meeting": {"needed": false}}'
    ),
}

_REPLIES: dict[str, str] = {
    "submittal": (
        "Good morning Mr. Alvarez,\n\n"
        "Thank you for the 16 September submission of the 03 30 00 concrete mix design. The Government "
        "review period runs [confirm: 21 days per the contract submittal register], which places our "
        "response on or before [confirm date].\n\n"
        "On the compressive strength data, we are reviewing whether the submitted results cover the full "
        "set of required ages and mix variations. If anything is missing we will identify it in a single "
        "consolidated comment rather than in successive rounds, so that your 12 October placement is not "
        "affected more than necessary.\n\n"
        "If the review will not support the 12 October date, we will advise you in writing as soon as we "
        "know, so you can evaluate your options under the contract.\n\n"
        "Respectfully,\nW. Pratt\nU.S. Army Corps of Engineers, Sacramento District"
    ),
    "coordination": (
        "Hi Dana,\n\n"
        "Thursday at 10:00 works. I have put it on my calendar and will plan for one hour so we have room "
        "for the cutoff wall discussion.\n\n"
        "Could you ask Geotech to bring their cutoff wall depth analysis for station 42+50, along with any "
        "boring logs in that reach? Having the data in the meeting will save us a second session. I will "
        "make sure Construction attends.\n\n"
        "Send the Teams link when you have it.\n\n"
        "Thanks,\nW. Pratt"
    ),
    "mod": (
        "Contracts Department,\n\n"
        "This acknowledges receipt of your correspondence requesting a Contracting Officer decision "
        "regarding differing site conditions at the north abutment, referencing your letter of "
        "14 September 2026 and FAR 52.236-2.\n\n"
        "The Government is reviewing the as-encountered rock elevation against the baseline geotechnical "
        "report. We will provide a response by [confirm date]. Nothing in this acknowledgment constitutes "
        "a determination on entitlement, and no direction to proceed or change in the contract is "
        "authorized by this message.\n\n"
        "Please continue to document conditions at the abutment and preserve any supporting survey data.\n\n"
        "Respectfully,\nW. Pratt\nU.S. Army Corps of Engineers, Sacramento District"
    ),
    "rfi": (
        "Mr. Tanaka,\n\n"
        "Thank you for RFI 014. We have routed the proposed conduit routing below the beam at grid C-4 to "
        "both structural and electrical for concurrence. Please do not release the conduit order until we "
        "confirm, since a change after fabrication would be at your risk.\n\n"
        "To speed the review, please confirm [the conduit size and quantity affected] and whether the "
        "2-inch clearance is measured to the bottom of the beam or to the fireproofing.\n\n"
        "Respectfully,\nW. Pratt"
    ),
}


def _resolve_dates(text: str) -> str:
    """Point the canned meeting times at real upcoming dates."""
    now = datetime.now()
    days_ahead = (3 - now.weekday()) % 7 or 7  # next Thursday
    return text.replace(
        "__NEXT_THURSDAY__", (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    ).replace("__TOMORROW__", (now + timedelta(days=1)).strftime("%Y-%m-%d"))


def _topic(prompt: str) -> str:
    lowered = prompt.lower()
    for key, marker in (
        ("submittal", "mix design"),
        ("coordination", "levee tie-in"),
        ("mod", "differing site conditions"),
        ("newsletter", "safety bulletin"),
        ("rfi", "conduit routing"),
    ):
        if marker in lowered:
            return key
    return "newsletter"


def stub_chat(messages, *, temperature=0.35):
    prompt = messages[-1]["content"]
    topic = _topic(prompt)
    if "Draft a reply" in prompt:
        return _REPLIES.get(topic, "Thank you for your message.\n\nRespectfully,\nW. Pratt")
    return _resolve_dates(_ANALYSES[topic])


def main() -> int:
    import uvicorn

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8010
    # "manual" exercises the default posture: no mail source, user supplies files.
    connector = sys.argv[2] if len(sys.argv) > 2 else "local_folder"
    folder = Path(tempfile.mkdtemp(prefix="spk-demo-mail-"))
    now = datetime.now(timezone.utc)
    for name, subject, sender, hours_ago, body in SAMPLES:
        (folder / name).write_bytes(
            (
                f"From: {sender}\r\nTo: {DEMO_USER}\r\n"
                f"Subject: {subject}\r\n"
                f"Date: {format_datetime(now - timedelta(hours=hours_ago))}\r\n"
                f"Message-ID: <{name}@demo>\r\n"
                'MIME-Version: 1.0\r\nContent-Type: text/plain; charset="utf-8"\r\n\r\n'
                f"{body}\r\n"
            ).encode()
        )

    os.environ["OUTLOOK_CONNECTOR"] = connector
    os.environ["OUTLOOK_LOCAL_FOLDER"] = str(folder) if connector == "local_folder" else ""

    from app import email_assistant

    email_assistant.chat_completion = stub_chat

    from app.main import app

    print(f"Connector: {connector}")
    print(f"Demo mail folder: {folder} ({len(SAMPLES)} messages)")
    print(f"Sign in as: {DEMO_USER}")
    print(f"Serving on http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
