import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

if "gcul" not in sys.modules:
    gcul_mock = ModuleType("gcul")

    class Contract:
        pass

    gcul_mock.Contract = Contract  # type: ignore
    sys.modules["gcul"] = gcul_mock

# Ensure project root (containing ledger_service.py) is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import Flask, jsonify, request
from gculpy.smartpay_escrow import SmartPayEscrow
from google.cloud.universalledger.v1 import (
    common_pb2,
    transactions_pb2,
    types_pb2,
    universalledger_pb2,
)
from lloyds_ltc_reboot_2026 import helpers


def submit_ledger_transfer(
    *, from_account: str, to_account: str, amount: int, currency: str
) -> Any:
    """Submit a real GCUL transfer using the existing service transfer flow.

    If the account has no local signing key metadata, return a structured
    placeholder response so the webhook flow can still be tested locally.
    """
    try:
        sender_id, sender_private_pem, _ = helpers.load_user_account_by_id(
            from_account
        )
        stub, endpoint = helpers.get_stub_and_endpoint(
            project_id=os.getenv(
                "GCUL_PROJECT_ID", helpers.DEFAULT_PROJECT_ID
            ),
            region=os.getenv("GCUL_REGION", helpers.DEFAULT_REGION),
        )
        seq_num = helpers.get_sequence_number(stub, endpoint, sender_id)
        transfer_tx = transactions_pb2.Transfer(
            amount=common_pb2.CurrencyValue(value=amount),
            beneficiary_id=to_account,
        )
        client_tx = types_pb2.ClientTransaction(
            sender_id=sender_id,
            sequence_number=seq_num,
            transfer_transaction=transfer_tx,
        )
        tx_digest, _ = helpers.sign_and_submit_with_local_key(
            stub,
            endpoint,
            sender_id,
            sender_private_pem,
            client_tx,
        )
        return {
            "transaction_digest": tx_digest,
            "from_account": from_account,
            "to_account": to_account,
            "amount": amount,
            "currency": currency,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "placeholder",
            "error": str(exc),
            "from_account": from_account,
            "to_account": to_account,
            "amount": amount,
            "currency": currency,
        }


class LedgerSmartPayEscrow(SmartPayEscrow):
    """SmartPayEscrow wired to the real GCUL ledger for fund transfers."""

    def transfer(
        self, from_account: str, to_account: str, amount: int, currency: str
    ) -> None:
        submit_ledger_transfer(
            from_account=from_account,
            to_account=to_account,
            amount=amount,
            currency=currency,
        )


app = Flask(__name__)
escrow_contract = LedgerSmartPayEscrow()
escrow_contract.initialize(
    "1:USR:GBP:424BFwjobbiTBtdjqoPjiHRgmkF1YYvSiTLjCbdbQoz34"
)


@app.get("/health")
def health() -> Any:
    return jsonify({"status": "ok"})


@app.get("/balance")
def balance() -> Any:
    account_id = request.args.get("account_id", "").strip()
    if not account_id:
        return jsonify({"error": "account_id query parameter is required"}), 400

    try:
        stub, endpoint = helpers.get_stub_and_endpoint(
            project_id=os.getenv(
                "GCUL_PROJECT_ID", helpers.DEFAULT_PROJECT_ID
            ),
            region=os.getenv("GCUL_REGION", helpers.DEFAULT_REGION),
        )
        req = universalledger_pb2.QueryAccountRequest(
            endpoint=endpoint, account_id=account_id
        )
        resp = stub.QueryAccount(req)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500

    if not resp.HasField("account"):
        return jsonify({"error": "No account data returned"}), 404

    account = resp.account
    if account.HasField("user_details"):
        return jsonify({
            "account_id": account_id,
            "endpoint": endpoint,
            "balance": account.user_details.balance.value,
            "account_status": account.user_details.account_status,
        })

    if account.HasField("contract_details"):
        return jsonify({
            "account_id": account_id,
            "endpoint": endpoint,
            "balance": None,
            "message": (
                "Account is a contract account; no user balance is available"
            ),
        })

    return jsonify({
        "account_id": account_id,
        "endpoint": endpoint,
        "balance": None,
        "message": "Account returned no user_details payload",
    })


@app.post("/webhooks/contract/invoke")
def invoke_contract_webhook() -> Any:
    payload = request.get_json(silent=True) or {}
    contract_id = payload.get("contract_id")
    participant_account_id = payload.get("participant_account_id") or payload.get(
        "caller"
    )
    method_name = payload.get("method_name")
    method_args = payload.get("method_args") or {}

    if not contract_id or not participant_account_id or not method_name:
        return (
            jsonify({
                "error": (
                    "contract_id, participant_account_id, and method_name are"
                    " required"
                )
            }),
            400,
        )

    try:
        _, participant_private_pem, _ = helpers.load_user_account_by_id(
            participant_account_id
        )
        stub, endpoint = helpers.get_stub_and_endpoint(
            project_id=os.getenv(
                "GCUL_PROJECT_ID", helpers.DEFAULT_PROJECT_ID
            ),
            region=os.getenv("GCUL_REGION", helpers.DEFAULT_REGION),
        )
        seq_num = helpers.get_sequence_number(
            stub, endpoint, participant_account_id
        )

        method_arguments = {}
        for k, v in method_args.items():
            if isinstance(v, bool):
                method_arguments[k] = common_pb2.Value(bool_value=v)
            elif isinstance(v, int):
                method_arguments[k] = common_pb2.Value(int64_value=v)
            else:
                method_arguments[k] = common_pb2.Value(string_value=str(v))

        invoke_method_tx = transactions_pb2.InvokeContractMethod(
            contract_id=contract_id,
            method_name=method_name,
            method_arguments=method_arguments,
        )

        client_tx = types_pb2.ClientTransaction(
            sender_id=participant_account_id,
            sequence_number=seq_num,
            invoke_contract_method_transaction=invoke_method_tx,
        )

        # 1. Execute smart contract method invocation on-chain
        tx_digest, cert = helpers.sign_and_submit_with_local_key(
            stub,
            endpoint,
            participant_account_id,
            participant_private_pem,
            client_tx,
        )

        # 2. Perform actual signed ledger balance transfer using keys/
        transfer_digest = ""
        escrow_id = os.getenv("GCUL_ESCROW_ACCOUNT_ID", "1:USR:GBP:424BFwjobbiTBtdjqoPjiHRgmkF1YYvSiTLjCbdbQoz34").strip()
        amount = int(method_args.get("amount", 0))

        if amount > 0:
            try:
                if method_name == "create_order":
                    buyer_id = str(method_args.get("buyer", participant_account_id))
                    res = submit_ledger_transfer(from_account=buyer_id, to_account=escrow_id, amount=amount, currency="GBP")
                    transfer_digest = res.get("transaction_digest", "")
                elif method_name == "order_delivered":
                    seller_id = str(method_args.get("seller", ""))
                    if seller_id:
                        res = submit_ledger_transfer(from_account=escrow_id, to_account=seller_id, amount=amount, currency="GBP")
                        transfer_digest = res.get("transaction_digest", "")
                elif method_name == "order_failed":
                    buyer_id = str(method_args.get("buyer", ""))
                    if buyer_id:
                        res = submit_ledger_transfer(from_account=escrow_id, to_account=buyer_id, amount=amount, currency="GBP")
                        transfer_digest = res.get("transaction_digest", "")
            except Exception as transfer_err:
                app.logger.warning(f"[webhook] On-chain transfer helper error: {transfer_err}")

        contract_fields = {}
        try:
            query_req = universalledger_pb2.QueryAccountRequest(
                endpoint=endpoint, account_id=contract_id
            )
            query_resp = stub.QueryAccount(query_req)
            if query_resp.HasField("account") and query_resp.account.HasField(
                "contract_details"
            ):
                cd = query_resp.account.contract_details
                if cd.HasField("contract_fields") and cd.contract_fields.fields:
                    for (
                        field_name,
                        field_val,
                    ) in cd.contract_fields.fields.items():
                        if field_val.HasField("int64_value"):
                            contract_fields[field_name] = field_val.int64_value
                        elif field_val.HasField("string_value"):
                            contract_fields[field_name] = (
                                field_val.string_value
                            )
                        elif field_val.HasField("bool_value"):
                            contract_fields[field_name] = field_val.bool_value
                        elif field_val.HasField("bytes_value"):
                            contract_fields[field_name] = (
                                field_val.bytes_value.hex()
                            )
        except Exception:  # noqa: BLE001
            pass

        return jsonify({
            "success": True,
            "transaction_digest": tx_digest,
            "transfer_digest": transfer_digest,
            "contract_id": contract_id,
            "method_name": method_name,
            "contract_fields": contract_fields,
        })

    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@app.get("/accounts")
def list_accounts() -> Any:
    try:
        stub, endpoint = helpers.get_stub_and_endpoint(
            project_id=os.getenv(
                "GCUL_PROJECT_ID", helpers.DEFAULT_PROJECT_ID
            ),
            region=os.getenv("GCUL_REGION", helpers.DEFAULT_REGION),
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500

    accounts = []
    keys_dir = os.path.join(os.getcwd(), helpers.KEYS_DIR)
    if os.path.exists(keys_dir):
        for filename in sorted(os.listdir(keys_dir)):
            if not filename.endswith(".json"):
                continue
            meta_path = os.path.join(keys_dir, filename)
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
                account_id = metadata.get("account_id")
                if not account_id:
                    continue
                req = universalledger_pb2.QueryAccountRequest(
                    endpoint=endpoint, account_id=account_id
                )
                resp = stub.QueryAccount(req)
                balance = None
                if resp.HasField("account") and resp.account.HasField(
                    "user_details"
                ):
                    balance = resp.account.user_details.balance.value
                accounts.append({
                    "account_id": account_id,
                    "balance": balance,
                })
            except Exception as exc:
                accounts.append({
                    "account_id": None,
                    "balance": None,
                    "error": str(exc),
                })

    return jsonify({"endpoint": endpoint, "accounts": accounts})


def initialize_contract_on_chain() -> None:
    """Call initialize() on the deployed on-chain contract at startup.

    Safe to run on every restart — just overwrites platform_escrow with the same value.
    Skips silently if env vars are missing or signing key is not found.
    """
    contract_id = os.getenv("GCUL_CONTRACT_ID", "").strip()
    escrow_account_id = os.getenv("GCUL_ESCROW_ACCOUNT_ID", "").strip()

    if not contract_id or not escrow_account_id:
        print(
            "[ledger_service] Skipping on-chain initialize — GCUL_CONTRACT_ID /"
            " GCUL_ESCROW_ACCOUNT_ID / GCUL_PARTICIPANT_ACCOUNT_ID not set in"
            " .env"
        )
        return

    try:
        _, participant_private_pem, _ = helpers.load_user_account_by_id(
            escrow_account_id
        )
        stub, endpoint = helpers.get_stub_and_endpoint(
            project_id=os.getenv(
                "GCUL_PROJECT_ID", helpers.DEFAULT_PROJECT_ID
            ),
            region=os.getenv("GCUL_REGION", helpers.DEFAULT_REGION),
        )
        seq_num = helpers.get_sequence_number(stub, endpoint, escrow_account_id)
        invoke_tx = transactions_pb2.InvokeContractMethod(
            contract_id=contract_id,
            method_name="initialize",
            method_arguments={
                "platform_escrow_account": common_pb2.Value(
                    string_value=escrow_account_id
                ),
            },
        )
        client_tx = types_pb2.ClientTransaction(
            sender_id=escrow_account_id,
            sequence_number=seq_num,
            invoke_contract_method_transaction=invoke_tx,
        )
        tx_digest, _ = helpers.sign_and_submit_with_local_key(
            stub,
            endpoint,
            escrow_account_id,
            participant_private_pem,
            client_tx,
        )
        print(
            "[ledger_service] ✓ Contract initialized on-chain."
            f" platform_escrow={escrow_account_id}  tx={tx_digest}"
        )
    except Exception as exc:  # noqa: BLE001
        print(
            "[ledger_service] ✗ Contract initialize failed (may already be set"
            f" or key missing): {exc}"
        )


if __name__ == "__main__":
    initialize_contract_on_chain()
    app.run(
        host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False
    )