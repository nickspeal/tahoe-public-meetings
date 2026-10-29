"""ntfy alerts for silent run problems."""
import pytest

from tahoe_ingest import alerts


def summary(status="degraded", **detail):
    return {"steps": [{"name": "classify-topics", "status": status, "detail": detail}]}


def test_clean_and_skipped_runs_raise_nothing():
    assert alerts.run_problems(summary("ok", failedMeetings=[], costUsd=0.02)) == []
    assert alerts.run_problems(summary("skipped", reason="no credentials")) == []
    assert alerts.run_problems({"steps": []}) == []


def test_failed_meetings_and_cost_limits_are_reported():
    problems = alerts.run_problems(summary(failedMeetings=["a", "b"], stoppedEarly="cost limit reached", costUsd=0.5,
                                           monthToDateUsd=4.9))
    assert len(problems) == 2
    assert "2 meeting(s): a, b" in problems[1] or "2 meeting(s): a, b" in problems[0]
    assert any("cost limit" in p for p in problems)
    assert alerts.run_problems(summary(stopped="Month-to-date AI spend reached budget"))[0].startswith("Claude classification stopped")


def test_a_time_budget_stop_is_reported_once_with_its_steps_and_duration():
    run = summary(stoppedEarly="time budget reached; remaining meetings keep rule tags and are classified on the next run",
                  timeBudgetReached=True, meetingsDeferred=3, costUsd=0.1)
    run.update(timeBudgetSeconds=2700, timeBudgetReached=True, timeBudgetSteps=["classify-topics", "summarize-items"],
               durationSeconds=2950.4)
    problems = alerts.run_problems(run)
    assert problems == ["The run reached its time budget (45 min) and finished after 49 min: "
                        "classify-topics, summarize-items stopped early and left the rest for the next run"]
    sent = []
    assert alerts.alert_on_summary(run, send=lambda *a, **k: sent.append(a)) == problems
    assert sent == [("Tahoe ingestion needs attention", problems[0])]
    # A run inside its budget raises nothing for it.
    assert alerts.run_problems({"steps": [], "timeBudgetSeconds": 2700, "timeBudgetReached": False, "timeBudgetSteps": []}) == []


def test_send_requires_https_and_skips_when_unset():
    assert alerts.send_ntfy("t", "m", env={}) is False
    with pytest.raises(ValueError):
        alerts.send_ntfy("t", "m", env={"NTFY_URL": "http://ntfy.sh/x"})


def test_send_posts_with_token():
    seen = {}

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def opener(request, timeout):
        seen["url"], seen["auth"], seen["body"] = request.full_url, request.get_header("Authorization"), request.data
        return Response()

    assert alerts.send_ntfy("t", "hello", env={"NTFY_URL": "https://ntfy.sh/x", "NTFY_TOKEN": "tk"}, opener=opener)
    assert seen == {"url": "https://ntfy.sh/x", "auth": "Bearer tk", "body": b"hello"}


def test_alert_on_summary_swallows_delivery_errors(capsys):
    def boom(*a, **k): raise RuntimeError("ntfy returned HTTP 500")
    assert alerts.alert_on_summary(summary(failedMeetings=["a"]), send=boom)
    assert "ntfy alert failed" in capsys.readouterr().out
    sent = []
    assert alerts.alert_on_summary(summary("ok"), send=lambda *a, **k: sent.append(a)) == [] and not sent
