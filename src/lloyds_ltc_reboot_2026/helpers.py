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

"""Shared helpers for Universal Ledger transactions, KMS signing, local ECDSA signing, and key/account persistence."""

import glob
import hashlib
import json
import os
import random
import time
from typing import Any, Tuple

import google.auth
import google.auth.transport.grpc
import google.auth.transport.requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from google.cloud import kms
from google.cloud.universalledger.v1 import (
    accounts_pb2,
    common_pb2,
    query_pb2,
    transactions_pb2,
    types_pb2,
    universalledger_pb2,
    universalledger_pb2_grpc,
)

DEFAULT_PROJECT_ID = "ltc-hack2026-team22"
DEFAULT_REGION = "green-tartan1.workshop.ongcp.co"
DEFAULT_ENDPOINT_NAME = "gcul-pilot-testing"
KEYS_DIR = "keys"


def get_stub_and_endpoint(
    project_id: str = DEFAULT_PROJECT_ID,
    region: str = DEFAULT_REGION,
) -> Tuple[universalledger_pb2_grpc.UniversalLedgerStub, str]:
    """Authenticate via ADC, establish a gRPC secure channel, and discover the gcul-pilot-testing endpoint."""
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    req = google.auth.transport.requests.Request()
    target = "universalledger.googleapis.com:443"
    channel = google.auth.transport.grpc.secure_authorized_channel(
        credentials, req, target
    )
    stub = universalledger_pb2_grpc.UniversalLedgerStub(channel)

    # First check target region, fallback to common regions if needed
    regions_to_check = [region, "us-central1", "us-east1", "us-east4", "europe-west3"]
    endpoints_found = []
    for reg in regions_to_check:
        parent = f"projects/{project_id}/locations/{reg}"
        try:
            list_req = universalledger_pb2.ListEndpointsRequest(parent=parent)
            resp = stub.ListEndpoints(list_req)
            for ep in resp.endpoints:
                if "gcul-pilot-testing" in ep.name:
                    endpoints_found.append(ep.name)
        except Exception:
            continue
        if endpoints_found:
            break

    if not endpoints_found:
        raise RuntimeError(
            f"No active Universal Ledger endpoints matching 'gcul-pilot-testing' found in project {project_id}"
        )

    selected_endpoint = random.choice(endpoints_found)
    return stub, selected_endpoint


def get_sequence_number(
    stub: universalledger_pb2_grpc.UniversalLedgerStub, endpoint: str, account_id: str
) -> int:
    """Query the current sequence number (next expected sequence number) for an account."""
    req = universalledger_pb2.QueryAccountRequest(
        endpoint=endpoint, account_id=account_id
    )
    print("get_sequence_number ", account_id)
    resp = stub.QueryAccount(req)
    return int(resp.account.sequence_number)


import grpc

def poll_for_finalization(
    stub: universalledger_pb2_grpc.UniversalLedgerStub,
    endpoint: str,
    tx_digest_hex: str,
    timeout_seconds: float = 30.0,
) -> types_pb2.TransactionCertificate:
    """Poll QueryTransactionState until the transaction is finalized, returning its certificate or raising on error."""
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        try:
            req = universalledger_pb2.QueryTransactionStateRequest(
                endpoint=endpoint, transaction_digest_hex=tx_digest_hex
            )
            resp = stub.QueryTransactionState(req)
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.NOT_FOUND:
                time.sleep(1.0)
                continue
            raise
        for attempt in resp.transaction_attempts:
            status_name = types_pb2.TransactionAttempt.TransactionStatus.Name(
                attempt.status
            )
            if status_name == "FINALIZED":
                if not attempt.HasField("proof_of_inclusion"):
                    raise RuntimeError("Transaction finalized without proof_of_inclusion")
                cert = attempt.proof_of_inclusion.transaction_certificate
                if cert.HasField("transaction_effects"):
                    if cert.transaction_effects.status.code != 0:
                        raise RuntimeError(
                            f"Transaction failed at execution: {cert.transaction_effects.status.message}"
                        )
                return cert
            elif status_name in ("REJECTED", "FAILED"):
                raise RuntimeError(
                    f"Transaction attempt {status_name}: {attempt.error_message}"
                )
        time.sleep(1.0)
    raise TimeoutError(
        f"Timed out after {timeout_seconds}s waiting for transaction finalization ({tx_digest_hex})"
    )


def sign_and_submit_with_kms(
    stub: universalledger_pb2_grpc.UniversalLedgerStub,
    endpoint: str,
    sender_id: str,
    kms_key_string: str,
    client_tx: types_pb2.ClientTransaction,
) -> Tuple[str, types_pb2.TransactionCertificate]:
    """Serialize, SHA256 digest, sign via Cloud KMS, submit to ledger, and poll for finality."""
    serialized_tx = client_tx.SerializeToString()
    digest = hashlib.sha256(serialized_tx).digest()

    kms_client = kms.KeyManagementServiceClient()
    sign_resp = kms_client.asymmetric_sign(
        request={"name": kms_key_string, "digest": {"sha256": digest}}
    )

    signed_tx = types_pb2.SignedTransaction(
        serialized_client_transaction=serialized_tx,
        sender_signature=sign_resp.signature,
    )

    submit_req = universalledger_pb2.SubmitTransactionRequest(
        endpoint=endpoint,
        serialized_signed_transaction=signed_tx.SerializeToString(),
    )
    submit_resp = stub.SubmitTransaction(submit_req)
    tx_digest = submit_resp.transaction_digest_hex

    cert = poll_for_finalization(stub, endpoint, tx_digest)
    return tx_digest, cert


def sign_and_submit_with_local_key(
    stub: universalledger_pb2_grpc.UniversalLedgerStub,
    endpoint: str,
    sender_id: str,
    private_key_pem: bytes,
    client_tx: types_pb2.ClientTransaction,
) -> Tuple[str, types_pb2.TransactionCertificate]:
    """Serialize, sign locally using EC P-256 private key (DER signature), submit to ledger, and poll for finality."""
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(private_key, ec.EllipticCurvePrivateKey):
        raise TypeError("Expected an EllipticCurvePrivateKey for local signing.")

    serialized_tx = client_tx.SerializeToString()
    # ECDSA with SHA256 outputs DER signature directly as required by KEY_FORMAT_PEM_EC_P256_SHA256
    signature = private_key.sign(serialized_tx, ec.ECDSA(hashes.SHA256()))

    signed_tx = types_pb2.SignedTransaction(
        serialized_client_transaction=serialized_tx, sender_signature=signature
    )

    submit_req = universalledger_pb2.SubmitTransactionRequest(
        endpoint=endpoint,
        serialized_signed_transaction=signed_tx.SerializeToString(),
    )
    submit_resp = stub.SubmitTransaction(submit_req)
    tx_digest = submit_resp.transaction_digest_hex

    cert = poll_for_finalization(stub, endpoint, tx_digest)
    return tx_digest, cert


def generate_local_ec_keypair() -> Tuple[bytes, bytes]:
    """Generate a local P-256 (SECP256R1) EC keypair and return (private_key_pem, public_key_pem)."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def get_next_user_index() -> int:
    """Find the next sequential user index (1, 2, 3...) based on existing metadata in the keys directory."""
    os.makedirs(KEYS_DIR, exist_ok=True)
    existing_files = glob.glob(os.path.join(KEYS_DIR, "user_*.json"))
    max_index = 0
    for file_path in existing_files:
        try:
            filename = os.path.basename(file_path)
            parts = filename.replace("user_", "").replace(".json", "")
            if parts.isdigit():
                max_index = max(max_index, int(parts))
        except Exception:
            pass
    return max_index + 1


def save_user_account(
    index: int, account_id: str, private_pem: bytes, public_pem: bytes
) -> Tuple[str, str, str]:
    """Save user keypair and account metadata inside the local keys/ directory."""
    os.makedirs(KEYS_DIR, exist_ok=True)
    prefix = f"user_{index:03d}"
    priv_path = os.path.join(KEYS_DIR, f"{prefix}.pem")
    pub_path = os.path.join(KEYS_DIR, f"{prefix}_pub.pem")
    meta_path = os.path.join(KEYS_DIR, f"{prefix}.json")

    with open(priv_path, "wb") as f:
        f.write(private_pem)
    with open(pub_path, "wb") as f:
        f.write(public_pem)
    print("save_user_account ", account_id)
    metadata = {
        "index": index,
        "account_id": account_id,
        "private_key_path": priv_path,
        "public_key_path": pub_path,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    return priv_path, pub_path, meta_path


def load_user_account(index: int) -> Tuple[str, bytes, bytes]:
    """Load user account ID and keypair PEMs by user index."""
    prefix = f"user_{index:03d}"
    meta_path = os.path.join(KEYS_DIR, f"{prefix}.json")
    if not os.path.exists(meta_path):
        raise FileNotFoundError(f"Account metadata not found for user index {index} ({meta_path})")

    with open(meta_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    account_id = metadata["account_id"]
    priv_path = metadata["private_key_path"]
    pub_path = metadata["public_key_path"]
    print("load_user_account ", account_id)

    with open(priv_path, "rb") as f:
        private_pem = f.read()
    with open(pub_path, "rb") as f:
        public_pem = f.read()

    return account_id, private_pem, public_pem


def load_user_account_by_id(account_id: str) -> Tuple[str, bytes, bytes]:
    """Search keys/ directory for a user account matching account_id and return (account_id, private_pem, public_pem)."""
    if not os.path.exists(KEYS_DIR):
        raise FileNotFoundError(f"Directory {KEYS_DIR} does not exist.")
    print("load_user_account_by_id ", account_id)
    for filename in sorted(os.listdir(KEYS_DIR)):
        if filename.endswith(".json"):
            meta_path = os.path.join(KEYS_DIR, filename)
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
                if metadata.get("account_id") == account_id:
                    with open(metadata["private_key_path"], "rb") as pf:
                        private_pem = pf.read()
                    with open(metadata["public_key_path"], "rb") as pubf:
                        public_pem = pubf.read()
                    return account_id, private_pem, public_pem
            except Exception:
                continue

    raise FileNotFoundError(f"No local key metadata found in {KEYS_DIR}/ matching Account ID: {account_id}")
