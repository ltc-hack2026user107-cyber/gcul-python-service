from pathlib import Path


def test_escrow_contract_source_exists() -> None:
    contract_path = Path("gculpy/escrow.py")
    assert contract_path.exists(), "Escrow contract source should exist"

    content = contract_path.read_text(encoding="utf-8")
    assert "class Escrow" in content
    assert "def create_order" in content
    assert "def order_delivered" in content
    assert "def order_failed" in content
