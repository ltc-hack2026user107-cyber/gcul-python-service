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

"""01-create-account.py: Generates an EC keypair locally and creates a new user account via the Account Manager."""

import argparse
import sys

from google.cloud.universalledger.v1 import (
    accounts_pb2,
    transactions_pb2,
    types_pb2,
)
from lloyds_ltc_reboot_2026 import helpers


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a new Universal Ledger user account locally and via GCUL.")
    parser.add_argument(
        "--account-manager-id",
        type=str,
        required=True,
        help="Account ID of the Account Manager responsible for creating this account.",
    )
    parser.add_argument(
        "--account-manager-kms-key",
        type=str,
        required=True,
        help="Google Cloud KMS key resource string of the Account Manager for signing.",
    )
    args = parser.parse_args()

    print("[*] Determining next user account index...")
    index = helpers.get_next_user_index()
    print(f"[*] Generating P-256 EC keypair for user_{index:03d}...")
    private_pem, public_pem = helpers.generate_local_ec_keypair()

    print("[*] Connecting to Universal Ledger and selecting endpoint...")
    stub, endpoint = helpers.get_stub_and_endpoint()
    print(f"[*] Selected endpoint: {endpoint}")

    print(f"[*] Fetching sequence number for Account Manager ({args.account_manager_id})...")
    seq_num = helpers.get_sequence_number(stub, endpoint, args.account_manager_id)
    print(f"[*] Account Manager Sequence Number: {seq_num}")

    print("[*] Constructing CreateAccount transaction...")
    create_account_tx = transactions_pb2.CreateAccount(
        public_key=public_pem,
        key_format=transactions_pb2.KEY_FORMAT_PEM_EC_P256_SHA256,
        roles=[
            accounts_pb2.ROLE_PAYER,
            accounts_pb2.ROLE_RECEIVER,
            accounts_pb2.ROLE_CONTRACT_CREATOR,
            accounts_pb2.ROLE_CONTRACT_PARTICIPANT,
        ],
        account_status=accounts_pb2.ACCOUNT_STATUS_ACTIVE,
        account_comment=f"User account {index:03d}",
    )

    client_tx = types_pb2.ClientTransaction(
        sender_id=args.account_manager_id,
        sequence_number=seq_num,
        create_account_transaction=create_account_tx,
    )

    print(f"[*] Signing with Cloud KMS key ({args.account_manager_kms_key}) and submitting...")
    tx_digest, cert = helpers.sign_and_submit_with_kms(
        stub,
        endpoint,
        args.account_manager_id,
        args.account_manager_kms_key,
        client_tx,
    )
    print(f"[+] Transaction finalized! Digest: {tx_digest}")

    new_account_id = None
    for event in cert.events:
        if event.type in ("account_created", "transaction_output"):
            for attr in event.attributes:
                if attr.key in ("account_id", "value"):
                    new_account_id = attr.value
                    break
        if new_account_id:
            break

    if not new_account_id:
        print("[!] Error: Could not find 'account_id' in account_created event certificate.", file=sys.stderr)
        sys.exit(1)

    priv_path, pub_path, meta_path = helpers.save_user_account(
        index, new_account_id, private_pem, public_pem
    )
    print("\n" + "=" * 60)
    print(f"[SUCCESS] User Account #{index:03d} created successfully!")
    print(f"  Account ID: {new_account_id}")
    print(f"  Private Key: {priv_path}")
    print(f"  Public Key:  {pub_path}")
    print(f"  Metadata:    {meta_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
