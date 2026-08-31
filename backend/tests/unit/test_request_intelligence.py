from app.core.request_intelligence import RequestIntent, classify_user_request


def test_classifies_post_gate_source_correction_as_confirmation_required():
    impact = classify_user_request(
        "Wait, I forgot Redis in the source architecture. Add Redis cache.",
        after_gate_1=True,
    )

    assert impact.intent == RequestIntent.SOURCE_CORRECTION
    assert impact.requires_confirmation is True
    assert impact.should_mutate_source is False
    assert {"effort", "cost", "sequencing", "validation", "rollback"} <= set(impact.impact_dimensions)


def test_classifies_target_context_as_target_planning_not_source_mutation():
    impact = classify_user_request(
        "Source is AWS cloud. Target is modernized AWS. Host React on S3 and CloudFront. "
        "Run backend workloads on ECS Fargate. Downtime tolerance is a 4-hour maintenance window.",
        after_gate_1=True,
    )

    assert impact.intent == RequestIntent.TARGET_PLANNING
    assert impact.requires_confirmation is False
    assert impact.should_mutate_source is False


def test_classifies_high_impact_replatform():
    impact = classify_user_request("Change the FastAPI backend to Java Spring Boot.")

    assert impact.intent == RequestIntent.HIGH_IMPACT_REPLATFORM
    assert impact.requires_confirmation is True
    assert "team_skills" in impact.impact_dimensions


def test_classifies_unscoped_payment_capability():
    impact = classify_user_request("Add Stripe payment gateway also.")

    assert impact.intent == RequestIntent.UNSCOPED_CAPABILITY
    assert impact.requires_confirmation is True
    assert "scope" in impact.impact_dimensions


def test_classifies_review_explanation_without_mutation():
    impact = classify_user_request(
        "Explain why ECS Fargate was chosen instead of EKS. Include effort, cost, risk, validation, and rollback.",
        after_gate_1=True,
        review_stage=True,
    )

    assert impact.intent == RequestIntent.REVIEW_EXPLANATION
    assert impact.requires_confirmation is False
    assert impact.should_mutate_source is False
