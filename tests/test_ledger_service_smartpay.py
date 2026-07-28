import unittest
from unittest.mock import patch

from ledger_service import app, escrow_contract


class LedgerServiceSmartPayTests(unittest.TestCase):
    def setUp(self) -> None:
        app.config["TESTING"] = True
        self.client = app.test_client()
        escrow_contract.orders.clear()
        escrow_contract.initialize("1:USR:ESCROW", "1:USR:ADMIN")

    def test_create_webhook_creates_order(self) -> None:
        response = self.client.post(
            "/webhooks/orders/create",
            json={
                "order_id": "ORD-200",
                "buyer_account": "1:USR:B1",
                "seller_account": "1:USR:S1",
                "amount": 5000,
                "currency": "GBP",
                "description": "Demo",
                "caller": "1:USR:ADMIN",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "CREATED")
        self.assertEqual(payload["order_id"], "ORD-200")

    def test_deliver_and_refund_webhooks_update_status(self) -> None:
        create_resp = self.client.post(
            "/webhooks/orders/create",
            json={
                "order_id": "ORD-201",
                "buyer_account": "1:USR:B2",
                "seller_account": "1:USR:S2",
                "amount": 3000,
                "currency": "GBP",
                "description": "Refund flow",
                "caller": "1:USR:ADMIN",
            },
        )
        self.assertEqual(create_resp.status_code, 200)

        with patch.object(escrow_contract, "_transfer", return_value=None):
            fund_resp = self.client.post(
                "/webhooks/orders/ORD-201/fund",
                json={"caller": "1:USR:B2"},
            )
            self.assertEqual(fund_resp.status_code, 200)

            deliver_resp = self.client.post(
                "/webhooks/orders/ORD-201/deliver",
                json={"tracking_ref": "TRK-9", "caller": "1:USR:S2"},
            )
            self.assertEqual(deliver_resp.status_code, 200)
            self.assertEqual(deliver_resp.get_json()["status"], "SHIPPED")

            refund_resp = self.client.post(
                "/webhooks/orders/ORD-201/refund",
                json={"reason": "NON_DELIVERY", "caller": "1:USR:ADMIN"},
            )
            self.assertEqual(refund_resp.status_code, 200)
            self.assertEqual(refund_resp.get_json()["status"], "REFUNDED")

    @patch("ledger_service.submit_ledger_transfer")
    def test_fund_webhook_calls_transfer_submitter(self, mock_submit_transfer: object) -> None:
        self.client.post(
            "/webhooks/orders/create",
            json={
                "order_id": "ORD-202",
                "buyer_account": "1:USR:B3",
                "seller_account": "1:USR:S3",
                "amount": 4000,
                "caller": "1:USR:ADMIN",
            },
        )
        mock_submit_transfer.return_value = {"transaction_digest": "abc"}

        response = self.client.post(
            "/webhooks/orders/ORD-202/fund",
            json={"caller": "1:USR:B3"},
        )

        self.assertEqual(response.status_code, 200)
        mock_submit_transfer.assert_called_once_with(
            from_account="1:USR:B3",
            to_account="1:USR:ESCROW",
            amount=4000,
            currency="GBP",
        )

    @patch("ledger_service.helpers.sign_and_submit_with_local_key")
    @patch("ledger_service.helpers.load_user_account_by_id")
    @patch("ledger_service.helpers.get_stub_and_endpoint")
    @patch("ledger_service.helpers.get_sequence_number")
    def test_invoke_contract_webhook_success(
        self, mock_get_seq, mock_get_stub, mock_load_account, mock_sign_submit
    ) -> None:
        mock_load_account.return_value = ("1:USR:B3", b"dummy_pem", b"dummy_pub")
        mock_get_stub.return_value = ("mock_stub", "mock_endpoint")
        mock_get_seq.return_value = 5

        from unittest.mock import MagicMock
        mock_cert = MagicMock()
        mock_cert.HasField.return_value = False
        mock_sign_submit.return_value = ("mock_digest", mock_cert)

        response = self.client.post(
            "/webhooks/contract/invoke",
            json={
                "contract_id": "1:CTR:123",
                "participant_account_id": "1:USR:B3",
                "method_name": "create_order",
                "method_args": {"buyer": "1:USR:B3", "amount": 1000}
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["transaction_digest"], "mock_digest")


if __name__ == "__main__":
    unittest.main()
