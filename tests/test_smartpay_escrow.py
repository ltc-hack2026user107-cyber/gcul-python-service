import unittest
import sys
from types import ModuleType

if "gcul" not in sys.modules:
    gcul_mock = ModuleType("gcul")
    class Contract:
        pass
    gcul_mock.Contract = Contract  # type: ignore
    sys.modules["gcul"] = gcul_mock

from gculpy.smartpay_escrow import SmartPayEscrow


class SmartPayEscrowTests(unittest.TestCase):
    def test_create_fund_ship_release_flow(self) -> None:
        contract = SmartPayEscrow()
        contract.initialize("1:USR:ESCROW", "1:USR:ADMIN")

        contract.create_order(
            order_id="ORD-100",
            buyer_account="1:USR:B1",
            seller_account="1:USR:S1",
            amount=5000,
            caller="1:USR:ADMIN",
        )

        contract.fund_escrow("ORD-100", caller="1:USR:B1")
        contract.mark_shipped("ORD-100", tracking_ref="TRK-100", caller="1:USR:S1")
        contract.release_to_seller("ORD-100", caller="1:USR:ADMIN")

        order = contract.get_order("ORD-100")
        self.assertEqual(order["status"], contract.STATUS_RELEASED)
        self.assertEqual(contract.get_order_status("ORD-100"), contract.STATUS_RELEASED)

    def test_refund_flow(self) -> None:
        contract = SmartPayEscrow()
        contract.initialize("1:USR:ESCROW", "1:USR:ADMIN")

        contract.create_order(
            order_id="ORD-101",
            buyer_account="1:USR:B2",
            seller_account="1:USR:S2",
            amount=3000,
            caller="1:USR:ADMIN",
        )

        contract.fund_escrow("ORD-101", caller="1:USR:B2")
        contract.refund_to_buyer("ORD-101", reason="NON_DELIVERY", caller="1:USR:ADMIN")

        order = contract.get_order("ORD-101")
        self.assertEqual(order["status"], contract.STATUS_REFUNDED)

    def test_transfer_hook_uses_runtime_transfer(self) -> None:
        contract = SmartPayEscrow()
        contract.initialize("1:USR:ESCROW", "1:USR:ADMIN")
        calls = {}

        def fake_transfer(*, from_account: str, to_account: str, amount: int, currency: str) -> None:
            calls["args"] = (from_account, to_account, amount, currency)

        contract.transfer = fake_transfer
        contract._transfer("1:USR:B1", "1:USR:ESCROW", 1000, "GBP")

        self.assertEqual(calls["args"], ("1:USR:B1", "1:USR:ESCROW", 1000, "GBP"))


if __name__ == "__main__":
    unittest.main()
