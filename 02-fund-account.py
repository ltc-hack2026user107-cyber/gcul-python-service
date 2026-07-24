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

"""02-fund-account.py: Funds a user account with 100 currency units using the Token Manager key."""

import argparse
import sys

from google.cloud.universalledger.v1 import (
    common_pb2,
    transactions_pb2,
    types_pb2,
)
from lloyds_ltc_reboot_2026 import helpers


def main() -> None:
    parser = argparse.ArgumentParser(description="Fund a user account with 100 currency units via Token Manager.")
    parser.add_argument(
        "--token-manager-id",
        type=str,
        required=True,
        help="Account ID of the Token Manager responsible for minting tokens.",
    )
    parser.add_argument(
        "--token-manager-kms-key",
        type=str,
        required=True,
        help="Google Cloud KMS key resource string of the Token Manager for signing.",
    )
    parser.add_argument(
        "--account-id",
        type=str,
        required=True,
        help="Account ID of the user account to fund.",
    )
    parser.add_argument(
        "--amount",
        type=int,
        default=100,
        help="Amount of currency units to mint and transfer (default: 100).",
    )
    args = parser.parse_args()

    target_account_id = args.account_id
    print(f"[*] Target Account ID provided: {target_account_id}")

    print("[*] Connecting to Universal Ledger and selecting endpoint...")
    stub, endpoint = helpers.get_stub_and_endpoint()
    print(f"[*] Selected endpoint: {endpoint}")

    print(f"[*] Fetching sequence number for Token Manager ({args.token_manager_id})...")
    seq_num = helpers.get_sequence_number(stub, endpoint, args.token_manager_id)
    print(f"[*] Token Manager Sequence Number: {seq_num}")

    print(f"[*] Constructing Mint transaction for {args.amount} currency units -> {target_account_id}...")
    mint_tx = transactions_pb2.Mint(
        mint_amount=common_pb2.CurrencyValue(value=args.amount),
        beneficiary_id=target_account_id,
    )

    client_tx = types_pb2.ClientTransaction(
        sender_id=args.token_manager_id,
        sequence_number=seq_num,
        mint_transaction=mint_tx,
    )

    print(f"[*] Signing with Cloud KMS key ({args.token_manager_kms_key}) and submitting...")
    tx_digest, cert = helpers.sign_and_submit_with_kms(
        stub,
        endpoint,
        args.token_manager_id,
        args.token_manager_kms_key,
        client_tx,
    )
    print(f"[+] Transaction finalized! Digest: {tx_digest}")

    print("\n" + "=" * 60)
    print(f"[SUCCESS] Funded account {target_account_id} with {args.amount} currency units!")
    print("=" * 60)


if __name__ == "__main__":
    main()
