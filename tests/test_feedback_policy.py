from app.randomization import _group_type_from_strategy, _strategy_from_user
from app.routes.parsons import (
    RESULT_ONLY_FEEDBACK,
    _analysis_group_type,
    _feedback_policy_for_strategy,
    _feedback_strategy_from_user,
)


def test_three_arm_group_mapping_is_canonical():
    assert _group_type_from_strategy("A") == "control"
    assert _group_type_from_strategy("B") == "experimental_1"
    assert _group_type_from_strategy("C") == "experimental_2"
    assert _analysis_group_type("A") == "control"
    assert _analysis_group_type("B") == "experimental_1"
    assert _analysis_group_type("C") == "experimental_2"


def test_feedback_policies_match_the_three_study_conditions():
    assert _feedback_policy_for_strategy("A") == "result_only"
    assert _feedback_policy_for_strategy("B") == "structured_ai_once"
    assert _feedback_policy_for_strategy("C") == "structured_only"
    assert RESULT_ONLY_FEEDBACK == "作答尚未正確，請重新調整後再次送出。"


def test_legacy_two_arm_users_keep_their_strategy_until_migrated():
    assert _strategy_from_user({"feedback_strategy": "B", "group_type": "experimental"}) == "B"
    assert _strategy_from_user({"feedback_strategy": "C", "group_type": "control"}) == "C"
    assert _feedback_strategy_from_user({"feedback_strategy": "C", "group_type": "control"}) == "C"
