"""Task CRUD, and the ownership rules that matter most."""


def create_task(client, headers, **overrides):
    payload = {"title": "write the runbook", "due_date": "2027-03-01"}
    payload.update(overrides)
    response = client.post("/tasks", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------------------- CRUD --


def test_create_returns_the_task(client, alice):
    task = create_task(client, alice["headers"], title="ship it")
    assert task["title"] == "ship it"
    assert task["is_done"] is False
    assert "id" in task


def test_create_does_not_leak_user_id(client, alice):
    """user_id is an internal detail; the response must not carry it."""
    task = create_task(client, alice["headers"])
    assert "user_id" not in task


def test_client_cannot_choose_the_owner(client, alice, bob):
    """A user_id in the body must be ignored, not honoured."""
    response = client.post(
        "/tasks",
        json={"title": "forged", "user_id": 9999},
        headers=alice["headers"],
    )
    assert response.status_code == 201
    # It landed in Alice's list, not user 9999's.
    listed = client.get("/tasks", headers=alice["headers"]).json()
    assert [t["title"] for t in listed] == ["forged"]


def test_read_own_task(client, alice):
    task = create_task(client, alice["headers"])
    response = client.get(f"/tasks/{task['id']}", headers=alice["headers"])
    assert response.status_code == 200
    assert response.json()["id"] == task["id"]


def test_list_returns_newest_first(client, alice):
    create_task(client, alice["headers"], title="first")
    create_task(client, alice["headers"], title="second")
    titles = [t["title"] for t in client.get("/tasks", headers=alice["headers"]).json()]
    assert titles == ["second", "first"]


def test_delete_then_gone(client, alice):
    task = create_task(client, alice["headers"])
    assert (
        client.delete(f"/tasks/{task['id']}", headers=alice["headers"]).status_code
        == 204
    )
    assert (
        client.get(f"/tasks/{task['id']}", headers=alice["headers"]).status_code == 404
    )


# ------------------------------------------------------------------ PATCH --


def test_patch_leaves_unmentioned_fields_alone(client, alice):
    """The exclude_unset behaviour: a partial PATCH must not wipe anything."""
    task = create_task(client, alice["headers"], description="keep me")
    response = client.patch(
        f"/tasks/{task['id']}", json={"is_done": True}, headers=alice["headers"]
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_done"] is True
    assert body["description"] == "keep me"
    assert body["due_date"] == "2027-03-01"


def test_patch_explicit_null_clears_the_field(client, alice):
    """Sending null must differ from omitting the key."""
    task = create_task(client, alice["headers"])
    response = client.patch(
        f"/tasks/{task['id']}", json={"due_date": None}, headers=alice["headers"]
    )
    assert response.status_code == 200
    assert response.json()["due_date"] is None


def test_patch_bumps_updated_at(client, alice):
    task = create_task(client, alice["headers"])
    updated = client.patch(
        f"/tasks/{task['id']}", json={"is_done": True}, headers=alice["headers"]
    ).json()
    assert updated["updated_at"] > task["updated_at"]
    assert updated["created_at"] == task["created_at"]


def test_empty_patch_is_rejected(client, alice):
    task = create_task(client, alice["headers"])
    response = client.patch(f"/tasks/{task['id']}", json={}, headers=alice["headers"])
    assert response.status_code == 400


# ------------------------------------------------------------- validation --


def test_blank_title_rejected(client, alice):
    response = client.post("/tasks", json={"title": ""}, headers=alice["headers"])
    assert response.status_code == 422


def test_overlong_title_rejected(client, alice):
    response = client.post(
        "/tasks", json={"title": "x" * 201}, headers=alice["headers"]
    )
    assert response.status_code == 422


def test_bad_date_rejected(client, alice):
    response = client.post(
        "/tasks",
        json={"title": "ok", "due_date": "not-a-date"},
        headers=alice["headers"],
    )
    assert response.status_code == 422


def test_limit_is_capped(client, alice):
    assert client.get("/tasks?limit=999", headers=alice["headers"]).status_code == 422


# ---------------------------------------------------------------- filters --


def test_filter_by_is_done(client, alice):
    done = create_task(client, alice["headers"], title="done one")
    create_task(client, alice["headers"], title="open one")
    client.patch(
        f"/tasks/{done['id']}", json={"is_done": True}, headers=alice["headers"]
    )

    titles = [
        t["title"]
        for t in client.get("/tasks?is_done=true", headers=alice["headers"]).json()
    ]
    assert titles == ["done one"]


def test_filter_by_due_before(client, alice):
    create_task(client, alice["headers"], title="early", due_date="2027-01-01")
    create_task(client, alice["headers"], title="late", due_date="2027-12-01")

    titles = [
        t["title"]
        for t in client.get(
            "/tasks?due_before=2027-06-01", headers=alice["headers"]
        ).json()
    ]
    assert titles == ["early"]


def test_pagination_does_not_repeat_rows(client, alice):
    """Rows created in the same instant must still page deterministically."""
    for i in range(5):
        create_task(client, alice["headers"], title=f"task {i}")

    page1 = client.get("/tasks?limit=2&offset=0", headers=alice["headers"]).json()
    page2 = client.get("/tasks?limit=2&offset=2", headers=alice["headers"]).json()
    ids = [t["id"] for t in page1 + page2]
    assert len(set(ids)) == 4, "pagination returned a duplicate row"


# --------------------------------------------------------------- OWNERSHIP --
# The most important tests in the suite.


def test_other_user_cannot_read(client, alice, bob):
    task = create_task(client, alice["headers"])
    response = client.get(f"/tasks/{task['id']}", headers=bob["headers"])
    assert response.status_code == 404, "must be 404, not 403 -- 403 confirms it exists"


def test_other_user_cannot_update(client, alice, bob):
    task = create_task(client, alice["headers"])
    response = client.patch(
        f"/tasks/{task['id']}", json={"title": "pwned"}, headers=bob["headers"]
    )
    assert response.status_code == 404


def test_other_user_cannot_delete(client, alice, bob):
    task = create_task(client, alice["headers"])
    assert (
        client.delete(f"/tasks/{task['id']}", headers=bob["headers"]).status_code == 404
    )
    # ...and Alice's task is untouched.
    assert (
        client.get(f"/tasks/{task['id']}", headers=alice["headers"]).status_code == 200
    )


def test_list_is_scoped_to_the_caller(client, alice, bob):
    create_task(client, alice["headers"], title="alice only")
    assert client.get("/tasks", headers=bob["headers"]).json() == []


def test_failed_attack_does_not_mutate(client, alice, bob):
    task = create_task(client, alice["headers"], title="original")
    client.patch(
        f"/tasks/{task['id']}", json={"title": "pwned"}, headers=bob["headers"]
    )
    after = client.get(f"/tasks/{task['id']}", headers=alice["headers"]).json()
    assert after["title"] == "original"


# ------------------------------------------------------------------- auth --

#end
def test_every_route_requires_a_token(client, alice):
    task = create_task(client, alice["headers"])
    assert client.get("/tasks").status_code == 401
    assert client.post("/tasks", json={"title": "x"}).status_code == 401
    assert client.get(f"/tasks/{task['id']}").status_code == 401
    assert client.patch(f"/tasks/{task['id']}", json={"title": "x"}).status_code == 401
    assert client.delete(f"/tasks/{task['id']}").status_code == 401

