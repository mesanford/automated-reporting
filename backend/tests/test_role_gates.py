"""Role enforcement on the spend-impacting endpoints.

Members can browse but cannot approve, execute, delete a connection, or
read the audit log. Owners and admins can. Last-owner protections fire
on PATCH / DELETE of the sole owner's membership.
"""
import pytest

from tests.conftest import auth_headers


@pytest.fixture()
def workspace_with_owner_and_member(client, alice, bob):
    """Alice creates a workspace, invites Bob as member. Returns (ws_id)."""
    r = client.post(
        "/api/workspaces", json={"name": "Acme"}, headers=auth_headers(alice)
    )
    ws_id = r.json()["id"]
    H_ALICE = auth_headers(alice, ws_id)

    invite = client.post(
        f"/api/workspaces/{ws_id}/invites",
        json={"email": f"{bob}@x.com", "role": "member"},
        headers=H_ALICE,
    ).json()
    client.post(
        f"/api/workspaces/invites/accept?token={invite['token']}",
        headers=auth_headers(bob),
    )
    return ws_id


@pytest.fixture()
def pending_plan(db, workspace_with_owner_and_member, alice):
    """Seed a pending OptimizationPlan and return (plan_id, ws_id)."""
    from app import models

    ws_id = workspace_with_owner_and_member
    plan = models.OptimizationPlan(
        workspace_id=ws_id,
        user_id=alice,
        connection_id=999,
        platform="google",
        campaign_name="Demo",
        change_type="budget_increase",
        # OptimizationResponse declares these as strings; oblige the
        # response schema by storing string JSON.
        original_value='{"budget":100}',
        proposed_value='{"budget":120}',
        status="pending",
        reasoning="seeded",
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan.id, ws_id


class TestMemberCannotMutate:
    def test_member_cannot_approve(self, client, pending_plan, bob):
        plan_id, ws_id = pending_plan
        r = client.post(
            f"/api/optimizations/{plan_id}/approve",
            headers=auth_headers(bob, ws_id),
        )
        assert r.status_code == 403
        assert "you are member" in r.json()["detail"]

    def test_member_cannot_execute(self, client, pending_plan, bob):
        plan_id, ws_id = pending_plan
        r = client.post(
            f"/api/optimizations/{plan_id}/execute",
            headers=auth_headers(bob, ws_id),
        )
        assert r.status_code == 403

    def test_member_cannot_delete_connection(self, client, workspace_with_owner_and_member, bob):
        r = client.delete(
            "/api/connections/99999",
            headers=auth_headers(bob, workspace_with_owner_and_member),
        )
        # Role check fires before the row lookup.
        assert r.status_code == 403

    def test_member_cannot_read_audit_log(self, client, workspace_with_owner_and_member, bob):
        ws_id = workspace_with_owner_and_member
        r = client.get(
            f"/api/workspaces/{ws_id}/audit-log",
            headers=auth_headers(bob, ws_id),
        )
        assert r.status_code == 403


class TestOwnerCanMutate:
    def test_owner_approves(self, client, pending_plan, alice):
        plan_id, ws_id = pending_plan
        r = client.post(
            f"/api/optimizations/{plan_id}/approve",
            headers=auth_headers(alice, ws_id),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

    def test_promoted_admin_can_approve(self, client, db, pending_plan, alice, bob):
        plan_id, ws_id = pending_plan
        # Owner promotes Bob to admin.
        r = client.patch(
            f"/api/workspaces/{ws_id}/members/{bob}",
            json={"role": "admin"},
            headers=auth_headers(alice, ws_id),
        )
        assert r.status_code == 200

        # Same call that was 403 a moment ago is now 200.
        r = client.post(
            f"/api/optimizations/{plan_id}/approve",
            headers=auth_headers(bob, ws_id),
        )
        assert r.status_code == 200


class TestLastOwnerGuardrails:
    def test_cannot_demote_only_owner(self, client, workspace_with_owner_and_member, alice):
        ws_id = workspace_with_owner_and_member
        r = client.patch(
            f"/api/workspaces/{ws_id}/members/{alice}",
            json={"role": "admin"},
            headers=auth_headers(alice, ws_id),
        )
        assert r.status_code == 400
        assert "owner" in r.json()["detail"].lower()

    def test_cannot_remove_only_owner(self, client, workspace_with_owner_and_member, alice):
        ws_id = workspace_with_owner_and_member
        r = client.delete(
            f"/api/workspaces/{ws_id}/members/{alice}",
            headers=auth_headers(alice, ws_id),
        )
        assert r.status_code == 400
        assert "last owner" in r.json()["detail"].lower()
