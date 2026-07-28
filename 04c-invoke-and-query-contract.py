# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# NOTE: This code is strictly limited to non-production demo and example purposes.
# This repo does not contain code that is either (1) intended to be used in a
# customer's production environment beyond just demo purposes, or (2) proprietary
# or may be used to build a future Google product or solution, or (3) is subject
# to a customer expectation of managed support or a warranty.

"""04c-invoke-and-query-contract.py: Invokes a smart contract method and queries the contract state on the Universal Ledger."""

import argparse
import sys
import requests

from google.cloud.universalledger.v1 import (
    common_pb2,
    query_pb2,
    transactions_pb2,
    types_pb2,
    universalledger_pb2,
)
from lloyds_ltc_reboot_2026 import helpers


def parse_method_arg(raw_value: str) -> tuple[str, common_pb2.Value]:
    if "=" not in raw_value:
        raise ValueError(f"Method argument '{raw_value}' must be in key=value format")

    key, value_text = raw_value.split("=", 1)
    if not key:
        raise ValueError("Method argument key cannot be empty")

    if value_text.isdigit() or (value_text.startswith("-") and value_text[1:].isdigit()):
        return key, common_pb2.Value(int64_value=int(value_text))

    if value_text.lower() in {"true", "false"}:
        return key, common_pb2.Value(bool_value=value_text.lower() == "true")

    return key, common_pb2.Value(string_value=value_text)


def parse_method_arg_raw(raw_value: str) -> tuple[str, any]:
    if "=" not in raw_value:
        raise ValueError(f"Method argument '{raw_value}' must be in key=value format")

    key, value_text = raw_value.split("=", 1)
    if not key:
        raise ValueError("Method argument key cannot be empty")

    if value_text.isdigit() or (value_text.startswith("-") and value_text[1:].isdigit()):
        return key, int(value_text)

    if value_text.lower() in {"true", "false"}:
        return key, value_text.lower() == "true"

    return key, value_text


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Invoke a smart contract method using a participant user account and read contract state via QueryAccount."
    )
    parser.add_argument(
        "--participant-account-id",
        type=str,
        required=True,
        help="Account ID of the user account holding ROLE_CONTRACT_PARTICIPANT.",
    )
    parser.add_argument(
        "--contract-id",
        type=str,
        required=True,
        help="Account ID of the deployed smart contract to invoke and query.",
    )
    parser.add_argument(
        "--method-name",
        type=str,
        default="increment",
        help="Name of the contract method to invoke (default: increment).",
    )
    parser.add_argument(
        "--method-arg",
        action="append",
        default=[],
        help="Method argument in key=value form. Repeat for multiple arguments, e.g. --method-arg buyer=1:USR:... --method-arg amount=100",
    )
    parser.add_argument(
        "--webhook-url",
        type=str,
        default="http://localhost:8000/webhooks/contract/invoke",
        help="Webhook URL to trigger the contract invocation. Default: http://localhost:8000/webhooks/contract/invoke",
    )
    args = parser.parse_args()

    participant_id = args.participant_account_id
    contract_id = args.contract_id

    # Try triggering via webhook first
    webhook_success = False
    if args.webhook_url:
        print(f"[*] Attempting to trigger invocation via webhook: {args.webhook_url}...")
        method_args_raw = {}
        for raw_value in args.method_arg:
            key, val = parse_method_arg_raw(raw_value)
            method_args_raw[key] = val

        payload = {
            "contract_id": contract_id,
            "participant_account_id": participant_id,
            "method_name": args.method_name,
            "method_args": method_args_raw,
        }

        try:
            resp = requests.post(args.webhook_url, json=payload, timeout=10)
            if resp.status_code == 200:
                result = resp.json()
                print("[+] Triggered via webhook successfully!")
                print(f"    Transaction Digest: {result.get('transaction_digest')}")
                print(f"    Contract Fields: {result.get('contract_fields')}")
                webhook_success = True
            else:
                print(f"[!] Webhook returned status code {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"[!] Failed to call webhook: {e}")

    if not webhook_success:
        print("[*] Falling back to direct gRPC invocation on Universal Ledger...")
        try:
            _, participant_private_pem, _ = helpers.load_user_account_by_id(participant_id)
            print(f"[*] Loaded Contract Participant Account ID: {participant_id}")
        except FileNotFoundError as e:
            print(f"[!] Error loading participant account key: {e}", file=sys.stderr)
            print("[i] Hint: Ensure the account metadata and private key exist in the keys/ directory.", file=sys.stderr)
            sys.exit(1)

        print("[*] Connecting to Universal Ledger and selecting endpoint...")
        stub, endpoint = helpers.get_stub_and_endpoint()
        print(f"[*] Selected endpoint: {endpoint}")

        print(f"[*] Fetching sequence number for Participant Account ({participant_id})...")
        seq_num = helpers.get_sequence_number(stub, endpoint, participant_id)
        print(f"[*] Participant Sequence Number: {seq_num}")

        method_arguments = {}
        for raw_value in args.method_arg:
            key, value = parse_method_arg(raw_value)
            method_arguments[key] = value

        print(f"[*] Constructing InvokeContractMethod transaction for method '{args.method_name}' -> {contract_id}...")
        invoke_method_tx = transactions_pb2.InvokeContractMethod(
            contract_id=contract_id,
            method_name=args.method_name,
            method_arguments=method_arguments,
        )

        client_tx = types_pb2.ClientTransaction(
            sender_id=participant_id,
            sequence_number=seq_num,
            invoke_contract_method_transaction=invoke_method_tx,
        )

        print(f"[*] Signing InvokeContractMethod locally with {participant_id} private key and submitting...")
        tx_digest, cert = helpers.sign_and_submit_with_local_key(
            stub,
            endpoint,
            participant_id,
            participant_private_pem,
            client_tx,
        )
        print(f"[+] InvokeContractMethod finalized! Digest: {tx_digest}")
        print(f"Contract method '{args.method_name}' invoked successfully on {contract_id}.")

    # Finally, read and print the latest contract state directly from the ledger
    try:
        stub, endpoint = helpers.get_stub_and_endpoint()
        print(f"\n[*] Submitting QueryAccount request to read contract state ({contract_id})...")
        query_req = universalledger_pb2.QueryAccountRequest(endpoint=endpoint, account_id=contract_id)
        query_resp = stub.QueryAccount(query_req)

        print("\n" + "=" * 60)
        print(f"Account: {contract_id}")
        if query_resp.HasField("account") and query_resp.account.HasField("contract_details"):
            cd = query_resp.account.contract_details
            print("Contract account details:")
            print(f"  Owner: {cd.owner_id}")
            print("  Contract fields:")
            if cd.HasField("contract_fields") and cd.contract_fields.fields:
                for field_name, field_val in cd.contract_fields.fields.items():
                    if field_val.HasField("int64_value"):
                        val_str = f"int64_value:{field_val.int64_value}"
                    elif field_val.HasField("string_value"):
                        val_str = f"string_value:\"{field_val.string_value}\""
                    elif field_val.HasField("bool_value"):
                        val_str = f"bool_value:{field_val.bool_value}"
                    elif field_val.HasField("bytes_value"):
                        val_str = f"bytes_value:{field_val.bytes_value.hex()}"
                    else:
                        val_str = str(field_val).strip().replace("\n", " ")
                    print(f"    {field_name}: {val_str}")
            else:
                print("    None")

            print("\n  Balances:")
            if cd.currency_balances:
                for curr, bal in cd.currency_balances.items():
                    print(f"    {curr}: {bal.value}")
            else:
                print("    None")
        else:
            print("  (No contract_details returned in QueryAccount response)")
        print("=" * 60)
    except Exception as e:
        print(f"[!] Error querying contract state: {e}")


if __name__ == "__main__":
    main()

