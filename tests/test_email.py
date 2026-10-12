'''Tests for the weekly email (no real email is sent).'''
from datetime import date

from collector import email_digest

TODAY = date(2026, 10, 19)


def job(**kw):
    base = {"org": "Zearn", "title": "Data Analyst", "tier": 1, "category": "Education", "url": "https://x/1",
            "location": "New York, NY", "remote": None, "new_this_week": True, "posted": "2026-10-15",
            "salary_min": 85000, "salary_max": 95000, "years_required": 2, "phd_required": False,
            "description": "Analyze student data."}
    base.update(kw)
    return base


def test_select_applies_filters(monkeypatch):
    monkeypatch.setenv("MIN_SALARY", "80000")
    monkeypatch.setenv("EXCLUDE_TERMS", "charter school, hedge fund")
    feed = {"jobs": [
        job(),
        job(title="Old", new_this_week=False),
        job(title="Low pay", salary_min=50000, salary_max=60000),
        job(title="Unknown pay", salary_min=None, salary_max=None),
        job(title="PhD", phd_required=True),
        job(title="Senior", years_required=7),
        job(title="Charter", description="Join our charter school network"),
    ]}
    assert [j["title"] for j in email_digest.select(feed, TODAY)] == ["Data Analyst", "Unknown pay"]


def test_first_run_uses_posted_date(monkeypatch):
    monkeypatch.delenv("MIN_SALARY", raising=False)
    monkeypatch.delenv("EXCLUDE_TERMS", raising=False)
    feed = {"first_run": True, "jobs": [job(posted="2026-10-10"), job(title="Stale", posted="2026-08-01"),
                                        job(title="Undated", posted=None)]}
    assert [j["title"] for j in email_digest.select(feed, TODAY)] == ["Data Analyst", "Undated"]


def test_build_groups_and_flags_broken_sources():
    jobs = [job(), job(org="Dept Of Education", tier=2, category="NYC Government", exam_required=True,
                       post_until="2026-10-30", salary_min=None, salary_max=None)]
    feed = {"sources_ok": 60, "sources_total": 71}
    health = {"sources": [{"org": "MDRC", "tier": 1, "status": "no supported platform found"},
                          {"org": "Zearn", "tier": 1, "status": "ok"}]}
    subject, text, html_body = email_digest.build(feed, jobs, health, TODAY)
    assert subject == "Job feed — Oct 19: 2 new postings"
    assert "TIER 1 ORGANIZATIONS" in text and "NYC GOVERNMENT" in text
    assert "$85,000–$95,000" in text and "pay not listed" in text
    assert "civil service exam" in text and "closes 2026-10-30" in text
    assert "Not reading: MDRC." in text
    assert "<a href='https://x/1'>Data Analyst</a>" in html_body


def test_build_empty_week():
    subject, text, _ = email_digest.build({"sources_ok": 1, "sources_total": 1}, [], {"sources": []}, TODAY)
    assert "0 new postings" in subject and "Nothing new this week." in text


def test_main_skips_without_secrets(monkeypatch, capsys):
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    assert email_digest.main() == 0
    assert "Email skipped" in capsys.readouterr().out


def test_send_uses_gmail_smtp(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["host"] = (host, port)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def login(self, user, pw):
            sent["login"] = (user, pw)
        def sendmail(self, frm, to, msg):
            sent["to"] = to
            sent["msg"] = msg

    monkeypatch.setattr(email_digest.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setenv("GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-pass")
    monkeypatch.delenv("DIGEST_TO", raising=False)
    email_digest.send("Subject", "text", "<p>html</p>")
    assert sent["host"] == ("smtp.gmail.com", 465)
    assert sent["login"] == ("me@example.com", "app-pass")
    assert sent["to"] == ["me@example.com"]
    assert "Subject: Subject" in sent["msg"]
