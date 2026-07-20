def test_category_returns_actual_notices(client):
    response = client.get("/api/categories/학사/notices")
    assert response.status_code == 200
    body = response.json()
    assert body["category"] == "학사"
    assert len(body["notices"]) == 2
    assert "2건" in body["message"]


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_sensitive_input_is_rejected(client):
    response = client.post("/api/chat", json={"message": "제 학번은 2026123456이고 휴학하고 싶어요"})
    assert response.status_code == 422
    assert "개인정보" in response.json()["detail"]


def test_admin_api_requires_token(client):
    response = client.get("/api/crawler/status")
    assert response.status_code == 403


def test_feedback_accepts_minimal_event(client):
    response = client.post("/api/feedback", json={
        "answerId": "answer-1234", "resolved": False, "reason": "outdated",
        "sourceIds": [1], "responseStatus": "success",
    })
    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}


def test_negative_feedback_appears_in_admin_data_gap_queue(client):
    client.post("/api/feedback", json={
        "answerId": "answer-5678", "resolved": False, "reason": "insufficient",
        "sourceIds": [], "responseStatus": "insufficient_evidence",
    })

    response = client.get("/api/data-gaps", headers={"X-Admin-Token": "local-dev-admin"})

    assert response.status_code == 200
    assert any(item["gapType"] == "user_feedback_insufficient" for item in response.json()["gaps"])


def test_notice_detail_contains_persisted_action_guide(client):
    search = client.get("/api/notices/search", params={"q": "휴학"})
    notice_id = search.json()["notices"][0]["id"]

    response = client.get(f"/api/notices/{notice_id}")

    assert response.status_code == 200
    guide = response.json()["actionGuide"]
    assert guide["taskName"]
    assert [step["order"] for step in guide["steps"]] == [1, 2, 3]
    assert guide["sourceUrl"]


def test_codex_job_is_claimed_and_hallucinated_contact_is_removed(client):
    notice_id = client.get("/api/notices/search", params={"q": "휴학"}).json()["notices"][0]["id"]
    headers = {"X-Admin-Token": "local-dev-admin"}
    queued = client.post(f"/api/notices/{notice_id}/codex-queue", headers=headers)

    assert queued.status_code == 200
    claimed = client.get("/api/codex/jobs/next", headers=headers)
    assert claimed.status_code == 200
    job = claimed.json()
    assert job["notice"]["id"] == notice_id

    completed = client.post(
        f"/api/codex/jobs/{job['jobId']}/complete",
        headers=headers,
        json={
            "category": "학사", "subCategory": "휴학", "actionType": "신청",
            "applicationPeriod": {}, "eventPeriod": {}, "target": {},
            "department": {
                "name": "학사지원팀", "contactPerson": "없는담당자",
                "phone": "010-9999-9999", "email": "fake@kangnam.ac.kr",
            },
            "keywords": ["휴학", "신청"], "noticeStatus": "unknown",
            "confidence": 0.95,
        },
    )

    assert completed.status_code == 200
    detail = client.get(f"/api/notices/{notice_id}").json()
    assert detail["metadata"]["department"]["contactPerson"] is None
    assert detail["metadata"]["department"]["phone"] is None
    assert detail["metadata"]["department"]["email"] is None
