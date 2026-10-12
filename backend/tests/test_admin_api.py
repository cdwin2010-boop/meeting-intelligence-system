"""시스템 관리자 관리 API와 감사 이력(작업 82-1 파트 B)."""
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Account, AccountDepartment, Department, Event
from app.services import org
from app.services.org import OrgError
from tests.test_system_admin import designate_now
from tests.test_upload_processing import add_account, auth, env  # noqa: F401  (env 는 픽스처)

GOOD_PASSWORD = "Valid-Pass-123"
KNOWN_PASSWORD = "unused-password"  # add_account 가 쓰는 테스트용 비밀번호(14자)


def call(env, method, account, path, **kw):
    return env["client"].request(method, path, headers=auth(account), **kw)


@pytest.fixture
def world(env):
    f = env["factory"]
    sysadmin = add_account(f, "고객사A", "sys", "staff", name="시스템관리")
    designate_now(env, sysadmin)
    return {
        "sys": sysadmin,
        "staff": add_account(f, "고객사A", "staff1", "staff", name="김담당"),
        "mgr": add_account(f, "고객사A", "mgr1", "manager", name="박관리"),
        "mgr2": add_account(f, "고객사A", "mgr2", "manager", name="이관리"),
        "boss": add_account(f, "고객사A", "boss", "executive", name="최지시"),
        "alien": add_account(f, "고객사B", "alien", "staff", name="타사직원"),
    }


def audit_texts(env) -> str:
    with env["factory"]() as s:
        return " ".join(str(e.payload) for e in s.scalars(select(Event)).all())


# ---------------- 권한 ----------------
@pytest.mark.parametrize("method, path", [
    ("GET", "/api/admin/accounts"), ("POST", "/api/admin/accounts"), ("PATCH", "/api/admin/accounts/1"),
    ("POST", "/api/admin/accounts/1/password-reset"), ("PUT", "/api/admin/accounts/1/system-admin"),
    ("GET", "/api/admin/departments"), ("POST", "/api/admin/departments"), ("PATCH", "/api/admin/departments/1"),
    ("PUT", "/api/admin/departments/1/head"), ("POST", "/api/admin/departments/1/members"),
    ("POST", "/api/admin/departments/1/members/1/remove"), ("GET", "/api/admin/audit"),
])
def test_only_system_admin_can_use_admin_api(env, world, method, path):
    assert env["client"].request(method, path).status_code == 401
    for key in ("staff", "mgr", "boss"):
        assert call(env, method, world[key], path, json={}).status_code == 403, (key, path)


# ---------------- 계정 ----------------
def test_list_search_paging_and_fields(env, world):
    body = call(env, "GET", world["sys"], "/api/admin/accounts?size=3").json()
    assert body["total"] == 5 and len(body["items"]) == 3 and body["size"] == 3  # 타 고객사 계정은 제외
    row = next(i for i in call(env, "GET", world["sys"], "/api/admin/accounts").json()["items"] if i["loginId"] == "sys")
    assert set(row) == {"id", "loginId", "name", "email", "rank", "isActive", "isSystemAdmin", "departments", "createdAt"}
    assert row["isSystemAdmin"] is True and row["rank"] == "staff"
    assert "password" not in str(row).lower()
    found = call(env, "GET", world["sys"], "/api/admin/accounts?q=mgr").json()
    assert {i["loginId"] for i in found["items"]} == {"mgr1", "mgr2"} and found["total"] == 2
    assert call(env, "GET", world["sys"], "/api/admin/accounts?q=타사").json()["total"] == 0


def test_create_account_and_login(env, world):
    body = {"loginId": "newbie", "name": "신입", "email": "n@example.com", "rank": "staff", "password": GOOD_PASSWORD}
    res = call(env, "POST", world["sys"], "/api/admin/accounts", json=body)
    assert res.status_code == 201
    out = res.json()
    assert out["loginId"] == "newbie" and out["rank"] == "staff" and out["isActive"] is True and out["isSystemAdmin"] is False
    assert GOOD_PASSWORD not in res.text and "passwordHash" not in res.text
    login = env["client"].post("/api/auth/login", json={"loginId": "newbie", "password": GOOD_PASSWORD})
    assert login.status_code == 200 and login.json()["isSystemAdmin"] is False
    with env["factory"]() as s:
        created = s.scalar(select(Account).where(Account.login_id == "newbie"))
        assert created.tenant_id == world["sys"].tenant_id and GOOD_PASSWORD not in created.password_hash
    assert GOOD_PASSWORD not in audit_texts(env)


@pytest.mark.parametrize("patch, status", [
    ({"loginId": "sys"}, 409),  # 중복 로그인 ID
    ({"loginId": ""}, 422), ({"name": "  "}, 422), ({"rank": "boss"}, 422),
    ({"password": "short"}, 422), ({"password": "x" * 200}, 422), ({"loginId": "l" * 101}, 422),
])
def test_create_account_validation(env, world, patch, status):
    body = {"loginId": "fresh", "name": "이름", "email": "", "rank": "staff", "password": GOOD_PASSWORD, **patch}
    assert call(env, "POST", world["sys"], "/api/admin/accounts", json=body).status_code == status


def test_password_policy_follows_setting(env, world, monkeypatch):
    monkeypatch.setattr(settings, "password_min_length", 14)
    body = {"loginId": "p1", "name": "n", "rank": "staff", "password": "a" * 13}
    assert call(env, "POST", world["sys"], "/api/admin/accounts", json=body).status_code == 422
    body["password"] = "a" * 14
    assert call(env, "POST", world["sys"], "/api/admin/accounts", json=body).status_code == 201


def test_patch_account_fields_and_history(env, world):
    path = f"/api/admin/accounts/{world['staff'].id}"
    res = call(env, "PATCH", world["sys"], path, json={"name": "김개명", "email": "k@example.com", "rank": "manager"})
    assert res.status_code == 200 and res.json()["name"] == "김개명" and res.json()["rank"] == "manager"
    assert call(env, "PATCH", world["sys"], path, json={"isActive": False}).json()["isActive"] is False
    # 비활성 계정은 로그인 거부(기존 동작)
    assert env["client"].post("/api/auth/login", json={"loginId": "staff1", "password": KNOWN_PASSWORD}).status_code == 401
    audit = call(env, "GET", world["sys"], "/api/admin/audit").json()["items"]
    updates = [e for e in audit if e["eventType"] == "admin.account_updated" and e["targetId"] == world["staff"].id]
    assert len(updates) == 2 and updates[0]["before"] == {"isActive": True} and updates[0]["after"] == {"isActive": False}
    assert updates[1]["before"]["rank"] == "staff" and updates[1]["after"]["rank"] == "manager"
    assert updates[0]["actor"]["id"] == world["sys"].id


def test_patch_rules_self_headship_and_system_admin_rank(env, world):
    own = f"/api/admin/accounts/{world['sys'].id}"
    assert call(env, "PATCH", world["sys"], own, json={"isActive": False}).status_code == 409
    assert call(env, "PATCH", world["sys"], own, json={"rank": "manager"}).status_code == 409
    other = f"/api/admin/accounts/{world['mgr'].id}"
    # 부서장이면 관리자 미만으로 내릴 수 없다
    dept = call(env, "POST", world["sys"], "/api/admin/departments", json={"name": "영업"}).json()
    assert call(env, "PUT", world["sys"], f"/api/admin/departments/{dept['id']}/head", json={"accountId": world["mgr"].id}).status_code == 200
    res = call(env, "PATCH", world["sys"], other, json={"rank": "staff"})
    assert res.status_code == 409 and "부서장" in res.json()["detail"]
    assert call(env, "PATCH", world["sys"], other, json={"rank": "executive"}).status_code == 200  # 위로는 가능
    # 시스템 관리자 계정은 staff 고정
    flag = call(env, "PUT", world["sys"], f"/api/admin/accounts/{world['staff'].id}/system-admin", json={"enabled": True})
    assert flag.status_code == 200 and flag.json()["isSystemAdmin"] is True
    res = call(env, "PATCH", world["sys"], f"/api/admin/accounts/{world['staff'].id}", json={"rank": "manager"})
    assert res.status_code == 409 and "staff" in res.json()["detail"]
    # 없는·타 고객사 계정
    assert call(env, "PATCH", world["sys"], "/api/admin/accounts/99999", json={"name": "x"}).status_code == 404
    assert call(env, "PATCH", world["sys"], f"/api/admin/accounts/{world['alien'].id}", json={"name": "x"}).status_code == 404


def test_system_admin_designation_rules(env, world):
    sys_id = world["sys"].id
    to = lambda key, enabled: call(  # noqa: E731
        env, "PUT", world["sys"], f"/api/admin/accounts/{world[key].id}/system-admin", json={"enabled": enabled})
    assert to("mgr", True).status_code == 409  # 상위 권한은 지정 불가
    assert to("alien", True).status_code == 404
    assert to("staff", True).status_code == 200 and to("staff", True).status_code == 200  # 멱등
    # 자기 자신 해제 금지, 마지막이 아니어도 본인은 불가
    res = call(env, "PUT", world["sys"], f"/api/admin/accounts/{sys_id}/system-admin", json={"enabled": False})
    assert res.status_code == 409
    assert to("staff", False).status_code == 200
    # 마지막 시스템 관리자(본인 하나) 해제는 위 규칙으로 거부
    audit = call(env, "GET", world["sys"], "/api/admin/audit").json()["items"]
    kinds = [e["eventType"] for e in audit]
    assert "admin.system_admin_set" in kinds and "admin.system_admin_unset" in kinds


def test_password_reset(env, world):
    path = f"/api/admin/accounts/{world['staff'].id}/password-reset"
    assert call(env, "POST", world["sys"], path, json={"newPassword": "short"}).status_code == 422
    res = call(env, "POST", world["sys"], path, json={"newPassword": GOOD_PASSWORD})
    assert res.status_code == 204 and res.text == ""
    assert env["client"].post("/api/auth/login", json={"loginId": "staff1", "password": GOOD_PASSWORD}).status_code == 200
    assert env["client"].post("/api/auth/login", json={"loginId": "staff1", "password": KNOWN_PASSWORD}).status_code == 401
    assert call(env, "POST", world["sys"], "/api/admin/accounts/99999/password-reset", json={"newPassword": GOOD_PASSWORD}).status_code == 404
    entries = [e for e in call(env, "GET", world["sys"], "/api/admin/audit").json()["items"] if e["eventType"] == "admin.password_reset"]
    assert len(entries) == 1 and entries[0]["before"] == {} and entries[0]["after"] == {}
    assert GOOD_PASSWORD not in audit_texts(env)


def test_self_password_change(env, world):
    client = env["client"]
    path = "/api/auth/change-password"
    assert client.post(path, json={"currentPassword": KNOWN_PASSWORD, "newPassword": GOOD_PASSWORD}).status_code == 401
    wrong = call(env, "POST", world["staff"], path, json={"currentPassword": "nope-nope-nope", "newPassword": GOOD_PASSWORD})
    assert wrong.status_code == 400 and wrong.json()["detail"] == "비밀번호를 변경하지 못했습니다."
    assert call(env, "POST", world["staff"], path, json={"currentPassword": KNOWN_PASSWORD, "newPassword": "short"}).status_code == 422
    ok = call(env, "POST", world["staff"], path, json={"currentPassword": KNOWN_PASSWORD, "newPassword": GOOD_PASSWORD})
    assert ok.status_code == 204
    assert client.post("/api/auth/login", json={"loginId": "staff1", "password": GOOD_PASSWORD}).status_code == 200
    assert client.post("/api/auth/login", json={"loginId": "staff1", "password": KNOWN_PASSWORD}).status_code == 401
    text = audit_texts(env)
    assert GOOD_PASSWORD not in text and KNOWN_PASSWORD not in text
    with env["factory"]() as s:
        assert s.scalars(select(Event).where(Event.event_type == "account.password_changed")).one().actor_account_id == world["staff"].id


# ---------------- 부서·소속 ----------------
def test_department_tree_create_patch_and_cycle(env, world):
    sysadmin = world["sys"]
    top = call(env, "POST", sysadmin, "/api/admin/departments", json={"name": "본부"})
    assert top.status_code == 201 and top.json()["kind"] == "normal" and top.json()["parentId"] is None
    child = call(env, "POST", sysadmin, "/api/admin/departments", json={"name": "1팀", "parentId": top.json()["id"]}).json()
    grand = call(env, "POST", sysadmin, "/api/admin/departments", json={"name": "1파트", "parentId": child["id"]}).json()
    group = call(env, "POST", sysadmin, "/api/admin/departments", json={"name": "임원", "isExecutiveGroup": True}).json()
    assert group["kind"] == "executive"
    assert call(env, "POST", sysadmin, "/api/admin/departments", json={"name": "본부"}).status_code == 409  # 이름 중복
    assert call(env, "POST", sysadmin, "/api/admin/departments", json={"name": " "}).status_code == 422
    assert call(env, "POST", sysadmin, "/api/admin/departments", json={"name": "x", "parentId": 9999}).status_code == 422
    tree = call(env, "GET", sysadmin, "/api/admin/departments").json()
    assert {d["name"] for d in tree} == {"본부", "1팀", "1파트", "임원"}
    assert next(d for d in tree if d["id"] == grand["id"])["parentId"] == child["id"]
    # 수정: 이름·상위 변경, 순환 금지
    renamed = call(env, "PATCH", sysadmin, f"/api/admin/departments/{child['id']}", json={"name": "개발1팀"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "개발1팀" and renamed.json()["parentId"] == top.json()["id"]
    assert call(env, "PATCH", sysadmin, f"/api/admin/departments/{top.json()['id']}", json={"parentId": grand["id"]}).status_code == 409
    assert call(env, "PATCH", sysadmin, f"/api/admin/departments/{child['id']}", json={"parentId": child["id"]}).status_code == 409
    assert call(env, "PATCH", sysadmin, f"/api/admin/departments/{grand['id']}", json={"parentId": None}).json()["parentId"] is None
    assert call(env, "PATCH", sysadmin, f"/api/admin/departments/{child['id']}", json={"name": "본부"}).status_code == 409
    assert call(env, "PATCH", sysadmin, "/api/admin/departments/9999", json={"name": "x"}).status_code == 404
    kinds = {e["eventType"] for e in call(env, "GET", sysadmin, "/api/admin/audit?size=100").json()["items"]}
    assert "department.updated" in kinds


def test_head_and_member_rules(env, world):
    sysadmin = world["sys"]
    dept = call(env, "POST", sysadmin, "/api/admin/departments", json={"name": "영업"}).json()
    base = f"/api/admin/departments/{dept['id']}"
    assert call(env, "POST", sysadmin, f"{base}/members", json={"accountId": world["staff"].id}).status_code == 200
    # 부서장은 관리자 이상
    assert call(env, "PUT", sysadmin, f"{base}/head", json={"accountId": world["staff"].id}).status_code == 409
    head = call(env, "PUT", sysadmin, f"{base}/head", json={"accountId": world["mgr"].id})
    assert head.status_code == 200 and head.json()["head"]["accountId"] == world["mgr"].id
    # 부서장은 소속 제거 불가, 새 부서장을 지정하면 기존 부서장은 부서원으로
    assert call(env, "POST", sysadmin, f"{base}/members/{world['mgr'].id}/remove").status_code == 409
    swapped = call(env, "PUT", sysadmin, f"{base}/head", json={"accountId": world["mgr2"].id}).json()
    assert swapped["head"]["accountId"] == world["mgr2"].id
    assert {m["accountId"]: m["role"] for m in swapped["members"]}[world["mgr"].id] == "member"
    removed = call(env, "POST", sysadmin, f"{base}/members/{world['staff'].id}/remove")
    assert removed.status_code == 200 and world["staff"].id not in {m["accountId"] for m in removed.json()["members"]}
    assert call(env, "POST", sysadmin, f"{base}/members/{world['staff'].id}/remove").status_code == 409  # 소속 아님
    # 겸직 가능
    other = call(env, "POST", sysadmin, "/api/admin/departments", json={"name": "개발"}).json()
    assert call(env, "POST", sysadmin, f"/api/admin/departments/{other['id']}/members", json={"accountId": world["mgr"].id}).status_code == 200
    row = next(i for i in call(env, "GET", sysadmin, "/api/admin/accounts?q=mgr1").json()["items"])
    assert {d["name"] for d in row["departments"]} == {"영업", "개발"}
    # 타 고객사 계정·부서
    assert call(env, "POST", sysadmin, f"{base}/members", json={"accountId": world["alien"].id}).status_code == 404
    assert call(env, "POST", sysadmin, "/api/admin/departments/9999/members", json={"accountId": world["mgr"].id}).status_code == 404
    # 비활성 계정은 소속 불가(기존 규칙)
    call(env, "PATCH", sysadmin, f"/api/admin/accounts/{world['boss'].id}", json={"isActive": False})
    assert call(env, "POST", sysadmin, f"{base}/members", json={"accountId": world["boss"].id}).status_code == 409


def test_api_uses_the_same_org_service_rules_as_manage_org(env, world):
    """부서 규칙은 services/org.py 한 곳이다: API 와 scripts/manage_org.py 가 같은 서비스 함수를 쓰고 같은 거부 결과를 낸다."""
    source = (Path(__file__).resolve().parent.parent / "scripts" / "manage_org.py").read_text(encoding="utf-8")
    assert "from app.services import org" in source and "org.set_head" in source
    api_source = (Path(__file__).resolve().parent.parent / "app" / "api" / "admin.py").read_text(encoding="utf-8")
    assert "app.services.org" in api_source and "RANK_ORDER" not in api_source
    dept = call(env, "POST", world["sys"], "/api/admin/departments", json={"name": "품질"}).json()
    api_rejected = call(env, "PUT", world["sys"], f"/api/admin/departments/{dept['id']}/head", json={"accountId": world["staff"].id})
    assert api_rejected.status_code == 409
    with env["factory"]() as s:
        department = s.get(Department, dept["id"])
        with pytest.raises(OrgError) as info:
            org.set_head(s, department, s.get(Account, world["staff"].id))
    assert str(info.value) == api_rejected.json()["detail"]


# ---------------- 감사 이력 ----------------
def test_audit_lists_admin_changes_with_paging_and_no_secrets(env, world):
    sysadmin = world["sys"]
    call(env, "POST", sysadmin, "/api/admin/accounts", json={"loginId": "a1", "name": "A", "rank": "staff", "password": GOOD_PASSWORD})
    call(env, "POST", sysadmin, "/api/admin/departments", json={"name": "신설"})
    call(env, "POST", sysadmin, f"/api/admin/accounts/{world['staff'].id}/password-reset", json={"newPassword": GOOD_PASSWORD})
    first = call(env, "GET", sysadmin, "/api/admin/audit?size=2").json()
    assert first["total"] >= 4 and len(first["items"]) == 2 and first["page"] == 1
    ids = [e["id"] for e in first["items"]]
    assert ids == sorted(ids, reverse=True)
    second = call(env, "GET", sysadmin, "/api/admin/audit?size=2&page=2").json()
    assert not set(ids) & {e["id"] for e in second["items"]}
    everything = call(env, "GET", sysadmin, "/api/admin/audit?size=100").json()["items"]
    assert {e["eventType"] for e in everything} >= {
        "admin.account_created", "admin.password_reset", "department.updated", "admin.system_admin_set",
    }
    created = next(e for e in everything if e["eventType"] == "admin.account_created")
    assert created["label"] == "계정 생성" and created["after"]["loginId"] == "a1" and created["actor"]["name"] == "시스템관리"
    text = str(everything)
    assert GOOD_PASSWORD not in text and "hash" not in text.lower() and "argon2" not in text.lower()
    # 회의록 내용 이력(다른 종류 사건)은 섞이지 않는다
    assert all(e["targetType"] in {"account", "department", "system_setting"} for e in everything)
