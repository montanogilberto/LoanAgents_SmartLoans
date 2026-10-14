"""
Commercial core of the GMO Ecosystem website: visit counter, AI token
allowance per visitor, lead capture (mini registration + contact form) and
lead follow-up for the GMO team.

Interim home: this lives in LoanAgents next to the Business Intake agent the
website already calls (intake_api.py), with a small SQLite database, until
the designed `leadCapture` module (knowledge/designs/prd_leadCapture.json)
is built into the GMO backend. Lead fields use that design's names so the
data can move there unchanged.
"""
