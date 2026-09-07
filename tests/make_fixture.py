"""Generate a synthetic LinkedIn-shaped alert email.

A stand-in until you run `python -m job_radar save-fixtures` and commit real
ones. It mirrors the structure that matters to the parser: table-nested cards,
`/comm/jobs/view/<id>` links carrying tracking parameters, a logo anchor and a
title anchor pointing at the same job, and a company/location line.
"""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

CARD = """
<table><tr><td>
  <table><tr>
    <td><a href="https://www.linkedin.com/comm/jobs/view/{job_id}/?trackingId=abc%3D%3D&refId=xyz"><img src="https://media.licdn.com/logo.png"></a></td>
    <td>
      <p><a href="https://www.linkedin.com/comm/jobs/view/{job_id}/?trackingId=abc%3D%3D&midToken=zz&trk=eml-email_job_alert_digest">{title}</a></p>
      <p>{company} &middot; {location}</p>
      <p>{salary}</p>
      <p>{age} &middot; {applicants} applicants</p>
      <p>Easy Apply</p>
    </td>
  </tr></table>
</td></tr></table>
"""

CARDS = [
    dict(
        job_id="4021998877",
        title="Python Developer",
        company="Northwind Analytics",
        location="Bengaluru, Karnataka, India (Hybrid)",
        salary="8 - 14 LPA",
        age="2 days ago",
        applicants="43",
    ),
    dict(
        job_id="4033112244",
        title="Senior Data Engineer",
        company="Cobalt Systems",
        location="Remote, India (Remote)",
        salary="",
        age="21 hours ago",
        applicants="12",
    ),
    dict(
        job_id="4044556677",
        title="Sales Development Representative",
        company="Brightline Media",
        location="Pune, Maharashtra, India (On-site)",
        salary="",
        age="6 days ago",
        applicants="310",
    ),
]

HTML = """<html><body>
<p>&zwnj;&zwnj; Your job alert for python developer</p>
<h2>12 new jobs match your preferences</h2>
{cards}
<p><a href="https://www.linkedin.com/comm/jobs/search/?keywords=python">See all jobs</a></p>
<p><a href="https://www.linkedin.com/comm/psettings/email-unsubscribe">Unsubscribe</a></p>
</body></html>"""

TEXT = """Python Developer
Northwind Analytics - Bengaluru, Karnataka, India (Hybrid)
2 days ago
View job: https://www.linkedin.com/comm/jobs/view/4021998877/?trackingId=abc

Senior Data Engineer
Cobalt Systems - Remote, India (Remote)
21 hours ago
View job: https://www.linkedin.com/comm/jobs/view/4033112244/?trackingId=def
"""


def build(path: Path) -> Path:
    msg = EmailMessage()
    msg["Subject"] = '12 new jobs for "python developer"'
    msg["From"] = "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>"
    msg["To"] = "you@gmail.com"
    msg["Date"] = "Mon, 07 Sep 2026 01:32:11 +0000"
    msg["Message-ID"] = "<fixture-sample@linkedin.com>"
    msg.set_content(TEXT)
    msg.add_alternative(
        HTML.format(cards="\n".join(CARD.format(**c) for c in CARDS)), subtype="html"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(msg.as_bytes())
    return path


if __name__ == "__main__":
    print(build(Path(__file__).parent / "fixtures" / "sample_alert.eml"))
