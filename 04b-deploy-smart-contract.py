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

"""04b-deploy-smart-contract.py: Deploys a compiled binary smart contract to the Universal Ledger."""

import argparse
import os
import sys

from google.cloud.universalledger.v1 import (
    transactions_pb2,
    types_pb2,
)
from lloyds_ltc_reboot_2026 import helpers


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy a compiled smart contract bytecode file (.bin) using a contract creator user account."
    )
    parser.add_argument(
        "--creator-account-id",
        type=str,
        required=True,
        help="Account ID of the user account holding ROLE_CONTRACT_CREATOR.",
    )
    parser.add_argument(
        "--contract-path",
        type=str,
        default="gculpy/counter.bin",
        help="Path to the compiled binary contract bytecode (.bin file). Default: gculpy/counter.bin",
    )
    args = parser.parse_args()

    creator_id = args.creator_account_id

    try:
        _, creator_private_pem, _ = helpers.load_user_account_by_id(creator_id)
        print(f"[*] Loaded Contract Creator Account ID: {creator_id}")
    except FileNotFoundError as e:
        print(f"[!] Error loading creator account key: {e}", file=sys.stderr)
        print("[i] Hint: Ensure the account metadata and private key exist in the keys/ directory.", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.contract_path):
        print(f"[!] Error: Compiled contract file '{args.contract_path}' not found.", file=sys.stderr)
        print("[i] Hint: Compile your contract source first using gculpyc (see README.md).", file=sys.stderr)
        sys.exit(1)

    with open(args.contract_path, "rb") as f:
        contract_bytes = f.read()

    print(f"[*] Loaded contract bytecode ({len(contract_bytes)} bytes) from '{args.contract_path}'.")

    print("[*] Connecting to Universal Ledger and selecting endpoint...")
    stub, endpoint = helpers.get_stub_and_endpoint()
    print(f"[*] Selected endpoint: {endpoint}")

    print(f"[*] Fetching sequence number for Creator Account ({creator_id})...")
    seq_num = helpers.get_sequence_number(stub, endpoint, creator_id)
    print(f"[*] Creator Sequence Number: {seq_num}")

    print("[*] Constructing CreateContract transaction...")
    create_contract_tx = transactions_pb2.CreateContract(
        contract_bytes=contract_bytes,
        contract_comment="counter-contract",
    )

    client_tx = types_pb2.ClientTransaction(
        sender_id=creator_id,
        sequence_number=seq_num,
        create_contract_transaction=create_contract_tx,
    )

    print(f"[*] Signing CreateContract locally with {creator_id} private key and submitting...")
    tx_digest, cert = helpers.sign_and_submit_with_local_key(
        stub,
        endpoint,
        creator_id,
        creator_private_pem,
        client_tx,
    )
    print(f"[+] CreateContract finalized! Digest: {tx_digest}")

    contract_id = None
    for event in cert.events:
        if event.type in ("contract_created", "transaction_output"):
            for attr in event.attributes:
                if attr.key in ("contract_id", "value"):
                    contract_id = attr.value
                    break
        if contract_id:
            break

    if not contract_id:
        print("[!] Error: Could not find 'contract_id' in finalized transaction events.", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 60)
    print(f"Contract created: {contract_id}")
    print("=" * 60)


if __name__ == "__main__":
    main()
