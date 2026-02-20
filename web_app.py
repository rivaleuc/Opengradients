#!/usr/bin/env python3
"""Repo Oracle Studio Web App.

A polished web front-end over repo_oracle where AI does the core work.
"""

from __future__ import annotations

import os
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Dict

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from web3 import Web3

from repo_oracle import (
    DEFAULT_MODEL_PRIORITY,
    OracleClient,
    init_oracle_client,
    load_private_key,
    resolve_repo_source,
    run_ask,
    run_review,
)


APP_ROOT = Path(__file__).resolve().parent
STATIC_DIR = APP_ROOT / "web_static"
DEFAULT_ROOT = APP_ROOT.parent.resolve()

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
CORS(app)

_ORACLE_CACHE: Dict[str, OracleClient] = {}
_ORACLE_LOCK = threading.Lock()
_USED_FEE_TX: set[str] = set()
_WALLET_RUN_CREDITS: Dict[str, int] = {}
_FEE_STATE_LOCK = threading.Lock()

BASE_SEPOLIA_CHAIN_ID_HEX = "0x14a34"
BASE_SEPOLIA_CHAIN_ID_INT = int(BASE_SEPOLIA_CHAIN_ID_HEX, 16)
BASE_SEPOLIA_RPC_URL = os.getenv("BASE_SEPOLIA_RPC_URL", "https://sepolia.base.org")
OPG_TOKEN_ADDRESS = Web3.to_checksum_address(
    os.getenv("OPG_TOKEN_ADDRESS", "0x240b09731D96979f50B2C649C9CE10FcF9C7987F")
)
OPG_FEE_AMOUNT = Decimal(os.getenv("OPG_FEE_AMOUNT", "0.0001"))
OPG_FEE_WEI = int(OPG_FEE_AMOUNT * Decimal(10**18))
RUNS_PER_FEE_TX = max(1, int(os.getenv("RUNS_PER_FEE_TX", "10")))
FEE_TX_LOOKUP_TIMEOUT_SEC = float(os.getenv("FEE_TX_LOOKUP_TIMEOUT_SEC", "45"))
FEE_TX_LOOKUP_POLL_SEC = float(os.getenv("FEE_TX_LOOKUP_POLL_SEC", "1.5"))


def resolve_fee_receiver() -> str:
    raw = os.getenv("OPG_FEE_RECEIVER", "").strip()
    if raw:
        return Web3.to_checksum_address(raw)

    key = load_private_key()
    if key:
        acct = Web3().eth.account.from_key(key)
        return Web3.to_checksum_address(acct.address)
    return ""


OPG_FEE_RECEIVER = resolve_fee_receiver()
W3_BASE = Web3(Web3.HTTPProvider(BASE_SEPOLIA_RPC_URL))
TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").hex().lower()


def get_oracle(model: str | None = None) -> OracleClient:
    key = (model or "").strip() or "__default__"
    with _ORACLE_LOCK:
        cached = _ORACLE_CACHE.get(key)
        if cached is not None:
            return cached
        oracle = init_oracle_client(force_model=model)
        _ORACLE_CACHE[key] = oracle
        return oracle


def parse_root(root_raw: str | None) -> Path:
    return resolve_repo_source(root_raw, default_root=DEFAULT_ROOT)


def _topic_addr(topic_hex: str) -> str:
    return Web3.to_checksum_address("0x" + topic_hex[-40:])


def _hex_to_int(raw: object) -> int:
    if isinstance(raw, bytes):
        return int.from_bytes(raw, byteorder="big")
    if hasattr(raw, "hex"):
        return int(raw.hex(), 16)
    if isinstance(raw, str):
        return int(raw, 16)
    return int(raw or 0)


def _wait_for_transaction(tx_hash: str):
    deadline = time.time() + FEE_TX_LOOKUP_TIMEOUT_SEC
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            tx = W3_BASE.eth.get_transaction(tx_hash)
            if tx:
                return tx
        except Exception as exc:
            last_exc = exc
        time.sleep(FEE_TX_LOOKUP_POLL_SEC)

    detail = f"last error: {last_exc}" if last_exc else "not propagated to RPC yet"
    raise ValueError(f"fee transaction not found after waiting {FEE_TX_LOOKUP_TIMEOUT_SEC:.0f}s ({detail})")


def _wait_for_receipt(tx_hash: str):
    deadline = time.time() + FEE_TX_LOOKUP_TIMEOUT_SEC
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            receipt = W3_BASE.eth.get_transaction_receipt(tx_hash)
            if receipt:
                return receipt
        except Exception as exc:
            last_exc = exc
        time.sleep(FEE_TX_LOOKUP_POLL_SEC)

    detail = f"last error: {last_exc}" if last_exc else "receipt unavailable"
    raise ValueError(f"fee receipt not found after waiting {FEE_TX_LOOKUP_TIMEOUT_SEC:.0f}s ({detail})")


def _require_fee_payment(payload: dict) -> tuple[str, str, int, bool]:
    wallet_address_raw = str(payload.get("wallet_address") or "").strip()
    fee_tx_hash_raw = str(payload.get("fee_tx_hash") or "").strip()
    if not wallet_address_raw:
        raise ValueError("wallet_address is required")
    if not OPG_FEE_RECEIVER:
        raise RuntimeError(
            "Fee configuration missing on server. "
            "Set OPG_FEE_RECEIVER or OG_PRIVATE_KEY/OPENGRADIENT_PRIVATE_KEY."
        )

    wallet_address = Web3.to_checksum_address(wallet_address_raw)

    with _FEE_STATE_LOCK:
        existing = _WALLET_RUN_CREDITS.get(wallet_address, 0)
        if existing > 0:
            remaining = existing - 1
            _WALLET_RUN_CREDITS[wallet_address] = remaining
            return wallet_address, "", remaining, False

    if not fee_tx_hash_raw:
        raise ValueError("No remaining runs. fee_tx_hash is required")
    if not fee_tx_hash_raw.startswith("0x") or len(fee_tx_hash_raw) != 66:
        raise ValueError("fee_tx_hash must be a valid transaction hash")
    fee_tx_hash = fee_tx_hash_raw.lower()

    with _FEE_STATE_LOCK:
        if fee_tx_hash in _USED_FEE_TX:
            raise ValueError("fee_tx_hash was already used")

    tx = _wait_for_transaction(fee_tx_hash)

    tx_from = Web3.to_checksum_address(tx["from"])
    if tx_from != wallet_address:
        raise ValueError("fee tx sender does not match wallet_address")
    if tx.get("chainId") and int(tx["chainId"]) != BASE_SEPOLIA_CHAIN_ID_INT:
        raise ValueError("fee tx is not on Base Sepolia")

    receipt = _wait_for_receipt(fee_tx_hash)
    if int(receipt.get("status", 0)) != 1:
        raise ValueError("fee transaction failed")

    paid_ok = False
    for log in receipt.get("logs", []):
        if Web3.to_checksum_address(log["address"]) != OPG_TOKEN_ADDRESS:
            continue
        topics = log.get("topics", [])
        if len(topics) < 3:
            continue
        t0 = topics[0].hex().lower()
        if t0 != TRANSFER_TOPIC:
            continue
        from_addr = _topic_addr(topics[1].hex())
        to_addr = _topic_addr(topics[2].hex())
        value_wei = _hex_to_int(log.get("data", "0x0"))
        if from_addr == wallet_address and to_addr == OPG_FEE_RECEIVER and value_wei >= OPG_FEE_WEI:
            paid_ok = True
            break

    if not paid_ok:
        raise ValueError("fee payment not found in transaction logs")

    with _FEE_STATE_LOCK:
        _USED_FEE_TX.add(fee_tx_hash)
        remaining = RUNS_PER_FEE_TX - 1
        _WALLET_RUN_CREDITS[wallet_address] = _WALLET_RUN_CREDITS.get(wallet_address, 0) + remaining
    return wallet_address, fee_tx_hash, remaining, True


@app.route("/")
def index():
    return send_from_directory(str(STATIC_DIR), "index.html")


@app.route("/api/ping")
def api_ping():
    models = [name for name, _ in DEFAULT_MODEL_PRIORITY]
    return jsonify(
        {
            "ok": True,
            "default_root": str(DEFAULT_ROOT),
            "source_hint": "Use local path or GitHub link (https://github.com/owner/repo)",
            "models": models,
            "status": "ready",
            "fee_required": True,
            "fee_token": OPG_TOKEN_ADDRESS,
            "fee_amount_opg": str(OPG_FEE_AMOUNT),
            "fee_receiver": OPG_FEE_RECEIVER,
            "fee_chain_id": BASE_SEPOLIA_CHAIN_ID_HEX,
            "runs_per_fee_tx": RUNS_PER_FEE_TX,
        }
    )


@app.route("/api/ask", methods=["POST"])
def api_ask():
    data = request.get_json(force=True, silent=True) or {}
    question = str(data.get("question") or "").strip()
    model = str(data.get("model") or "").strip() or None
    max_files = int(data.get("max_files") or 8)
    max_files = max(1, min(max_files, 20))

    if not question:
        return jsonify({"ok": False, "error": "question is required"}), 400

    try:
        wallet_address, fee_tx_hash, remaining_runs, paid_now = _require_fee_payment(data)
        root = parse_root(data.get("root"))
        oracle = get_oracle(model)
        result = run_ask(oracle=oracle, root=root, question=question, max_files=max_files)
        return jsonify(
            {
                "ok": True,
                "mode": "ask",
                "model": result.model_name,
                "root": str(root),
                "answer": result.answer,
                "planner_focus": result.planner_focus,
                "planner_files": result.planner_files,
                "wallet_address": wallet_address,
                "fee_tx_hash": fee_tx_hash,
                "remaining_runs": remaining_runs,
                "paid_now": paid_now,
            }
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/review", methods=["POST"])
def api_review():
    data = request.get_json(force=True, silent=True) or {}
    target = str(data.get("target") or "")
    model = str(data.get("model") or "").strip() or None

    try:
        wallet_address, fee_tx_hash, remaining_runs, paid_now = _require_fee_payment(data)
        root = parse_root(data.get("root"))
        oracle = get_oracle(model)
        result = run_review(oracle=oracle, root=root, target=target)
        return jsonify(
            {
                "ok": True,
                "mode": "review",
                "model": result.model_name,
                "root": str(root),
                "diff_empty": result.diff_empty,
                "answer": result.answer,
                "target": target,
                "wallet_address": wallet_address,
                "fee_tx_hash": fee_tx_hash,
                "remaining_runs": remaining_runs,
                "paid_now": paid_now,
            }
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8090"))
    host = os.getenv("HOST", "127.0.0.1")
    print("=" * 68)
    print("Repo Oracle Studio")
    print(f"Open: http://{host}:{port}")
    print(f"Default root: {DEFAULT_ROOT}")
    print("=" * 68)
    app.run(host=host, port=port, debug=False)
