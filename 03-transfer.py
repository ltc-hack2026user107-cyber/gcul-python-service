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

"""03-transfer.py: Transfers 100 currency units from user account 01 to secondary user account 02."""

import argparse
import sys

from google.cloud.universalledger.v1 import (
    common_pb2,
    transactions_pb2,
    types_pb2,
)
from lloyds_ltc_reboot_2026 import helpers


def main() -> None:
    parser = argparse.ArgumentParser(description="Transfer currency units between Universal Ledger accounts.")
    parser.add_argument(
        "--from-account-id",
        type=str,
        required=True,
        help="Account ID of the sender user account.",
    )
    parser.add_argument(
        "--to-account-id",
        type=str,
        required=True,
        help="Account ID of the receiver user account.",
    )
    parser.add_argument(
        "--amount",
        type=int,
        default=100,
        help="Amount of currency units to transfer (default: 100).",
    )
    args = parser.parse_args()

    try:
        sender_id, sender_private_pem, _ = helpers.load_user_account_by_id(args.from_account_id)
        print(f"[*] Loaded Sender Account ID: {sender_id}")
    except FileNotFoundError as e:
        print(f"[!] Error loading sender account key: {e}", file=sys.stderr)
        print("[i] Hint: Ensure the account metadata and private key exist in the keys/ directory.", file=sys.stderr)
        sys.exit(1)

    receiver_id = args.to_account_id
    print(f"[*] Target Receiver Account ID: {receiver_id}")

    print("[*] Connecting to Universal Ledger and selecting endpoint...")
    stub, endpoint = helpers.get_stub_and_endpoint()
    print(f"[*] Selected endpoint: {endpoint}")

    print(f"[*] Fetching sequence number for Sender Account ({sender_id})...")
    seq_num = helpers.get_sequence_number(stub, endpoint, sender_id)
    print(f"[*] Sender Sequence Number: {seq_num}")

    print(f"[*] Constructing Transfer transaction for {args.amount} currency units -> {receiver_id}...")
    transfer_tx = transactions_pb2.Transfer(
        amount=common_pb2.CurrencyValue(value=args.amount),
        beneficiary_id=receiver_id,
    )

    client_tx = types_pb2.ClientTransaction(
        sender_id=sender_id,
        sequence_number=seq_num,
        transfer_transaction=transfer_tx,
    )

    print(f"[*] Signing locally with {sender_id} EC P-256 private key and submitting...")
    tx_digest, cert = helpers.sign_and_submit_with_local_key(
        stub,
        endpoint,
        sender_id,
        sender_private_pem,
        client_tx,
    )
    print(f"[+] Transaction finalized! Digest: {tx_digest}")

    print("\n" + "=" * 60)
    print(f"[SUCCESS] Transferred {args.amount} currency units from {sender_id} to {receiver_id}!")
    print("=" * 60)


if __name__ == "__main__":
    main()
