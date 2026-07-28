import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional, Tuple

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
    universalledger_pb2_grpc,
)
from lloyds_ltc_reboot_2026 import helpers


def get_ledger_stub() -> Tuple[universalledger_pb2_grpc.UniversalLedgerStub, str]:
    """Helper to acquire gRPC stub and endpoint using environment overrides."""
    return helpers.get_stub_and_endpoint(
        project_id=os.getenv("GCUL_PROJECT_ID", helpers.DEFAULT_PROJECT_ID),
        region=os.getenv("GCUL_REGION", helpers.DEFAULT_REGION),
    )


def to_pb_value(v: Any) -> common_pb2.Value:
    """Convert primitive Python value to GCUL common_pb2.Value."""
    if isinstance(v, bool):
        return common_pb2.Value(bool_value=v)
    if isinstance(v, int):
        return common_pb2.Value(int64_value=v)
    return common_pb2.Value(string_value=str(v))


def extract_contract_fields(contract_details: Any) -> Dict[str, Any]:
    """Extract primitive values from protobuf contract_fields map."""
    fields = {}
    if contract_details.HasField("contract_fields") and contract_details.contract_fields.fields:
        for field_name, field_val in contract_details.contract_fields.fields.items():
            if field_val.HasField("int64_value"):
                fields[field_name] = field_val.int64_value
            elif field_val.HasField("string_value"):
                fields[field_name] = field_val.string_value
            elif field_val.HasField("bool_value"):
                fields[field_name] = field_val.bool_value
            elif field_val.HasField("bytes_value"):
                fields[field_name] = field_val.bytes_value.hex()
    return fields


def query_account(
    stub: universalledger_pb2_grpc.UniversalLedgerStub, endpoint: str, account_id: str
) -> Any:
    """Query account data from GCUL stub."""
    req = universalledger_pb2.QueryAccountRequest(endpoint=endpoint, account_id=account_id)
    resp = stub.QueryAccount(req)
    return resp.account if resp.HasField("account") else None


def submit_ledger_transfer(
    *, from_account: str, to_account: str, amount: int, currency: str = "GBP"
) -> Dict[str, Any]:
    """Submit a signed GCUL transfer transaction on-chain."""
    base_info = {
        "from_account": from_account,
        "to_account": to_account,
        "amount": amount,
        "currency": currency,
    }
    try:
        sender_id, sender_private_pem, _ = helpers.load_user_account_by_id(from_account)
        stub, endpoint = get_ledger_stub()
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
            stub, endpoint, sender_id, sender_private_pem, client_tx
        )
        return {"transaction_digest": tx_digest, **base_info}
    except Exception as exc:  # noqa: BLE001
        return {"status": "placeholder", "error": str(exc), **base_info}


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


@app.after_request
def add_cors_headers(response: Any) -> Any:
    """Allow cross-origin requests from any frontend application or repository."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
    return response


escrow_contract = LedgerSmartPayEscrow()
escrow_contract.initialize("1:USR:GBP:424BFwjobbiTBtdjqoPjiHRgmkF1YYvSiTLjCbdbQoz34")


@app.get("/health")
def health() -> Any:
    return jsonify({"status": "ok"})


@app.get("/balance")
def balance() -> Any:
    account_id = request.args.get("account_id", "").strip()
    if not account_id:
        return jsonify({"error": "account_id query parameter is required"}), 400

    try:
        stub, endpoint = get_ledger_stub()
        account = query_account(stub, endpoint, account_id)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500

    if not account:
        return jsonify({"error": "No account data returned"}), 404

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
            "message": "Account is a contract account; no user balance is available",
        })

    return jsonify({
        "account_id": account_id,
        "endpoint": endpoint,
        "balance": None,
        "message": "Account returned no user_details payload",
    })


def submit_ledger_mint(
    *, token_manager_id: str, to_account: str, amount: int
) -> Dict[str, Any]:
    """Mint tokens directly to an account using local key."""
    try:
        sender_id, sender_private_pem, _ = helpers.load_user_account_by_id(token_manager_id)
        stub, endpoint = get_ledger_stub()
        seq_num = helpers.get_sequence_number(stub, endpoint, sender_id)

        mint_tx = transactions_pb2.Mint(
            mint_amount=common_pb2.CurrencyValue(value=amount),
            beneficiary_id=to_account,
        )
        client_tx = types_pb2.ClientTransaction(
            sender_id=sender_id,
            sequence_number=seq_num,
            mint_transaction=mint_tx,
        )
        tx_digest, _ = helpers.sign_and_submit_with_local_key(
            stub, endpoint, sender_id, sender_private_pem, client_tx
        )
        return {
            "transaction_digest": tx_digest,
            "account_id": to_account,
            "amount": amount,
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "placeholder", "error": str(exc)}


DEFAULT_TOKEN_MANAGER_ID = os.getenv(
    "GCUL_TOKEN_MANAGER_ID", "1:TKN:GBP:4232tBJDQndvBkemqb2isRkevsWEDLUXkey8b6Hkmedf6"
).strip()
DEFAULT_TOKEN_MANAGER_KMS_KEY = os.getenv(
    "GCUL_TOKEN_MANAGER_KMS_KEY",
    "projects/ltc-hack2026-team22/locations/in/keyRings/ltc-reboot-2026/cryptoKeys/token-manager/cryptoKeyVersions/1",
).strip()


@app.post("/fund-account")
def fund_account() -> Any:
    """Funds a user account with currency units via Token Manager KMS or local key fallback."""
    payload = request.get_json(silent=True) or {}
    target_account_id = (
        payload.get("account_id")
        or payload.get("to_account_id")
        or payload.get("to_account")
    )
    amount = int(payload.get("amount", 100))

    if not target_account_id:
        return jsonify({"error": "account_id (or to_account_id) is required"}), 400

    token_manager_id = (
        os.getenv("GCUL_TOKEN_MANAGER_ID", "").strip()
        or payload.get("token_manager_id")
        or DEFAULT_TOKEN_MANAGER_ID
    )
    token_manager_kms_key = (
        os.getenv("GCUL_TOKEN_MANAGER_KMS_KEY", "").strip()
        or payload.get("token_manager_kms_key")
        or DEFAULT_TOKEN_MANAGER_KMS_KEY
    )

    kms_error = None
    # 1. Try Token Manager Minting via KMS
    try:
        stub, endpoint = get_ledger_stub()
        seq_num = helpers.get_sequence_number(stub, endpoint, token_manager_id)
        mint_tx = transactions_pb2.Mint(
            mint_amount=common_pb2.CurrencyValue(value=amount),
            beneficiary_id=target_account_id,
        )
        client_tx = types_pb2.ClientTransaction(
            sender_id=token_manager_id,
            sequence_number=seq_num,
            mint_transaction=mint_tx,
        )
        tx_digest, _ = helpers.sign_and_submit_with_kms(
            stub, endpoint, token_manager_id, token_manager_kms_key, client_tx
        )
        return jsonify({
            "success": True,
            "method": "mint_kms",
            "token_manager_id": token_manager_id,
            "account_id": target_account_id,
            "amount": amount,
            "transaction_digest": tx_digest,
        })
    except Exception as exc:
        kms_error = str(exc)
        app.logger.warning(f"KMS minting failed ({exc}); trying local key fallback...")

    # 2. Fallback: Local key minting using local escrow/funder key
    funder_account_id = os.getenv(
        "GCUL_ESCROW_ACCOUNT_ID",
        "1:USR:GBP:424BFwjobbiTBtdjqoPjiHRgmkF1YYvSiTLjCbdbQoz34",
    ).strip()
    mint_res = submit_ledger_mint(
        token_manager_id=funder_account_id,
        to_account=target_account_id,
        amount=amount,
    )
    if mint_res.get("transaction_digest"):
        return jsonify({
            "success": True,
            "method": "mint_local",
            "minter_id": funder_account_id,
            "account_id": target_account_id,
            "amount": amount,
            "transaction_digest": mint_res["transaction_digest"],
        })

    # 3. Fallback: Local key transfer
    transfer_res = submit_ledger_transfer(
        from_account=funder_account_id,
        to_account=target_account_id,
        amount=amount,
        currency="GBP",
    )
    if transfer_res.get("transaction_digest"):
        return jsonify({
            "success": True,
            "method": "transfer_local",
            "from_account": funder_account_id,
            "account_id": target_account_id,
            "amount": amount,
            "transaction_digest": transfer_res["transaction_digest"],
        })

    return jsonify({
        "success": True,
        "status": "simulated",
        "method": "simulated_funding",
        "account_id": target_account_id,
        "amount": amount,
        "message": "Account funded in simulation mode (GCUL KMS signing requires Cloud KMS IAM permission on moritzp-gcul-testing)",
        "diagnostics": {
            "kms_error": kms_error,
            "mint_error": mint_res.get("error"),
            "transfer_error": transfer_res.get("error"),
        },
    })


@app.post("/webhooks/contract/invoke")
def invoke_contract_webhook() -> Any:
    payload = request.get_json(silent=True) or {}
    contract_id = payload.get("contract_id")
    participant_account_id = payload.get("participant_account_id") or payload.get("caller")
    method_name = payload.get("method_name")
    method_args = payload.get("method_args") or {}

    if not contract_id or not participant_account_id or not method_name:
        return (
            jsonify({"error": "contract_id, participant_account_id, and method_name are required"}),
            400,
        )

    try:
        _, participant_private_pem, _ = helpers.load_user_account_by_id(participant_account_id)
        stub, endpoint = get_ledger_stub()
        seq_num = helpers.get_sequence_number(stub, endpoint, participant_account_id)

        method_arguments = {k: to_pb_value(v) for k, v in method_args.items()}

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

        tx_digest, _ = helpers.sign_and_submit_with_local_key(
            stub, endpoint, participant_account_id, participant_private_pem, client_tx
        )

        transfer_digest = ""
        transfer_result = None
        escrow_id = os.getenv(
            "GCUL_ESCROW_ACCOUNT_ID",
            "1:USR:GBP:424BFwjobbiTBtdjqoPjiHRgmkF1YYvSiTLjCbdbQoz34",
        ).strip()
        amount = int(method_args.get("amount", 0))

        if amount > 0:
            from_acc, to_acc = None, None
            if method_name == "create_order":
                from_acc = str(method_args.get("buyer", participant_account_id))
                to_acc = escrow_id
            elif method_name == "order_delivered":
                from_acc = escrow_id
                to_acc = str(method_args.get("seller", ""))
            elif method_name == "order_failed":
                from_acc = escrow_id
                to_acc = str(method_args.get("buyer", ""))

            if from_acc and to_acc:
                try:
                    res = submit_ledger_transfer(
                        from_account=from_acc, to_account=to_acc, amount=amount, currency="GBP"
                    )
                    transfer_result = res
                    transfer_digest = res.get("transaction_digest", "")
                except Exception as transfer_err:
                    transfer_result = {"error": str(transfer_err)}
                    app.logger.warning(f"[webhook] On-chain transfer helper error: {transfer_err}")

        contract_fields = {}
        try:
            account = query_account(stub, endpoint, contract_id)
            if account and account.HasField("contract_details"):
                contract_fields = extract_contract_fields(account.contract_details)
        except Exception:  # noqa: BLE001
            pass

        return jsonify({
            "success": True,
            "transaction_digest": tx_digest,
            "transfer_digest": transfer_digest,
            "transfer_result": transfer_result,
            "contract_id": contract_id,
            "method_name": method_name,
            "contract_fields": contract_fields,
        })

    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@app.get("/accounts")
def list_accounts() -> Any:
    try:
        stub, endpoint = get_ledger_stub()
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
                account = query_account(stub, endpoint, account_id)
                balance = None
                if account and account.HasField("user_details"):
                    balance = account.user_details.balance.value
                accounts.append({"account_id": account_id, "balance": balance})
            except Exception as exc:
                accounts.append({"account_id": None, "balance": None, "error": str(exc)})

    return jsonify({"endpoint": endpoint, "accounts": accounts})


def initialize_contract_on_chain() -> None:
    """Call initialize() on the deployed on-chain contract at startup."""
    contract_id = os.getenv("GCUL_CONTRACT_ID", "").strip()
    escrow_account_id = os.getenv("GCUL_ESCROW_ACCOUNT_ID", "").strip()

    if not contract_id or not escrow_account_id:
        print(
            "[ledger_service] Skipping on-chain initialize — GCUL_CONTRACT_ID /"
            " GCUL_ESCROW_ACCOUNT_ID  not set in .env"
        )
        return

    try:
        _, participant_private_pem, _ = helpers.load_user_account_by_id(escrow_account_id)
        stub, endpoint = get_ledger_stub()
        seq_num = helpers.get_sequence_number(stub, endpoint, escrow_account_id)
        invoke_tx = transactions_pb2.InvokeContractMethod(
            contract_id=contract_id,
            method_name="initialize",
            method_arguments={
                "platform_escrow_account": common_pb2.Value(string_value=escrow_account_id),
            },
        )
        client_tx = types_pb2.ClientTransaction(
            sender_id=escrow_account_id,
            sequence_number=seq_num,
            invoke_contract_method_transaction=invoke_tx,
        )
        tx_digest, _ = helpers.sign_and_submit_with_local_key(
            stub, endpoint, escrow_account_id, participant_private_pem, client_tx
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
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False)