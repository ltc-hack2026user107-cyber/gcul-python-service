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

"""04a-grant-contract-roles.py: Grants smart contract creator and participant roles to a user account via the Account Manager."""

import argparse
import sys

from google.cloud.universalledger.v1 import (
    accounts_pb2,
    transactions_pb2,
    types_pb2,
)
from lloyds_ltc_reboot_2026 import helpers


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Grant ROLE_CONTRACT_CREATOR and ROLE_CONTRACT_PARTICIPANT to a Universal Ledger user account via Account Manager."
    )
    parser.add_argument(
        "--account-manager-id",
        type=str,
        required=True,
        help="Account ID of the Account Manager modifying this account.",
    )
    parser.add_argument(
        "--account-manager-kms-key",
        type=str,
        required=True,
        help="Google Cloud KMS key resource string of the Account Manager for signing.",
    )
    parser.add_argument(
        "--account-id",
        type=str,
        required=True,
        help="Account ID of the user account to grant contract roles to.",
    )
    args = parser.parse_args()

    print("[*] Connecting to Universal Ledger and selecting endpoint...")
    stub, endpoint = helpers.get_stub_and_endpoint()
    print(f"[*] Selected endpoint: {endpoint}")

    print(f"[*] Fetching sequence number for Account Manager ({args.account_manager_id})...")
    seq_num = helpers.get_sequence_number(stub, endpoint, args.account_manager_id)
    print(f"[*] Account Manager Sequence Number: {seq_num}")

    print(f"[*] Constructing AddRoles transaction for account {args.account_id}...")
    add_roles_tx = transactions_pb2.AddRoles(
        account_id=args.account_id,
        roles=[
            accounts_pb2.ROLE_CONTRACT_CREATOR,
            accounts_pb2.ROLE_CONTRACT_PARTICIPANT,
        ],
    )

    client_tx = types_pb2.ClientTransaction(
        sender_id=args.account_manager_id,
        sequence_number=seq_num,
        add_roles_transaction=add_roles_tx,
    )

    print(f"[*] Signing with Cloud KMS key ({args.account_manager_kms_key}) and submitting...")
    tx_digest, cert = helpers.sign_and_submit_with_kms(
        stub,
        endpoint,
        args.account_manager_id,
        args.account_manager_kms_key,
        client_tx,
    )
    print(f"[+] AddRoles transaction finalized! Digest: {tx_digest}")
    print("\n" + "=" * 60)
    print(f"[SUCCESS] Granted ROLE_CONTRACT_CREATOR and ROLE_CONTRACT_PARTICIPANT to {args.account_id}!")
    print("=" * 60)


if __name__ == "__main__":
    main()
