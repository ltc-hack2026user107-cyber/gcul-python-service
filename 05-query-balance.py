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

"""05-query-balance.py: Query and print the current balance of a user account."""

import argparse
import sys

from google.cloud.universalledger.v1 import universalledger_pb2
from lloyds_ltc_reboot_2026 import helpers


def main() -> None:
    parser = argparse.ArgumentParser(description="Query the balance of a Universal Ledger account.")
    parser.add_argument("--account-id", type=str, required=True, help="Account ID to query.")
    parser.add_argument(
        "--project-id",
        type=str,
        default=helpers.DEFAULT_PROJECT_ID,
        help="Google Cloud project ID containing the Universal Ledger endpoint.",
    )
    parser.add_argument(
        "--region",
        type=str,
        default=helpers.DEFAULT_REGION,
        help="Ledger region to probe for an endpoint.",
    )
    args = parser.parse_args()

    try:
        stub, endpoint = helpers.get_stub_and_endpoint(project_id=args.project_id, region=args.region)
    except Exception as exc:
        print(f"[!] Failed to connect to Universal Ledger: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Selected endpoint: {endpoint}")
    req = universalledger_pb2.QueryAccountRequest(endpoint=endpoint, account_id=args.account_id)
    resp = stub.QueryAccount(req)

    if not resp.HasField("account"):
        print("[!] No account data returned.", file=sys.stderr)
        sys.exit(1)

    account = resp.account
    if account.HasField("user_details"):
        balance = account.user_details.balance.value
        print(f"Balance for {args.account_id}: {balance}")
    elif account.HasField("contract_details"):
        print(f"Account {args.account_id} is a contract account; no user balance is available.")
    else:
        print(f"Account {args.account_id} returned no user_details payload.")


if __name__ == "__main__":
    main()
