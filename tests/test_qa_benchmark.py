"""Run 28 original benchmark contract and accounting cases on frozen code."""
from eval.qa_reproduce import run_frozen_contract_tests


def test_frozen_benchmark_contract_and_accounting():
    output = run_frozen_contract_tests("benchmark")
    assert "28 passed" in output
