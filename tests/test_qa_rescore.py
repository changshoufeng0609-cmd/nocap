"""Run 31 original post-run normalization contract cases on frozen code."""
from eval.qa_reproduce import run_frozen_contract_tests


def test_frozen_post_run_normalization_contract():
    output = run_frozen_contract_tests("rescore")
    assert "31 passed" in output
