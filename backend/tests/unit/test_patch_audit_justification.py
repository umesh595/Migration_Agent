from app.api.routers.sessions import _patch_justification


def test_patch_justification_explains_applied_dependency():
    justification = _patch_justification(
        {
            "op": "add_dependency",
            "source_id": "frontend",
            "target_id": "backend",
            "kind": "sync_call",
        },
        "applied",
        None,
    )

    assert "frontend" in justification
    assert "backend" in justification
    assert "sync_call" in justification


def test_patch_justification_uses_rejection_reason():
    justification = _patch_justification(
        {"op": "add_dependency"},
        "rejected",
        "dependency already exists",
    )

    assert justification == "dependency already exists"
